"""FastAPI app: /api plus the built React bundle, on one port.

Run with:  uvicorn backend.app.main:app --port 8000
Then open: http://localhost:8000
"""

from __future__ import annotations

import logging
import logging.handlers
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app import config
from backend.app.api.routes import router
from backend.app.db.models import init_db, seed_watchlist

STATIC_DIR = Path(__file__).parent / "static"
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


def configure_logging() -> None:
    """Rotating file log. CLAUDE.md: when numbers look wrong, the log is how you find out
    whether it was the API or the maths."""
    LOG_DIR.mkdir(exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "screener.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s"))
    root = logging.getLogger()
    root.setLevel(config.settings.log_level.upper())
    root.addHandler(handler)
    root.addHandler(logging.StreamHandler())


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    init_db()
    seed_watchlist()
    logging.getLogger(__name__).info("Options screener ready on http://localhost:8000")
    yield
    from backend.app.ib.client import get_client

    await get_client().disconnect()


app = FastAPI(
    title="Options Screener",
    description="Local options screener. Read-only against IBKR; never places orders.",
    version="0.1.0",
    lifespan=lifespan,
)

# Vite's dev server, for when the UI is being worked on.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        """Serve the SPA for any non-API path.

        Unknown /api paths must still 404 -- returning index.html for a mistyped endpoint
        turns a clear error into a silent empty screen.
        """
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        index = STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        return JSONResponse({"detail": "Frontend not built"}, status_code=404)

else:

    @app.get("/")
    def no_frontend() -> JSONResponse:
        return JSONResponse(
            {
                "detail": "Frontend not built.",
                "fix": "cd frontend && npm install && npm run build",
                "api_docs": "/docs",
            },
            status_code=200,
        )
