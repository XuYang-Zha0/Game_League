"""Unified Game League backend entrypoint.

Run this file instead of starting one backend per game:

    .venv/bin/python api_server.py
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from counter_strike.cs_api_server import (
    LIVE_SYNC_STATE,
    router as cs2_router,
    start_live_sync_worker,
    stop_live_sync_worker,
)
from ai_assistant import router as ai_router
from valorant.valorant_api_server import router as valorant_router


app = FastAPI(title="Game League API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.on_event("startup")
def app_startup() -> None:
    start_live_sync_worker()


@app.on_event("shutdown")
def app_shutdown() -> None:
    stop_live_sync_worker()


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "liveSync": {
            "enabled": LIVE_SYNC_STATE.get("enabled", False),
            "startedAt": LIVE_SYNC_STATE.get("startedAt", ""),
            "lastRunAt": LIVE_SYNC_STATE.get("lastRunAt", ""),
            "lastError": LIVE_SYNC_STATE.get("lastError", ""),
        },
    }


app.include_router(cs2_router)
app.include_router(valorant_router)
app.include_router(ai_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api_server:app", host="127.0.0.1", port=8000, reload=True)
