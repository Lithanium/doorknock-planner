from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import load_settings
from app.services import SnapshotStore

DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def create_app(snapshot_path: Path | None = None) -> FastAPI:
    settings = load_settings()
    app = FastAPI(title="Doorknock Planner", version="0.1.0")
    app.state.settings = settings
    app.state.store = SnapshotStore(snapshot_path or settings.snapshot_path)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(DEV_ORIGINS),
        # Private-network origins (RFC 1918), so phones on the LAN can reach
        # the dev server whatever the local subnet is.
        allow_origin_regex=(
            r"http://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+"
            r"|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+):\d+"
        ),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
