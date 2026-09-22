import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import main
from app.models import AnalysisResult, Section, StructureResult, TranscriptResult, VideoInfo
from app.services import analysis, pipeline, transcribe, youtube


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    return TestClient(main.app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "llm_model" in body


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "YouTube" in r.text


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    ],
)
def test_extract_video_id(url):
    assert youtube.extract_video_id(url) == "dQw4w9WgXcQ"


def test_extract_video_id_invalid():
    with pytest.raises(ValueError):
        youtube.extract_video_id("https://example.com/video")


def test_clean_vtt():
    raw = (
        "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nПривет <c>мир</c>\n\n"
        "00:00:02.000 --> 00:00:03.000\nПривет мир\nКак дела"
    )
    assert youtube._clean_vtt(raw) == "Привет мир Как дела"


def test_full_pipeline_with_mocks(client, monkeypatch):
    video = VideoInfo(video_id="x" * 11, title="Тест", url="https://youtu.be/xxxxxxxxxxx")
    fake_transcript = TranscriptResult(video=video, source="youtube_subtitles", language="ru",
                                       text="тест " * 50)
    fake_analysis = AnalysisResult(
        structure=StructureResult(topic="Тема", sections=[Section(title="Раздел", summary="О чём")]),
        summary="Резюме",
        key_ideas=["Идея 1", "Идея 2"],
    )
    monkeypatch.setattr(pipeline, "get_transcript", lambda url: fake_transcript)
    monkeypatch.setattr(analysis, "analyze", lambda text: fake_analysis)

    r = client.post("/api/process", json={"url": "https://youtu.be/xxxxxxxxxxx"})
    assert r.status_code == 202
    job_id = r.json()["id"]

    # TestClient выполняет BackgroundTasks синхронно, к этому моменту задача уже завершена
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done"
    assert job["analysis"]["summary"] == "Резюме"
    assert job["analysis"]["key_ideas"] == ["Идея 1", "Идея 2"]


class _FakeLLM:
    """Считает запросы к модели и возвращает валидные ответы под каждый промпт."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str, **_kwargs) -> str:
        self.prompts.append(prompt)
        return "тезис"

    def complete_json(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        if "ideas" in prompt:
            return {"ideas": ["идея"]}
        return {"topic": "тема", "sections": [{"title": "раздел", "summary": "о чём"}]}


def test_analyze_condenses_transcript_only_once(monkeypatch):
    """analyze() не должен повторно сжимать уже сжатый текст: это лишние запросы к модели."""
    fake = _FakeLLM()
    monkeypatch.setattr(analysis, "get_llm", lambda: fake)
    monkeypatch.setattr(analysis.settings, "llm_chunk_chars", 100)

    text = "слово " * 400  # 2400 символов -> 24 фрагмента
    analysis.analyze(text)

    chunk_prompts = [p for p in fake.prompts if "фрагмент" in p]
    assert len(chunk_prompts) == 24, "ожидалось одно сжатие, а не два"
    assert len(fake.prompts) == 24 + 3, "после сжатия должно быть ровно три запроса"


def test_analyze_matches_individual_steps(monkeypatch):
    """Результат analyze() совпадает с отдельными вызовами structure/summary/key_ideas."""
    fake = _FakeLLM()
    monkeypatch.setattr(analysis, "get_llm", lambda: fake)
    result = analysis.analyze("достаточно длинный текст для анализа " * 3)
    assert result.summary == "тезис"
    assert result.key_ideas == ["идея"]
    assert result.structure.topic == "тема"


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/videos/youtu.be/aaaaaaaaaaa",
        "http://169.254.169.254/latest/meta-data/",
        "https://vimeo.com/12345",
        "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
    ],
)
def test_extract_video_id_rejects_foreign_hosts(url):
    with pytest.raises(ValueError):
        youtube.extract_video_id(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
    ],
)
def test_extract_video_id_accepts_youtube_subdomains(url):
    assert youtube.extract_video_id(url) == "dQw4w9WgXcQ"


def test_process_rejects_foreign_url(client):
    r = client.post("/api/process", json={"url": "http://169.254.169.254/latest/meta-data/"})
    assert r.status_code == 400


def test_jobs_limit_is_bounded(client):
    assert client.get("/api/jobs?limit=999999").status_code == 422


def test_same_video_is_processed_one_at_a_time(tmp_path, monkeypatch):
    """Две задачи на один video_id не должны работать с общим медиа-файлом одновременно."""
    active = 0
    peak = 0
    guard = threading.Lock()

    def fake_audio(url, video_id):
        audio = tmp_path / f"{video_id}.mp3"
        audio.write_bytes(b"fake")
        return audio

    def fake_transcribe(audio):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.1)  # имитируем длинную транскрибацию
        with guard:
            active -= 1
        return "текст речи " * 5, "ru"

    monkeypatch.setattr(youtube, "get_video_info", lambda url: VideoInfo(
        video_id="v" * 11, title="t", url=url))
    monkeypatch.setattr(youtube, "download_subtitles", lambda url, vid: None)
    monkeypatch.setattr(youtube, "download_audio", fake_audio)
    monkeypatch.setattr(transcribe, "transcribe", fake_transcribe)

    url = "https://youtu.be/" + "v" * 11
    threads = [threading.Thread(target=pipeline.get_transcript, args=(url,)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak == 1, "задачи на одно видео обрабатывались параллельно"


def test_job_not_found(client):
    assert client.get("/api/jobs/nope").status_code == 404


def test_text_request_validation(client):
    assert client.post("/api/summary", json={"text": "короткий"}).status_code == 422
