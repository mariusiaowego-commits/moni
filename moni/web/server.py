"""FastAPI application factory and server bootstrap."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from moni.web.exceptions import register_exception_handlers

# Allowed CORS origin — no wildcards
ALLOWED_ORIGIN = "http://127.0.0.1:8900"

# Host/port the server listens on (NOT 0.0.0.0)
HOST = "127.0.0.1"
PORT = 8900

# Frontend static files (T6 SPA) — copied from wt/t6-web-fe into the moni
# project at v1.0 launch, so the runtime path is relative to the package.
# Override at runtime with the MONI_STATIC_DIR env var if needed.
_DEFAULT_STATIC_DIR = str(Path(__file__).parent / "static")
STATIC_DIR = Path(os.environ.get("MONI_STATIC_DIR", _DEFAULT_STATIC_DIR))


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: startup and shutdown hooks."""
    # Startup: e.g. connect to DB, warm caches
    yield
    # Shutdown: e.g. close DB pool, flush queues


def create_app() -> FastAPI:
    """Build and return the ASGI FastAPI application.

    Includes:
    - CORS middleware restricted to ``http://127.0.0.1:8900`` (no wildcards)
    - Exception handlers for all ``WebBaseException`` subclasses
    - All routers from ``moni.web.routes`` sub-package (registered by child tasks)
    """
    app = FastAPI(
        title="moni",
        version="1.0.0",
        lifespan=_lifespan,
    )

    # CORS — allow ONLY the configured frontend origin
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[ALLOWED_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register web-layer exception handlers
    register_exception_handlers(app)

    # Import and mount routers from the routes sub-package.
    # Routes are registered by child tasks; we import lazily so this file
    # doesn't break before routes/ exists.
    try:
        from moni.web import routes  # noqa: F401

        for router in getattr(routes, "_routers", []):
            app.include_router(router)
    except ImportError:
        # routes/ not yet created — next task adds them
        pass

    # Minimal health route — activates CORS middleware so headers are present
    # even before other routes are registered.
    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # ── Static files (T6 SPA, sibling worktree) ────────────────────────────
    # Mount at /static/* so /index.html, /js/*.js, /css/*.css resolve.
    # T6 frontend lives in wt/t6-web-fe; we hardcode the absolute path with
    # an env override (MONI_STATIC_DIR) for portability.
    if STATIC_DIR.exists() and STATIC_DIR.is_dir():
        app.mount(
            "/static",
            StaticFiles(directory=str(STATIC_DIR), check_dir=False),
            name="static",
        )

        # SPA fallback: any non-/api/* path that doesn't match a static file
        # → return index.html so client-side router takes over.
        index_html = STATIC_DIR / "index.html"

        @app.get("/", include_in_schema=False)
        async def serve_root() -> FileResponse:
            return FileResponse(str(index_html))

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str, request: Request) -> Response:
            # Don't shadow /api/* or /docs or /openapi.json — let FastAPI handle
            if full_path.startswith(("api/", "docs", "openapi.json", "redoc")):
                return Response(status_code=404)
            # If the path maps to an actual file under STATIC_DIR, serve it
            candidate = STATIC_DIR / full_path
            if candidate.is_file():
                return FileResponse(str(candidate))
            # Otherwise → SPA index.html (client-side router)
            return FileResponse(str(index_html))
    else:
        # v1.0 fall-back: log a clear warning if static dir missing.
        import logging
        logging.getLogger(__name__).warning(
            "MONI_STATIC_DIR does not exist or is not a directory: %s. "
            "Web UI will be unavailable; only the JSON API will respond.",
            STATIC_DIR,
        )

    return app


# ---------------------------------------------------------------------------
# Direct run entry point (for local dev: python -m moni.web.server)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "moni.web.server:create_app",
        factory=True,
        host=HOST,
        port=PORT,
        reload=False,
    )
