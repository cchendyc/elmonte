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

import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from api.deps import db_session
from api.graphql.app import execute, render_explorer
from api.id_codec import decode


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
        origins.extend(o.strip() for o in extra.split(",") if o.strip())
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


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/people/{public_id}/cv")
def person_cv(public_id: str, db: Session = Depends(db_session)) -> FileResponse:
    kind, row_id = decode(public_id)
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

    path = Path(snap["local_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="cv file missing")

    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else "text/html"
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.get("/api/graphql", response_class=HTMLResponse)
def graphql_explorer() -> HTMLResponse:
    return HTMLResponse(render_explorer())


@app.post("/api/graphql")
async def graphql_endpoint(
    request: Request, db: Session = Depends(db_session)
) -> Response:
    # ClientDisconnect fires when the browser drops the socket before the
    # body is fully read — happens routinely when Apollo cancels an
    # obsolete query, when StrictMode double-mounts a component, or when
    # HMR replaces a component with a pending fetch. There's no client
    # left to send a response to, so we return an empty 499 (nginx's
    # convention for "client closed request") and skip resolver work.
    try:
        payload = await request.json()
    except ClientDisconnect:
        return Response(status_code=499)

    # Resolvers are sync SQLAlchemy — run off the event loop so slow searches
    # don't block health checks and other in-flight requests.
    success, result = await run_in_threadpool(execute, payload, db)
    return JSONResponse(result, status_code=200 if success else 400)
