import logging
import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from app.models import (
    Job,
    JobPublic,
    KeyIdeasResult,
    StructureResult,
    SummarizeResult,
    SummaryResult,
    TextRequest,
    TranscriptResult,
    UrlRequest,
)
from app.services import analysis, pipeline, security, storage, youtube

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["processing"])

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def current_client(request: Request) -> str:
    """Опознаёт клиента и применяет лимит на чтение (дешёвые GET-запросы)."""
    client_id = security.authenticate(request)
    security.read_limiter.check(client_id)
    return client_id


def paying_client(request: Request) -> str:
    """Для запросов, которые тратят деньги: лимит по частоте и по суткам.

    Слот одновременности здесь не занимаем: тело запроса валидируется после зависимостей,
    и на 422 слот остался бы занятым навсегда.
    """
    client_id = security.authenticate(request)
    security.cost_limiter.check(client_id)
    return client_id


# ---------- полный пайплайн (асинхронно, с опросом статуса) ----------

@router.post("/process", response_model=JobPublic, status_code=202,
             summary="Запустить полную обработку видео")
def process(req: UrlRequest, background: BackgroundTasks, client_id: str = Depends(paying_client)) -> Job:
    # Слот занимаем здесь (тело уже провалидировано) и передаём фоновой задаче: она освободит
    # его в finally, поэтому падение пайплайна не заблокирует клиента навсегда.
    security.concurrency_limiter.acquire(client_id)
    try:
        try:
            youtube.extract_video_id(str(req.url))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        job = storage.create_job(str(req.url), owner=client_id)
    except Exception:
        security.concurrency_limiter.release(client_id)
        raise
    background.add_task(pipeline.run_job_in_background, job.id, client_id)
    return job


@router.get("/jobs/{job_id}", response_model=JobPublic, summary="Статус и результат задачи")
def get_job(job_id: str, client_id: str = Depends(current_client)) -> Job:
    job = storage.get_job(job_id)
    # Чужая задача неотличима от несуществующей: иначе перебором можно узнать, какие id есть.
    if job is None or not security.owns(job.owner, client_id):
        raise HTTPException(404, "Задача не найдена")
    return job


@router.get("/jobs", response_model=list[JobPublic], summary="Последние задачи")
def list_jobs(limit: int = Query(20, ge=1, le=100), client_id: str = Depends(current_client)) -> list[Job]:
    return storage.list_jobs(limit, owner=client_id)


# ---------- одним запросом: видео -> транскрипт -> резюме ----------

@router.get("/summarize", response_model=SummarizeResult,
            summary="YouTube -> транскрипт -> резюме с ключевыми пунктами")
def summarize(video_id: str = Query(..., description="ID видео (dQw4w9WgXcQ) или ссылка на YouTube"),
              client_id: str = Depends(paying_client)) -> SummarizeResult:
    """Минимальный сценарий целиком: достаём транскрипт и сразу отдаём резюме по нему."""
    url = _to_watch_url(video_id)
    with security.job_slot(client_id):
        try:
            timed = pipeline.get_transcript_timed(url)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # noqa: BLE001
            log.exception("summarize: получение транскрипта не удалось")
            raise HTTPException(500, f"Не удалось получить транскрипт: {type(e).__name__}: {e}") from e

        if not timed.cues:
            raise HTTPException(422, "В видео не распознано ни одной фразы")

        try:
            summary, points = analysis.summarize_timed(timed.cues)
        except Exception as e:  # noqa: BLE001
            log.exception("summarize: ИИ-модель не ответила")
            raise HTTPException(502, f"Ошибка ИИ-модели: {type(e).__name__}: {e}") from e

        return SummarizeResult(video=timed.video, source=timed.source, language=timed.language,
                               summary=summary, key_points=points)


def _to_watch_url(video_id: str) -> str:
    """Принимаем и голый ID, и полную ссылку — сужаем до YouTube-хоста и проверяем ID."""
    value = video_id.strip()
    if not value:
        raise HTTPException(400, "Укажите video_id или ссылку на YouTube")
    url = value if "://" in value else f"https://www.youtube.com/watch?v={value}"
    try:
        return f"https://www.youtube.com/watch?v={youtube.extract_video_id(url)}"
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ---------- отдельные этапы (синхронно) ----------

@router.post("/transcript", response_model=TranscriptResult, summary="Ссылка -> транскрипт")
def transcript(req: UrlRequest, client_id: str = Depends(paying_client)) -> TranscriptResult:
    with security.job_slot(client_id):
        try:
            return pipeline.get_transcript(str(req.url))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # noqa: BLE001
            log.exception("transcript failed")
            raise HTTPException(500, f"{type(e).__name__}: {e}") from e


@router.post("/structure", response_model=StructureResult, summary="Текст -> структура разделов")
def structure(req: TextRequest, client_id: str = Depends(paying_client)) -> StructureResult:
    return _safe(lambda: analysis.structure(req.text), client_id)


@router.post("/summary", response_model=SummaryResult, summary="Текст -> краткое резюме")
def summary(req: TextRequest, client_id: str = Depends(paying_client)) -> SummaryResult:
    return _safe(lambda: analysis.summary(req.text), client_id)


@router.post("/ideas", response_model=KeyIdeasResult, summary="Текст -> ключевые идеи")
def ideas(req: TextRequest, client_id: str = Depends(paying_client)) -> KeyIdeasResult:
    return _safe(lambda: analysis.key_ideas(req.text), client_id)


def _safe(fn, client_id: str):
    with security.job_slot(client_id):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            log.exception("LLM step failed")
            raise HTTPException(502, f"Ошибка ИИ-модели: {type(e).__name__}: {e}") from e
