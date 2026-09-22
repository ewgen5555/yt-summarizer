"""Хранилище задач: один JSON-файл на задачу в data/jobs. Без БД — достаточно для слабого хостинга."""
import logging
import threading
import uuid
from datetime import datetime

from app.config import settings
from app.models import Job, JobStatus
from app.services import security

log = logging.getLogger(__name__)
_lock = threading.Lock()


def create_job(url: str, owner: str = security.ANONYMOUS_OWNER) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], url=url, owner=owner)
    save_job(job)
    return job


def save_job(job: Job) -> None:
    job.updated_at = datetime.utcnow()
    path = settings.jobs_dir / f"{job.id}.json"
    with _lock:
        path.write_text(job.model_dump_json(indent=2), encoding="utf-8")


def get_job(job_id: str) -> Job | None:
    path = settings.jobs_dir / f"{job_id}.json"
    if not path.exists():
        return None
    return Job.model_validate_json(path.read_text(encoding="utf-8"))


def list_jobs(limit: int = 50, owner: str | None = None) -> list[Job]:
    files = sorted(settings.jobs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    jobs = [Job.model_validate_json(p.read_text(encoding="utf-8")) for p in files]
    if owner is not None:
        jobs = [job for job in jobs if security.owns(job.owner, owner)]
    return jobs[:limit]


def count_jobs() -> int:
    return len(list(settings.jobs_dir.glob("*.json")))


def update_status(job: Job, status: JobStatus, progress: str = "") -> None:
    job.status = status
    job.progress = progress
    save_job(job)
    log.info("job %s -> %s %s", job.id, status.value, progress)
