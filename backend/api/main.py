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
import html
import ipaddress
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
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

    Stores a ``deque`` of Unix timestamps per client IP.  Expired entries are
    pruned on every check for the active bucket, and a periodic whole-table
    sweep reclaims one-off buckets (an IP that made a single request and never
    returned otherwise stays in memory forever).  A hard bucket cap bounds the
    damage from an address-spoofing flood while preserving availability for
    the requests that triggered the cap.  Thread-safe via an internal lock.
    """

    # Sweep the whole table every N checks.  A balance: frequent enough to
    # reclaim one-off IPs quickly, rare enough that it is not O(n) per request.
    _SWEEP_EVERY = 512
    # Hard cap on tracked IPs.  In-process limiting is best-effort; when an
    # attacker can rotate addresses freely, memory stays bounded and legitimate
    # clients get a fresh bucket after the rare clear.
    _MAX_BUCKETS = 100_000

    def __init__(self, max_requests: int, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._checks = 0

    def _prune_bucket(self, ip: str, now: float) -> None:
        """Drop timestamps that have left the sliding window."""
        bucket = self._buckets.get(ip)
        if bucket is None:
            return
        cutoff = now - self.window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if not bucket:
            del self._buckets[ip]

    def _sweep(self, now: float) -> None:
        for ip in list(self._buckets):
            self._prune_bucket(ip, now)

    def allow(self, ip: str) -> bool:
        with self._lock:
            now = time.time()
            self._checks += 1
            if self._checks % self._SWEEP_EVERY == 0:
                self._sweep(now)

            self._prune_bucket(ip, now)
            bucket = self._buckets.get(ip)
            if bucket is None:
                if len(self._buckets) >= self._MAX_BUCKETS:
                    # A rotated-address flood: bound memory.  This is a rare
                    # availability reset, not an unbounded allocation.
                    self._buckets.clear()
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
    take the **last syntactically valid IP** in the chain, falling back to
    ``request.client.host`` when the header is missing or malformed.  Rejecting
    malformed values matters: ``unknown`` or an empty tail would otherwise put
    every request in one shared attacker-controlled bucket.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        for part in reversed(forwarded.split(",")):
            candidate = part.strip().strip('"')
            if not candidate:
                continue
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                continue
            return candidate
    host = request.client.host if request.client else "0.0.0.0"
    return host


# 10 req / minute for CV downloads.
_cv_limiter = _RateLimiter(max_requests=10, window_seconds=60.0)

# 120 req / minute for GraphQL endpoints (query + explorer).
_api_limiter = _RateLimiter(max_requests=120, window_seconds=60.0)

# Health checks get their own bucket so a monitoring loop can never consume a
# real user's GraphQL/CV quota, and vice versa.
_health_limiter = _RateLimiter(max_requests=240, window_seconds=60.0)

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,128}$")


def _cv_access_log(ip: str, public_id: str) -> None:
    """Log a SHA-256-hashed IP + person id for audit."""
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:12]
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    logger.info("cv_download hash=%s person=%s at=%s", ip_hash, public_id, ts)


def _rate_limit_exceeded() -> JSONResponse:
    return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)


app = FastAPI(
    title="El Monte research atlas API",
    version="0.3.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.exception_handler(OperationalError)
async def _database_unavailable(request: Request, exc: OperationalError) -> JSONResponse:
    """Structured 503 for any route that reaches the database directly."""
    logger.warning("database unavailable on %s: %s", request.url.path, exc)
    return JSONResponse(
        {"detail": "database unavailable"}, status_code=503
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
    allow_headers=[
        "Content-Type",
        "X-Request-ID",
        "Apollo-Require-Preflight",
        "Accept",
    ],
)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Hardening headers + request correlation id on every response.

    `setdefault` so we never clobber explicit headers from routes or CORS.
    A strict CSP is deliberately omitted: the only HTML served is the GraphiQL
    explorer, which relies on inline scripts.
    """
    request_id = request.headers.get("X-Request-ID", "")
    if not _REQUEST_ID_PATTERN.fullmatch(request_id):
        request_id = uuid.uuid4().hex
    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


@app.get("/api/health")
def health(
    request: Request, db: Session = Depends(db_session)
) -> dict[str, str]:
    """Liveness + database reachability.

    This is intentionally tiny (one ``SELECT 1``) so render.com and uptime
    monitors can hit it often.  It uses a separate limiter from the GraphQL
    endpoint.
    """
    if not _health_limiter.allow(_client_ip(request)):
        return _rate_limit_exceeded()
    try:
        db.execute(text("SELECT 1"))
    except OperationalError:
        raise HTTPException(status_code=503, detail="database unavailable") from None
    return JSONResponse(
        {"status": "ok"},
        headers={"Cache-Control": "no-store"},
    )


_PRIVACY_PATH = Path(__file__).resolve().parents[2] / "PRIVACY.md"


# 60 req / minute for the privacy policy page.
_privacy_limiter = _RateLimiter(max_requests=10, window_seconds=60.0)


