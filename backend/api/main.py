"""FastAPI application entry point.

Run in development:

    .venv/bin/uvicorn api.main:app --reload --port 8000

The Vite dev server proxies /api/* to this process (see vite.config.ts).

Endpoints:

    POST /api/graphql              — GraphQL query endpoint (Ariadne)
    GET  /api/graphql              — GraphiQL explorer (dev only)
    GET  /api/health               — liveness check
    GET  /api/people/{id}/cv       — cached CV snapshot for a person
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy.exc import OperationalError
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from api.deps import db_session
from api.graphql.app import execute, render_explorer
from api.id_codec import decode

logger = logging.getLogger("elmonte.api")


# ---------------------------------------------------------------------------
# in-process rate limiter (no external dependencies)
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Token-bucket-ish counter: allow *max_requests* each sliding *window_seconds*.

    Stores a ``deque`` of Unix timestamps per client IP.  Prunes expired entries
    on every check and deletes empty buckets, so the shared dict never grows
    unbounded.  Thread-safe via an internal lock.

    """

    def __init__(self, max_requests: int, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, ip: str) -> bool:
        with self._lock:
            now = time.time()
            bucket = self._buckets.get(ip)
            if bucket is None:
                self._buckets[ip] = deque([now])
                return True

            # Prune timestamps outside the window.
            cutoff = now - self.window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()

            # Delete stale empty buckets to prevent unbounded memory growth.
            if not bucket:
                del self._buckets[ip]
                self._buckets[ip] = deque([now])
                return True

            if len(bucket) < self.max_requests:
                bucket.append(now)
                return True

            return False


def _client_ip(request: Request) -> str:
    """Best-effort real client IP behind a reverse proxy (Render, nginx).

    The proxy *appends* the connecting IP at the end of ``X-Forwarded-For``;
    earlier entries are client-controlled and may be spoofed.  We therefore
    take the **last** value in the chain (or ``request.client.host`` when
    no proxy header is present).
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    host = request.client.host if request.client else "0.0.0.0"
    return host


# 10 req / minute for CV downloads.
_cv_limiter = _RateLimiter(max_requests=10, window_seconds=60.0)

# 120 req / minute for GraphQL endpoints (query + explorer).
_api_limiter = _RateLimiter(max_requests=120, window_seconds=60.0)


def _cv_access_log(ip: str, public_id: str) -> None:
    """Log a SHA-256-hashed IP + person id for audit."""
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:12]
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    logger.info("cv_download hash=%s person=%s at=%s", ip_hash, public_id, ts)


def _rate_limit_exceeded() -> JSONResponse:
    return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)


app = FastAPI(
    title="El Monte research atlas API",
    version="0.2.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

def _cors_origins() -> list[str]:
    """Dev localhost plus any comma-separated origins in CORS_ORIGINS (e.g.
    GitHub Pages: https://user.github.io,https://user.github.io/elmonte)."""
    origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
    extra = os.environ.get("CORS_ORIGINS", "").strip()
    if extra:
        for origin in extra.split(","):
            origin = origin.strip()
            if origin == "*":
                # A wildcard CORS origin is almost always a misconfiguration;
                # reject it instead of silently opening the API to every site.
                logger.warning("CORS_ORIGINS contains '*' — wildcard origin ignored")
                continue
            if origin:
                origins.append(origin)
    return origins


# Vite dev proxies /api → :8000 (same-origin). Production frontends on other
# hosts (GitHub Pages, etc.) need their origin listed in CORS_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Hardening headers on every response (nosniff, clickjacking, referrer).

    `setdefault` so we never clobber explicit headers from routes or CORS.
    A strict CSP is deliberately omitted: the only HTML served is the GraphiQL
    explorer, which relies on inline scripts.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


@app.get("/api/health")
def health(request: Request) -> dict[str, str]:
    if not _api_limiter.allow(_client_ip(request)):
        return _rate_limit_exceeded()
    return {"status": "ok"}


_PRIVACY_PATH = Path(__file__).resolve().parents[2] / "PRIVACY.md"


# 60 req / minute for the privacy policy page.
_privacy_limiter = _RateLimiter(max_requests=10, window_seconds=60.0)


@app.get("/api/privacy")
def privacy(request: Request) -> Response:
    # ----- rate limiting ---------------------------------------------------
    if not _privacy_limiter.allow(_client_ip(request)):
        return _rate_limit_exceeded()
    # -----------------------------------------------------------------------
    if not _PRIVACY_PATH.exists():
        raise HTTPException(status_code=404, detail="privacy policy not found")
    content = _PRIVACY_PATH.read_text(encoding="utf-8")
    # Static page, but keep the TTL short so contact/deletion-instruction
    # updates propagate within the hour rather than the day.
    return Response(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )


def _snapshot_file_or_404(raw: str) -> Path:
    """Resolve a stored snapshot path, refusing traversal escapes.

    Stored paths are either absolute or relative to the repo root (see
    scripts/backfill/common.py).  ``..`` segments and URL-ish strings are
    rejected outright; anything else must exist as a regular file.
    """
    candidate = Path(raw)
    if ".." in candidate.parts or "://" in raw:
        raise HTTPException(status_code=404, detail="cv file missing")
    if not candidate.is_absolute():
        candidate = _REPO_ROOT / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(_REPO_ROOT.resolve()):
        raise HTTPException(status_code=404, detail="cv file missing")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="cv file missing")
    return resolved


