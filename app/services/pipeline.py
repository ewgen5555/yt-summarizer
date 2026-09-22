"""Основной обработчик: ссылка -> транскрипт -> анализ. Каждый шаг доступен отдельно через API."""
import logging

from app.config import settings
from app.models import Job, JobStatus, TranscriptResult
from app.services import analysis, security, storage, transcribe, youtube

log = logging.getLogger(__name__)


def get_transcript(url: str) -> TranscriptResult:
    """Шаг 1-2: метаданные + текст. Сначала субтитры YouTube (бесплатно), иначе аудио + Whisper."""
    video = youtube.get_video_info(url)

    # Один и тот же ролик может обрабатываться несколькими задачами сразу, а медиа-файл у них общий.
    # Скачивание, транскрибация и удаление для одного video_id выполняются по очереди.
    with youtube.video_lock(video.video_id):
        if settings.prefer_youtube_subtitles:
            subs = youtube.download_subtitles(url, video.video_id)
            if subs:
                text, lang = subs
                return TranscriptResult(video=video, source="youtube_subtitles", language=lang, text=text)

        audio = youtube.download_audio(url, video.video_id)
        try:
            text, lang = transcribe.transcribe(audio)
        finally:
            audio.unlink(missing_ok=True)  # не храним медиа на диске
    if len(text.strip()) < 20:
        raise RuntimeError(
            "Не удалось распознать речь в этом видео: движок транскрибации вернул пустой текст. "
            "Проверьте, что в видео есть различимая речь, и при необходимости смените WHISPER_MODEL."
        )
    return TranscriptResult(video=video, source=settings.transcriber, language=lang, text=text)


def run_job(job_id: str) -> None:
    """Фоновая задача: выполняет весь пайплайн, сохраняя прогресс в хранилище."""
    job = storage.get_job(job_id)
    if job is None:
        log.error("job %s не найдена", job_id)
        return
    try:
        storage.update_status(job, JobStatus.downloading, "Получаем данные видео")
        job.transcript = get_transcript(job.url)
        job.video = job.transcript.video
        storage.update_status(job, JobStatus.analyzing,
                              f"Транскрипт готов ({len(job.transcript.text)} симв.), анализируем")

        job.analysis = analysis.analyze(job.transcript.text)
        storage.update_status(job, JobStatus.done, "Готово")
    except Exception as e:  # noqa: BLE001 — любая ошибка должна попасть в статус задачи
        log.exception("job %s завершилась ошибкой", job_id)
        job.error = f"{type(e).__name__}: {e}"
        storage.update_status(job, JobStatus.error, "Ошибка")


def run_job_in_background(job_id: str, client_id: str) -> None:
    """Обёртка для BackgroundTasks: слот снимаем всегда, в том числе при падении пайплайна,
    иначе клиент навсегда останется с исчерпанным лимитом одновременных задач."""
    try:
        run_job(job_id)
    finally:
        security.concurrency_limiter.release(client_id)


def process_sync(job: Job) -> Job:
    """Синхронный вариант (для CLI/тестов)."""
    run_job(job.id)
    return storage.get_job(job.id) or job
