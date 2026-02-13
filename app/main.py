from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings
from app.db import SessionFactory, close_db, init_db
from app.logging import configure_logging
from app.services.cache import CacheClient
from app.workers.search_worker import SearchWorker

configure_logging()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    cache_client = CacheClient(settings.redis_url)
    await cache_client.connect()

    worker = SearchWorker(
        session_factory=SessionFactory,
        cache_client=cache_client,
        settings=settings,
    )
    app.state.search_worker = worker

    yield

    await worker.shutdown()
    await cache_client.close()
    await close_db()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(router, prefix=settings.api_prefix)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def ui_home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
