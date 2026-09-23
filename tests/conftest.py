"""Общие фикстуры тестов.

`client` отдаёт TestClient с уже выключенной сетью: пайплайн уходит в фон, поэтому
тесты API не должны ходить в YouTube и к модели. Подмены живут именно здесь, а не в
autouse-фикстуре: autouse перекрывал бы настоящие analyze()/get_transcript() в тестах,
которые проверяют саму логику этих функций и клиентом не пользуются.
"""

import pytest
from fastapi.testclient import TestClient

from app import main
from app.models import (
    AnalysisResult,
    Cue,
    KeyIdeasResult,
    Section,
    StructureResult,
    SummaryPoint,
    SummaryResult,
    TranscriptResult,
    VideoInfo,
)
from app.services import analysis, pipeline, security

URL = "https://youtu.be/dQw4w9WgXcQ"


@pytest.fixture(autouse=True)
def reset_limiters():
    """Лимитеры живут в памяти процесса — без сброса счётчики протекают между тестами."""
    security.reset_all()
    yield
    security.reset_all()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    monkeypatch.setattr(security.settings, "data_dir", tmp_path)
    monkeypatch.setattr(security.settings, "api_token", "")
    monkeypatch.setattr(security.settings, "trust_proxy", False)
    monkeypatch.setattr(security.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(security.settings, "rate_limit_process_per_minute", 10)
    monkeypatch.setattr(security.settings, "rate_limit_process_per_day", 50)
    monkeypatch.setattr(security.settings, "rate_limit_read_per_minute", 120)
    monkeypatch.setattr(security.settings, "max_concurrent_jobs_per_client", 2)
    go_offline(monkeypatch)
    with TestClient(main.app) as c:
        yield c


def go_offline(monkeypatch) -> None:
    """Подменяет транскрипцию и модель, чтобы тесты не ходили в сеть."""
    from app.models import TimedTranscript

    video = VideoInfo(video_id="dQw4w9WgXcQ", title="Тест", url=URL)
    transcript = TranscriptResult(video=video, source="youtube_subtitles", language="ru",
                                  text="текст " * 50)
    timed = TimedTranscript(video=video, source="youtube_subtitles", language="ru",
                            cues=[Cue(start=0.0, text="текст речи " * 20)])
    monkeypatch.setattr(pipeline, "get_transcript", lambda url: transcript)
    monkeypatch.setattr(pipeline, "get_transcript_timed", lambda url: timed)
    monkeypatch.setattr(analysis, "analyze", lambda text: AnalysisResult(
        structure=StructureResult(topic="Тема", sections=[Section(title="Раздел", summary="О чём")]),
        summary="Резюме",
        key_ideas=["Идея"],
    ))
    monkeypatch.setattr(analysis, "structure", lambda text: StructureResult(
        topic="Тема", sections=[Section(title="Раздел", summary="О чём")]))
    monkeypatch.setattr(analysis, "summary", lambda text: SummaryResult(summary="Резюме"))
    monkeypatch.setattr(analysis, "key_ideas", lambda text: KeyIdeasResult(ideas=["Идея"]))
    monkeypatch.setattr(analysis, "summarize_timed", lambda cues: (
        "Резюме видео", [SummaryPoint(text="Ключевой пункт", timestamp="00:00")]))
