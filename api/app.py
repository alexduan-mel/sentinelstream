"""FastAPI application: JSON API under /api plus the built web UI at /."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from api import auth, filings, tasks
from api.db import Connect, default_connect

DEFAULT_WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


class SinglePageApp(StaticFiles):
    """Static files, with ``index.html`` for client-side routes such as ``/tasks``.

    Only extensionless paths outside ``/api`` fall back, so a missing asset or API route stays a 404.
    """

    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            first, _, _ = path.partition("/")
            if exc.status_code != 404 or first == "api" or "." in Path(path).name:
                raise
            return await super().get_response("index.html", scope)


class Health(BaseModel):
    status: str
    version: str


def create_app(
    *,
    version: str | None = None,
    web_dist: Path | None = None,
    connect: Connect | None = None,
    login_attempts: auth.LoginAttempts | None = None,
) -> FastAPI:
    """Build the app.

    ``version`` defaults to ``SENTINELSTREAM_VERSION`` (the image's commit SHA, set at build time),
    so ``/healthz`` shows which build is running. ``web_dist`` is served at ``/`` when it exists.
    ``connect`` opens a database connection (tests pass their own).

    Every ``/api`` route needs a signed-in user except login and logout.
    """
    resolved_version = version or os.getenv("SENTINELSTREAM_VERSION") or "dev"
    resolved_web_dist = web_dist or Path(os.getenv("WEB_DIST_DIR", str(DEFAULT_WEB_DIST)))

    app = FastAPI(title="SentinelStream", version=resolved_version)
    app.state.connect = connect or default_connect
    app.state.login_attempts = login_attempts or auth.LoginAttempts()

    @app.get("/healthz", response_model=Health)
    def healthz() -> Health:
        return Health(status="ok", version=resolved_version)

    app.include_router(auth.public_router)
    signed_in = [Depends(auth.current_user)]
    app.include_router(auth.session_router, dependencies=signed_in)
    app.include_router(tasks.router, dependencies=signed_in)
    app.include_router(filings.router, dependencies=signed_in)

    # Mounted last so API routes always win over static files.
    if resolved_web_dist.is_dir():
        app.mount("/", SinglePageApp(directory=resolved_web_dist, html=True), name="web")

    return app


app = create_app()
