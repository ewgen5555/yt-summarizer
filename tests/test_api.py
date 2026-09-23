import threading
import time

import pytest

from app.models import (
    AnalysisResult,
    Cue,
    Section,
    StructureResult,
    TranscriptResult,
    VideoInfo,
)
from app.services import analysis, pipeline, security, storage, transcribe, youtube

URL = "https://youtu.be/dQw4w9WgXcQ"


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

    def fake_transcribe_with_cues(audio):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.1)  # имитируем длинную транскрибацию
        with guard:
            active -= 1
        text = "текст речи " * 5
        return text, "ru", [Cue(start=0.0, text=text)]

    monkeypatch.setattr(youtube, "get_video_info", lambda url: VideoInfo(
        video_id="v" * 11, title="t", url=url))
    monkeypatch.setattr(youtube, "download_subtitle_cues", lambda url, vid: None)
    monkeypatch.setattr(youtube, "download_audio", fake_audio)
    monkeypatch.setattr(transcribe, "transcribe_with_cues", fake_transcribe_with_cues)

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


# ---------- владение задачами ----------

def test_job_response_hides_owner(client):
    job = storage.create_job("https://youtu.be/xxxxxxxxxxx", owner="anon:someone")
    body = client.get(f"/api/jobs/{job.id}").json()
    assert "owner" not in body


def test_jobs_list_hides_owner(client):
    storage.create_job(URL, owner=security.anonymous_owner("testclient"))
    body = client.get("/api/jobs").json()
    assert body and all("owner" not in job for job in body)


def test_other_client_cannot_read_job(client, monkeypatch):
    """Задача другого клиента недоступна: 404, а не 403 — чтобы не подтверждать её существование."""
    job = storage.create_job("https://youtu.be/xxxxxxxxxxx", owner="anon:other-client")
    monkeypatch.setattr(security, "authenticate", lambda request: "anon:me")
    r = client.get(f"/api/jobs/{job.id}")
    assert r.status_code == 404


def test_other_client_does_not_see_job_in_list(client, monkeypatch):
    storage.create_job("https://youtu.be/xxxxxxxxxxx", owner="anon:other-client")
    mine = storage.create_job("https://youtu.be/yyyyyyyyyyy", owner="anon:me")
    monkeypatch.setattr(security, "authenticate", lambda request: "anon:me")
    ids = [job["id"] for job in client.get("/api/jobs").json()]
    assert ids == [mine.id]


def test_legacy_jobs_are_not_public(client, monkeypatch):
    """Задачи, созданные до появления владельцев, доступны только владельцу токена."""
    legacy = storage.create_job("https://youtu.be/xxxxxxxxxxx", owner=security.ANONYMOUS_OWNER)
    monkeypatch.setattr(security, "authenticate", lambda request: "anon:anyone")
    assert client.get(f"/api/jobs/{legacy.id}").status_code == 404
    monkeypatch.setattr(security, "authenticate", lambda request: "token:abc")
    assert client.get(f"/api/jobs/{legacy.id}").status_code == 200


# ---------- авторизация ----------

def test_api_open_without_token(client, monkeypatch):
    monkeypatch.setattr(security.settings, "api_token", "")
    assert client.get("/api/jobs").status_code == 200


def test_api_requires_token_when_configured(client, monkeypatch):
    monkeypatch.setattr(security.settings, "api_token", "s3cret")
    assert client.get("/api/jobs").status_code == 401
    assert client.post("/api/process", json={"url": URL}).status_code == 401