_REPO_ROOT = Path(__file__).resolve().parents[2]


@app.get("/api/people/{public_id}/cv")
def person_cv(public_id: str, request: Request, db: Session = Depends(db_session)) -> FileResponse:
    # ----- rate limiting ---------------------------------------------------
    ip = _client_ip(request)
    if not _cv_limiter.allow(ip):
        return _rate_limit_exceeded()
    _cv_access_log(ip, public_id)
    # -----------------------------------------------------------------------

    try:
        kind, row_id = decode(public_id)
    except ValueError:
        # Malformed public id — a client error, not a server fault.
        raise HTTPException(status_code=400, detail="malformed id") from None
    if kind != "person":
        raise HTTPException(status_code=404, detail="person not found")

    row = db.execute(
        text("SELECT cv_snapshot_id FROM people WHERE id = :i"),
        {"i": row_id},
    ).mappings().first()
    if row is None or row["cv_snapshot_id"] is None:
        raise HTTPException(status_code=404, detail="cv not available")

    snap = db.execute(
        text("SELECT local_path FROM source_snapshots WHERE id = :i"),
        {"i": int(row["cv_snapshot_id"])},
    ).mappings().first()
    if snap is None or not snap["local_path"]:
        raise HTTPException(status_code=404, detail="cv snapshot missing")

    path = _snapshot_file_or_404(snap["local_path"])

    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else "text/html"
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.get("/api/graphql", response_class=HTMLResponse)
def graphql_explorer(request: Request) -> HTMLResponse:
    if os.environ.get("ELMONTE_ENV") == "production":
        # The explorer exposes the full SDL + an interactive console; keep it
        # local-only in production (the GraphQL schema itself stays public).
        raise HTTPException(status_code=404, detail="not found")
    if not _api_limiter.allow(_client_ip(request)):
        return _rate_limit_exceeded()
    return HTMLResponse(render_explorer())


# GraphQL queries are small; cap the body so a misbehaving client can't make
# the server buffer gigabytes (M4).
MAX_BODY_BYTES = 64 * 1024


async def _read_json_body(request: Request) -> dict | None:
    """Read + parse a JSON body, capped at :data:`MAX_BODY_BYTES`.

    Returns ``None`` when the cap is exceeded (caller answers 413); raises
    :class:`json.JSONDecodeError` for malformed JSON.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            return None
    body = b"".join(chunks)
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


@app.post("/api/graphql")
async def graphql_endpoint(
    request: Request, db: Session = Depends(db_session)
) -> Response:
    # ----- rate limiting ---------------------------------------------------
    if not _api_limiter.allow(_client_ip(request)):
        return _rate_limit_exceeded()
    # -----------------------------------------------------------------------

    # ClientDisconnect fires when the browser drops the socket before the
    # body is fully read — happens routinely when Apollo cancels an
    # obsolete query, when StrictMode double-mounts a component, or when
    # HMR replaces a component with a pending fetch. There's no client
    # left to send a response to, so we return an empty 499 (nginx's
    # convention for "client closed request") and skip resolver work.
    try:
        payload = await _read_json_body(request)
    except ClientDisconnect:
        return Response(status_code=499)
    except json.JSONDecodeError:
        return JSONResponse(
            {"detail": "request body is not valid JSON"}, status_code=400
        )
    if payload is None:
        return JSONResponse(
            {"detail": "request body too large"}, status_code=413
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            {"detail": "request body must be a JSON object"}, status_code=400
        )

    # Resolvers are sync SQLAlchemy — run off the event loop so slow searches
    # don't block health checks and other in-flight requests.
    try:
        success, result = await run_in_threadpool(execute, payload, db)
    except OperationalError:
        # Database unreachable (Neon paused, network blip): a structured 503
        # beats a bare 500 with a stack trace.
        return JSONResponse(
            {"errors": [{"message": "database unavailable"}]}, status_code=503
        )
    return JSONResponse(result, status_code=200 if success else 400)
