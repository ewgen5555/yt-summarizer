import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.models import (
    Job,
    KeyIdeasResult,
    StructureResult,
    SummaryResult,
    TextRequest,
    TranscriptResult,
    UrlRequest,
)
from app.services import analysis, pipeline, storage

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["processing"])


# ---------- полный пайплайн (асинхронно, с опросом статуса) ----------

@router.post("/process", response_model=Job, status_code=202, summary="Запустить полную обработку видео")
def process(req: UrlRequest, background: BackgroundTasks) -> Job:
    job = storage.create_job(str(req.url))
    background.add_task(pipeline.run_job, job.id)
    return job


@router.get("/jobs/{job_id}", response_model=Job, summary="Статус и результат задачи")
def get_job(job_id: str) -> Job:
    job = storage.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    return job


@router.get("/jobs", response_model=list[Job], summary="Последние задачи")
def list_jobs(limit: int = 20) -> list[Job]:
    return storage.list_jobs(limit)


# ---------- отдельные этапы (синхронно) ----------

@router.post("/transcript", response_model=TranscriptResult, summary="Ссылка -> транскрипт")
def transcript(req: UrlRequest) -> TranscriptResult:
    try:
        return pipeline.get_transcript(str(req.url))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.exception("transcript failed")
        raise HTTPException(500, f"{type(e).__name__}: {e}") from e


@router.post("/structure", response_model=StructureResult, summary="Текст -> структура разделов")
def structure(req: TextRequest) -> StructureResult:
    return _safe(lambda: analysis.structure(req.text))


@router.post("/summary", response_model=SummaryResult, summary="Текст -> краткое резюме")
def summary(req: TextRequest) -> SummaryResult:
    return _safe(lambda: analysis.summary(req.text))


@router.post("/ideas", response_model=KeyIdeasResult, summary="Текст -> ключевые идеи")
def ideas(req: TextRequest) -> KeyIdeasResult:
    return _safe(lambda: analysis.key_ideas(req.text))


def _safe(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log.exception("LLM step failed")
        raise HTTPException(502, f"Ошибка ИИ-модели: {type(e).__name__}: {e}") from e