def test_api_accepts_correct_token(client, monkeypatch):
    monkeypatch.setattr(security.settings, "api_token", "s3cret")
    r = client.get("/api/jobs", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200


def test_api_rejects_wrong_token(client, monkeypatch):
    monkeypatch.setattr(security.settings, "api_token", "s3cret")
    r = client.get("/api/jobs", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_health_stays_public(client, monkeypatch):
    """Проверка живости нужна мониторингу и не должна требовать токена."""
    monkeypatch.setattr(security.settings, "api_token", "s3cret")
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["auth_required"] is True


def test_owner_is_derived_from_token_not_ip(client, monkeypatch):
    monkeypatch.setattr(security.settings, "api_token", "s3cret")
    headers = {"Authorization": "Bearer s3cret"}
    first = client.post("/api/process", json={"url": URL}, headers=headers).json()["id"]
    second = client.post("/api/process", json={"url": URL}, headers=headers).json()["id"]
    assert storage.get_job(first).owner == storage.get_job(second).owner


# ---------- лимиты ----------

def test_process_rate_limit_returns_429(client, monkeypatch):
    monkeypatch.setattr(security.settings, "rate_limit_process_per_minute", 2)
    monkeypatch.setattr(security.settings, "max_concurrent_jobs_per_client", 0)
    codes = [client.post("/api/process", json={"url": URL}).status_code for _ in range(3)]
    assert codes == [202, 202, 429]


def test_rate_limit_sets_retry_after(client, monkeypatch):
    monkeypatch.setattr(security.settings, "rate_limit_process_per_minute", 1)
    monkeypatch.setattr(security.settings, "max_concurrent_jobs_per_client", 0)
    client.post("/api/process", json={"url": URL})
    r = client.post("/api/process", json={"url": URL})
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1


def test_read_limit_is_separate_from_process_limit(client, monkeypatch):
    """Опрос статуса каждые 2.5 с не должен упираться в лимит на запуск обработки."""
    monkeypatch.setattr(security.settings, "rate_limit_process_per_minute", 1)
    monkeypatch.setattr(security.settings, "max_concurrent_jobs_per_client", 0)
    client.post("/api/process", json={"url": URL})
    for _ in range(5):
        assert client.get("/api/jobs").status_code == 200


def test_daily_limit_applies(client, monkeypatch):
    monkeypatch.setattr(security.settings, "rate_limit_process_per_day", 1)
    monkeypatch.setattr(security.settings, "max_concurrent_jobs_per_client", 0)
    assert client.post("/api/structure", json={"text": "длинный текст " * 5}).status_code == 200
    assert client.post("/api/structure", json={"text": "длинный текст " * 5}).status_code == 429


def test_limits_disabled_by_setting(client, monkeypatch):
    monkeypatch.setattr(security.settings, "rate_limit_enabled", False)
    monkeypatch.setattr(security.settings, "rate_limit_process_per_minute", 1)
    codes = [client.post("/api/process", json={"url": URL}).status_code for _ in range(5)]
    assert codes == [202] * 5


def test_limits_are_per_client(client, monkeypatch):
    monkeypatch.setattr(security.settings, "rate_limit_process_per_minute", 1)
    monkeypatch.setattr(security.settings, "max_concurrent_jobs_per_client", 0)
    monkeypatch.setattr(security, "authenticate", lambda request: "anon:first")
    assert client.post("/api/process", json={"url": URL}).status_code == 202
    monkeypatch.setattr(security, "authenticate", lambda request: "anon:second")
    assert client.post("/api/process", json={"url": URL}).status_code == 202


def test_concurrent_job_limit_rejects_extra_work(client, monkeypatch):
    monkeypatch.setattr(security.settings, "max_concurrent_jobs_per_client", 1)
    security.concurrency_limiter.acquire(security.anonymous_owner("testclient"))
    assert client.post("/api/structure", json={"text": "длинный текст " * 5}).status_code == 429


def test_concurrent_slot_is_released_after_success(client, monkeypatch):
    monkeypatch.setattr(security.settings, "max_concurrent_jobs_per_client", 1)
    client.post("/api/process", json={"url": URL})  # TestClient выполняет фон синхронно
    assert security.concurrency_limiter._active == {}


def test_concurrent_slot_is_released_after_failure(client, monkeypatch):
    """Падение пайплайна не должно навсегда занимать слот клиента."""
    monkeypatch.setattr(security.settings, "max_concurrent_jobs_per_client", 1)

    def boom(url):
        raise RuntimeError("pipeline failed")

    monkeypatch.setattr(pipeline, "get_transcript", boom)
    assert client.post("/api/process", json={"url": URL}).status_code == 202
    assert security.concurrency_limiter._active == {}


def test_invalid_body_does_not_leak_slot(client, monkeypatch):
    """422 на невалидном теле не должен занимать слот одновременности."""
    monkeypatch.setattr(security.settings, "max_concurrent_jobs_per_client", 1)
    assert client.post("/api/summary", json={"text": "мало"}).status_code == 422
    assert security.concurrency_limiter._active == {}


def test_rejected_url_does_not_leak_slot(client, monkeypatch):
    monkeypatch.setattr(security.settings, "max_concurrent_jobs_per_client", 1)
    assert client.post("/api/process", json={"url": "https://vimeo.com/1"}).status_code == 400
    assert security.concurrency_limiter._active == {}


def test_client_ip_ignores_forwarded_header_by_default(client, monkeypatch):
    """Без trust_proxy подделанный X-Forwarded-For не должен менять клиента."""
    monkeypatch.setattr(security.settings, "trust_proxy", False)
    monkeypatch.setattr(security.settings, "rate_limit_process_per_minute", 1)
    monkeypatch.setattr(security.settings, "max_concurrent_jobs_per_client", 0)
    first = client.post("/api/process", json={"url": URL},
                        headers={"X-Forwarded-For": "1.2.3.4"})
    second = client.post("/api/process", json={"url": URL},
                         headers={"X-Forwarded-For": "5.6.7.8"})
    assert first.status_code == 202
    assert second.status_code == 429


def test_client_ip_uses_last_forwarded_entry_when_trusted(client, monkeypatch):
    """За прокси берём последний адрес: левые клиент дописывает сам и подделывает."""
    monkeypatch.setattr(security.settings, "trust_proxy", True)
    monkeypatch.setattr(security.settings, "rate_limit_process_per_minute", 1)
    monkeypatch.setattr(security.settings, "max_concurrent_jobs_per_client", 0)
    spoofed = {"X-Forwarded-For": "9.9.9.9, 1.2.3.4"}
    assert client.post("/api/process", json={"url": URL}, headers=spoofed).status_code == 202
    assert client.post("/api/process", json={"url": URL}, headers=spoofed).status_code == 429
    # другой реальный клиент за тем же прокси имеет свой бюджет
    other = {"X-Forwarded-For": "9.9.9.9, 5.6.7.8"}
    assert client.post("/api/process", json={"url": URL}, headers=other).status_code == 202


def test_rate_limiter_forgets_idle_clients(monkeypatch):
    """Перебор IP не должен раздувать словарь клиентов до бесконечности."""
    monkeypatch.setattr(security.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(security.settings, "rate_limit_read_per_minute", 5)
    limiter = security.read_limiter
    monkeypatch.setattr(limiter, "PRUNE_EVERY", 3)
    limiter.check("anon:old")
    # сдвигаем окно в прошлое, чтобы клиент считался неактивным
    for bucket in limiter._buckets["anon:old"].values():
        bucket.hits = [t - 3600 for t in bucket.hits]
    for i in range(2):
        limiter.check(f"anon:new{i}")
    assert "anon:old" not in limiter._buckets
    assert "anon:new1" in limiter._buckets
