"""Тесты минимального сценария /summarize: таймкоды, группировка, сборка ответа.

Сеть не трогаем: LLM и транскрипт подменяем.
"""

from unittest.mock import MagicMock

import pytest

from app.models import Cue, TimedTranscript, VideoInfo
from app.services import analysis, pipeline, security


def test_format_timestamp():
    assert analysis.format_timestamp(0) == "00:00"
    assert analysis.format_timestamp(65) == "01:05"
    assert analysis.format_timestamp(3725) == "1:02:05"


def test_timed_text_marks_every_line_with_timecode():
    cues = [Cue(start=0, text="привет"), Cue(start=65, text="пока")]
    assert analysis.format_timed_text(cues) == "[00:00] привет\n[01:05] пока"


def test_clean_timestamp_keeps_only_plausible_values():
    assert analysis._clean_timestamp("01:05") == "01:05"
    assert analysis._clean_timestamp("[1:02:05]") == "01:02:05"
    assert analysis._clean_timestamp("примерно в начале") is None
    assert analysis._clean_timestamp(None) is None
    assert analysis._clean_timestamp("12") is None


def test_cue_groups_split_on_size_and_keep_cues_whole():
    cues = [Cue(start=0, text="a" * 10), Cue(start=1, text="b" * 10), Cue(start=2, text="c" * 10)]
    assert [len(g) for g in analysis._cue_groups(cues, 15)] == [1, 1, 1]
    assert [len(g) for g in analysis._cue_groups(cues, 25)] == [2, 1]


def test_summarize_timed_exposes_summary_and_points(monkeypatch):
    fake = MagicMock()
    fake.complete_json.return_value = {
        "summary": "О чём видео",
        "key_points": [
            {"text": "первый пункт", "timestamp": "00:05"},
            {"text": "второй пункт", "timestamp": "не знаю"},
            {"text": "  ", "timestamp": "00:09"},
        ],
    }
    monkeypatch.setattr(analysis, "get_llm", lambda: fake)

    summary, points = analysis.summarize_timed([Cue(start=5, text="речь")])

    assert summary == "О чём видео"
    # пустой пункт отбрасываем, непонятный таймкод не выдумываем
    assert [(p.text, p.timestamp) for p in points] == [("первый пункт", "00:05"), ("второй пункт", None)]
    assert "[00:05] речь" in fake.complete_json.call_args.args[0]


def test_summarize_timed_chunks_long_transcripts(monkeypatch):
    """Длинный транскрипт режем на группы, таймкоды внутри групп сохраняются."""
    fake = MagicMock()
    fake.complete_json.side_effect = [
        {"summary": "часть 1", "key_points": [{"text": "п1", "timestamp": "00:00"}]},
        {"summary": "часть 2", "key_points": [{"text": "п2", "timestamp": "00:20"}]},
    ]
    monkeypatch.setattr(analysis, "get_llm", lambda: fake)
    monkeypatch.setattr(analysis.settings, "llm_chunk_chars", 12)

    cues = [Cue(start=0, text="a" * 10), Cue(start=20, text="b" * 10)]
    summary, points = analysis.summarize_timed(cues)

    assert fake.complete_json.call_count == 2
    assert summary == "часть 1\n\nчасть 2"
    assert [p.timestamp for p in points] == ["00:00", "00:20"]


# ---------- endpoint ----------

def _offline_summarize(monkeypatch) -> None:
    """Подменяем транскрипт и модель, чтобы /summarize не ходил в сеть."""
    video = VideoInfo(video_id="dQw4w9WgXcQ", title="Тест", url="https://youtu.be/dQw4w9WgXcQ")
    timed = TimedTranscript(video=video, source="youtube_subtitles", language="ru",
                            cues=[Cue(start=0, text="речь")])
    monkeypatch.setattr(pipeline, "get_transcript_timed", lambda url: timed)
    monkeypatch.setattr(analysis, "summarize_timed",
                        lambda cues: ("Резюме", [analysis.SummaryPoint(text="пункт", timestamp="00:00")]))


def test_summarize_endpoint_accepts_video_id(client, monkeypatch):
    _offline_summarize(monkeypatch)

    resp = client.get("/api/summarize", params={"video_id": "dQw4w9WgXcQ"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"] == "Резюме"
    assert body["key_points"] == [{"text": "пункт", "timestamp": "00:00"}]


@pytest.mark.parametrize("value", ["", "   ", "short", "https://evil.com/watch?v=dQw4w9WgXcQ"])
def test_summarize_rejects_bad_input(client, value):
    resp = client.get("/api/summarize", params={"video_id": value})
    assert resp.status_code in (400, 422)


def test_summarize_applies_paid_rate_limit(client, monkeypatch):
    _offline_summarize(monkeypatch)
    monkeypatch.setattr(security.settings, "rate_limit_process_per_minute", 1)
    security.reset_all()

    assert client.get("/api/summarize", params={"video_id": "dQw4w9WgXcQ"}).status_code == 200
    assert client.get("/api/summarize", params={"video_id": "dQw4w9WgXcQ"}).status_code == 429


def test_summarize_requires_auth_when_token_set(client, monkeypatch):
    _offline_summarize(monkeypatch)
    monkeypatch.setattr(security.settings, "api_token", "secret")
    security.reset_all()

    assert client.get("/api/summarize", params={"video_id": "dQw4w9WgXcQ"}).status_code == 401
    ok = client.get("/api/summarize", params={"video_id": "dQw4w9WgXcQ"},
                    headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200


def test_summarize_reports_llm_failure_as_502(client, monkeypatch):
    _offline_summarize(monkeypatch)

    def boom(cues):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(analysis, "summarize_timed", boom)

    resp = client.get("/api/summarize", params={"video_id": "dQw4w9WgXcQ"})
    assert resp.status_code == 502


def test_summarize_reports_empty_transcript(client, monkeypatch):
    timed = TimedTranscript(video=VideoInfo(video_id="dQw4w9WgXcQ", title="Т", url="https://youtu.be/dQw4w9WgXcQ"),
                            source="faster_whisper", language=None, cues=[])
    monkeypatch.setattr(pipeline, "get_transcript_timed", lambda url: timed)

    resp = client.get("/api/summarize", params={"video_id": "dQw4w9WgXcQ"})
    assert resp.status_code == 422
