import pytest
from fastapi.testclient import TestClient

from app import main
from app.models import AnalysisResult, Section, StructureResult, TranscriptResult, VideoInfo
from app.services import analysis, pipeline, youtube


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


def test_job_not_found(client):
    assert client.get("/api/jobs/nope").status_code == 404


def test_text_request_validation(client):
    assert client.post("/api/summary", json={"text": "короткий"}).status_code == 422