_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _render_markdown_inline(value: str) -> str:
    """Render the small, trusted Markdown subset used by PRIVACY.md."""
    escaped = html.escape(value)

    def link_repl(match: re.Match[str]) -> str:
        label = match.group(1)
        url = match.group(2).strip()
        if url.startswith(("https://", "http://")):
            return (
                f'<a href="{html.escape(url, quote=True)}" target="_blank" '
                f'rel="noreferrer">{label}</a>'
            )
        return label

    escaped = _MARKDOWN_LINK_RE.sub(link_repl, escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped


def _render_markdown(content: str) -> str:
    """Convert PRIVACY.md into a small, dependency-free HTML document."""
    lines = content.splitlines()
    parts: list[str] = [
        "<!doctype html><html><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>Privacy Policy — El Monte Research Atlas</title>",
        "<style>",
        (
            "body{max-width:760px;margin:0 auto;padding:32px 20px 64px;"
            "font:16px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
            "color:#1f2937;background:#fff}h1{font-size:2rem;line-height:1.2}"
        ),
        "h2{font-size:1.35rem;margin-top:2rem}h3{font-size:1.05rem;margin-top:1.5rem}",
        "p{margin:0.75rem 0}ul,ol{padding-left:1.4rem}li{margin:0.35rem 0}",
        "a{color:#4338ca;text-decoration:none}a:hover{text-decoration:underline}",
        "pre,code{font:13px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}",
        "pre{background:#f3f4f6;padding:14px;border-radius:8px;overflow:auto}",
        "table{border-collapse:collapse;margin:1rem 0;width:100%}",
        "th,td{border:1px solid #e5e7eb;padding:8px 10px;text-align:left}",
        "th{background:#f9fafb}",
        "</style></head><body>",
    ]

    in_code = False
    in_list = False
    table: list[list[str]] = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            parts.append("</ul>")
            in_list = False

    def flush_table() -> None:
        nonlocal table
        if not table:
            return
        rows = "".join(
            "<tr>"
            + "".join(
                (
                    f"<th>{_render_markdown_inline(cell)}</th>"
                    if idx == 0
                    else f"<td>{_render_markdown_inline(cell)}</td>"
                )
                for idx, cell in enumerate(row)
            )
            + "</tr>"
            for row in table
        )
        parts.append(f"<table>{rows}</table>")
        table = []

    for raw in lines:
        line = raw.rstrip()
        if line.strip().startswith("```"):
            close_list()
            flush_table()
            if in_code:
                parts.append("</code></pre>")
                in_code = False
            else:
                parts.append("<pre><code>")
                in_code = True
            continue
        if in_code:
            parts.append(html.escape(line))
            continue

        stripped = line.strip()
        if not stripped:
            close_list()
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            close_list()
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            table.append(cells)
            continue

        flush_table()
        if stripped.startswith("# "):
            close_list()
            parts.append(f"<h1>{_render_markdown_inline(stripped[2:])}</h1>")
            continue
        if stripped.startswith("## "):
            close_list()
            parts.append(f"<h2>{_render_markdown_inline(stripped[3:])}</h2>")
            continue
        if stripped.startswith("### "):
            close_list()
            parts.append(f"<h3>{_render_markdown_inline(stripped[4:])}</h3>")
            continue
        if stripped.startswith("- "):
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{_render_markdown_inline(stripped[2:])}</li>")
            continue

        close_list()
        parts.append(f"<p>{_render_markdown_inline(stripped)}</p>")

    close_list()
    flush_table()
    if in_code:
        parts.append("</code></pre>")
    parts.append("</body></html>")
    return "\n".join(parts)



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
    # updates propagate within the hour rather than the day. Serve rendered
    # HTML rather than raw Markdown text so browser visitors get a real page.
    return HTMLResponse(
        _render_markdown(content),
        headers={"Cache-Control": "public, max-age=3600"},
    )


def _snapshot_roots() -> tuple[Path, ...]:
    """Allowed filesystem roots for CV snapshots.

    Relative paths are rooted at the repo.  Absolute snapshot paths are only
    accepted when an operator explicitly configured ``ELMONTE_DATA_ROOT``
    (useful when raw snapshots live on a mounted volume); anything else
    outside the repository is treated as missing.
    """
    roots = [_REPO_ROOT]
    configured = os.environ.get("ELMONTE_DATA_ROOT", "").strip()
    if configured:
        roots.append(Path(configured).resolve())
    return tuple(roots)


def _snapshot_file_or_404(raw: str) -> Path:
    """Resolve a stored snapshot path, refusing traversal escapes.

    Stored paths are either relative to the repo root or, when
    ``ELMONTE_DATA_ROOT`` is configured, under that explicit data root (see
    scripts/backfill/common.py).  ``..`` segments and URL-ish strings are
    rejected outright; anything else must exist as a regular file.
    """
    candidate = Path(raw)
    if ".." in candidate.parts or "://" in raw:
        raise HTTPException(status_code=404, detail="cv file missing")
    if not candidate.is_absolute():
        candidate = _REPO_ROOT / candidate
    resolved = candidate.resolve()
    allowed = any(resolved.is_relative_to(root) for root in _snapshot_roots())
    if not allowed or not resolved.exists() or not resolved.is_file():
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
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        headers={"Cache-Control": "private, no-store"},
    )


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
    :class:`json.JSONDecodeError` for malformed JSON and
    :class:`UnicodeDecodeError` for non-UTF-8 bodies.
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
    except (json.JSONDecodeError, UnicodeDecodeError):
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
    response = JSONResponse(result, status_code=200 if success else 400)
    # GraphQL results contain person-level data; never let a shared cache keep
    # them.  Browsers still cache per-tab in memory, but no intermediary may.
    response.headers["Cache-Control"] = "no-store"
    return response
