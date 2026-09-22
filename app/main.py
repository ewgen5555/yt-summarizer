import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import BASE_DIR, settings
from app.logging_config import setup_logging
from app.models import HealthResponse
from app.services import storage

VERSION = "0.1.0"
FRONTEND_DIR: Path = BASE_DIR / "frontend"

setup_logging(settings.log_level, settings.data_dir)
log = logging.getLogger(__name__)
_started = time.time()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    log.info("Старт v%s: llm=%s/%s transcriber=%s", VERSION, settings.llm_provider, settings.llm_model,
             settings.transcriber)
    yield
    log.info("Остановка")


app = FastAPI(
    title="YouTube AI Summarizer",
    version=VERSION,
    description="Ссылка на YouTube → транскрипт → структура, резюме, ключевые идеи",
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/health", response_model=HealthResponse, tags=["service"])
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=VERSION,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        transcriber=settings.transcriber,
        jobs_total=storage.count_jobs(),
        uptime_sec=int(time.time() - _started),
        auth_required=bool(settings.api_token),
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

