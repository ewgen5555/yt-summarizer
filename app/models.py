from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, HttpUrl


class JobStatus(str, Enum):
    queued = "queued"
    downloading = "downloading"
    transcribing = "transcribing"
    analyzing = "analyzing"
    done = "done"
    error = "error"


# ---------- запросы ----------

class UrlRequest(BaseModel):
    url: HttpUrl = Field(..., description="Ссылка на видео YouTube")


class TextRequest(BaseModel):
    text: str = Field(..., min_length=20, description="Транскрипт или любой текст для анализа")


# ---------- ответы ----------

class VideoInfo(BaseModel):
    video_id: str
    title: str
    channel: str | None = None
    duration_sec: int | None = None
    url: str


class TranscriptResult(BaseModel):
    video: VideoInfo
    source: str = Field(..., description="youtube_subtitles | faster_whisper | openai")
    language: str | None = None
    text: str


class Section(BaseModel):
    title: str
    summary: str
    timestamp: str | None = None


class StructureResult(BaseModel):
    topic: str
    sections: list[Section]


class SummaryResult(BaseModel):
    summary: str


class KeyIdeasResult(BaseModel):
    ideas: list[str]


class AnalysisResult(BaseModel):
    structure: StructureResult
    summary: str
    key_ideas: list[str]


class Job(BaseModel):
    id: str
    url: str
    # Кто создал задачу. Хранится на диске, но наружу не отдаётся — см. JobPublic.
    owner: str = ""
    status: JobStatus = JobStatus.queued
    progress: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    video: VideoInfo | None = None
    transcript: TranscriptResult | None = None
    analysis: AnalysisResult | None = None
    error: str | None = None


class JobPublic(Job):
    """Задача в ответе API: владелец скрыт.

    Отдельная модель, а не response_model_exclude: у списков FastAPI это исключение
    не применяется к элементам, и owner утёк бы из GET /api/jobs.
    """

    owner: str = Field(default="", exclude=True)


class HealthResponse(BaseModel):
    status: str
    version: str
    llm_provider: str
    llm_model: str
    transcriber: str
    jobs_total: int
    uptime_sec: int
    auth_required: bool = False
