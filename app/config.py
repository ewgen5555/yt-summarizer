from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Все настройки читаются из переменных окружения / файла .env."""

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    # --- сервер ---
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    data_dir: Path = BASE_DIR / "data"

    # --- ИИ-модель (любой OpenAI-совместимый API: OpenAI, OpenRouter, Groq, DeepSeek, Ollama...) ---
    llm_provider: str = "openai"          # openai | ollama | openrouter | groq | deepseek | custom
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 8192
    llm_timeout: int = 120
    # сколько символов транскрипта отправлять в модель за один запрос
    llm_chunk_chars: int = 12000

    # --- транскрибация ---
    # subtitles: сначала пробуем готовые субтитры YouTube (бесплатно, быстро),
    # затем fallback на движок ниже
    prefer_youtube_subtitles: bool = True
    transcriber: str = "faster_whisper"   # faster_whisper | openai
    whisper_model: str = "base"           # tiny | base | small (для слабого ПК: tiny/base)
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_language: str | None = None   # None = автоопределение
    # VAD отбрасывает речь, если поверх неё музыка или шум (типично для клипов и заставок),
    # поэтому по умолчанию выключен. Включайте, если на входе много тишины и мало музыки.
    whisper_vad: bool = False
    openai_transcribe_model: str = "whisper-1"

    # --- ограничения ---
    max_video_minutes: int = 120
    subtitle_langs: str = "ru,en"
    # cookies.txt (Netscape) — если YouTube с IP хостинга отвечает "Sign in to confirm you're not a bot"
    yt_cookies_file: Path | None = None
    # прокси для yt-dlp, напр. socks5://user:pass@host:1080
    yt_proxy: str | None = None

    @property
    def jobs_dir(self) -> Path:
        p = self.data_dir / "jobs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def media_dir(self) -> Path:
        p = self.data_dir / "media"
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
