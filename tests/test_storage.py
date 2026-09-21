"""Characterize JSON storage using isolated temporary directories."""

import os
import re
from datetime import datetime

import pytest

from app.models import JobStatus, TranscriptResult, VideoInfo
from app.services import storage

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.settings, "data_dir", tmp_path)


def test_create_job_persists_defaults():
    job = storage.create_job(URL)
    assert re.fullmatch(r"[0-9a-f]{12}", job.id)
    assert job.url == URL
    assert job.status == JobStatus.queued
    assert job.progress == ""
    assert job.error is None
    assert (storage.settings.jobs_dir / f"{job.id}.json").is_file()
    loaded = storage.get_job(job.id)
    assert loaded is not None
    assert loaded.model_dump() == job.model_dump()
    assert storage.count_jobs() == 1


def test_create_jobs_generates_distinct_ids():
    first = storage.create_job(URL)
    second = storage.create_job(URL)
    assert first.id != second.id
    assert storage.count_jobs() == 2


def test_missing_job_returns_none():
    assert storage.get_job("missing") is None


def test_empty_storage():
    assert storage.list_jobs() == []
    assert storage.count_jobs() == 0


def test_save_job_round_trips_unicode_and_nested_models():
    job = storage.create_job(URL)
    created_at = job.created_at
    old_updated_at = datetime(2000, 1, 1)
    job.updated_at = old_updated_at
    job.video = VideoInfo(video_id="dQw4w9WgXcQ", title="Лекция о данных", url=URL)
    job.transcript = TranscriptResult(
        video=job.video,
        source="youtube_subtitles",
        language="ru",
        text="Текст лекции с русскими символами и выводами.",
    )
    job.progress = "Транскрипт готов"
    storage.save_job(job)
    loaded = storage.get_job(job.id)
    assert loaded is not None
    assert loaded.model_dump() == job.model_dump()
    assert loaded.created_at == created_at
    assert loaded.updated_at > old_updated_at
    assert storage.count_jobs() == 1


@pytest.mark.parametrize("status", list(JobStatus))
def test_update_status_persists_status_and_progress(status):
    job = storage.create_job(URL)
    storage.update_status(job, status, "Проверка статуса")
    loaded = storage.get_job(job.id)
    assert loaded is not None
    assert loaded.status == status
    assert loaded.progress == "Проверка статуса"
    assert job.status == status


def test_error_details_are_persisted():
    job = storage.create_job(URL)
    job.error = "RuntimeError: test failure"
    storage.update_status(job, JobStatus.error, "Ошибка")
    loaded = storage.get_job(job.id)
    assert loaded is not None
    assert loaded.error == "RuntimeError: test failure"
    assert loaded.status == JobStatus.error


def test_list_jobs_uses_file_mtime_and_respects_limit():
    jobs = [storage.create_job(URL) for _ in range(3)]
    # Explicit timestamps avoid sleeps and filesystem clock resolution races.
    for job, timestamp in zip(jobs, [1_700_000_020, 1_700_000_010, 1_700_000_030], strict=True):
        os.utime(storage.settings.jobs_dir / f"{job.id}.json", (timestamp, timestamp))
    expected = [jobs[2].id, jobs[0].id, jobs[1].id]
    assert [job.id for job in storage.list_jobs()] == expected
    assert [job.id for job in storage.list_jobs(limit=2)] == expected[:2]
    assert storage.list_jobs(limit=0) == []
    assert len(storage.list_jobs(limit=10)) == 3


def test_non_json_files_are_ignored():
    (storage.settings.jobs_dir / "notes.txt").write_text("not a job", encoding="utf-8")
    assert storage.count_jobs() == 0
    assert storage.list_jobs() == []
