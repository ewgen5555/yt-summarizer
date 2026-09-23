"""Транскрибация аудио. Движок выбирается настройкой TRANSCRIBER."""
import logging
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.models import Cue

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _whisper_model():
    # импорт внутри, чтобы сервис запускался и без faster-whisper (если выбран openai)
    from faster_whisper import WhisperModel

    log.info("Загрузка faster-whisper '%s' (%s/%s)", settings.whisper_model, settings.whisper_device,
             settings.whisper_compute_type)
    return WhisperModel(settings.whisper_model, device=settings.whisper_device,
                        compute_type=settings.whisper_compute_type)


def _transcribe_faster_whisper(audio: Path) -> tuple[str, str | None]:
    text, lang, _ = _transcribe_faster_whisper_cues(audio)
    return text, lang


def _transcribe_faster_whisper_cues(audio: Path) -> tuple[str, str | None, list[Cue]]:
    model = _whisper_model()
    segments, info = model.transcribe(str(audio), language=settings.whisper_language,
                                      vad_filter=settings.whisper_vad, beam_size=1)
    cues: list[Cue] = []
    for s in segments:
        t = s.text.strip()
        if t:
            cues.append(Cue(start=float(s.start), text=t))
    return " ".join(c.text for c in cues), info.language, cues


def _transcribe_openai(audio: Path) -> tuple[str, str | None]:
    from openai import OpenAI

    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url,
                    timeout=settings.llm_timeout)
    with audio.open("rb") as f:
        resp = client.audio.transcriptions.create(model=settings.openai_transcribe_model, file=f)
    return resp.text, settings.whisper_language


def transcribe(audio: Path) -> tuple[str, str | None]:
    """Возвращает (текст, язык)."""
    text, lang, _ = transcribe_with_cues(audio)
    return text, lang


def transcribe_with_cues(audio: Path) -> tuple[str, str | None, list[Cue]]:
    """Как transcribe, но дополнительно отдаёт реплики с таймкодами (для /summarize).

    Движки, которые не отдают сегменты (openai), возвращают одну реплику с началом 0 —
    тогда таймкоды в ответе просто не заполнятся, а текст останется полным.
    """
    engine = settings.transcriber
    log.info("Транскрибация %s движком %s", audio.name, engine)
    if engine == "faster_whisper":
        return _transcribe_faster_whisper_cues(audio)
    if engine == "openai":
        text, lang = _transcribe_openai(audio)
        return text, lang, ([Cue(start=0.0, text=text)] if text.strip() else [])
    raise ValueError(f"Неизвестный TRANSCRIBER: {engine}")
