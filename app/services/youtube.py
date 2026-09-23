"""Работа с YouTube через yt-dlp: метаданные, субтитры, аудио."""
import logging
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

from app.config import settings
from app.models import Cue, VideoInfo

log = logging.getLogger(__name__)

_YT_ID_RE = re.compile(r"(?:v=|/shorts/|/live/|youtu\.be/|/embed/)([A-Za-z0-9_-]{11})")
_VTT_TIME_RE = re.compile(r"^\s*((?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)\s*-->")
_ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "youtu.be",
}


def extract_video_id(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host not in _ALLOWED_HOSTS:
        raise ValueError("Ссылка должна вести на YouTube (youtube.com или youtu.be)")
    m = _YT_ID_RE.search(url)
    if not m:
        raise ValueError("Не удалось распознать ссылку на YouTube-видео")
    return m.group(1)


# Один и тот же ролик может обрабатываться несколькими задачами сразу: медиа-файл у них общий,
# поэтому скачивание/транскрибацию/удаление для одного video_id выполняем по очереди.
_video_locks: dict[str, threading.Lock] = {}
_video_locks_guard = threading.Lock()


@contextmanager
def video_lock(video_id: str):
    with _video_locks_guard:
        lock = _video_locks.setdefault(video_id, threading.Lock())
    with lock:
        yield


def _base_opts() -> dict:
    opts: dict = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}
    if settings.yt_cookies_file:
        opts["cookiefile"] = str(settings.yt_cookies_file)
    if settings.yt_proxy:
        opts["proxy"] = settings.yt_proxy
    return opts


def get_video_info(url: str) -> VideoInfo:
    video_id = extract_video_id(url)
    try:
        with yt_dlp.YoutubeDL(_base_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        if "Sign in to confirm" in str(e):
            raise RuntimeError(
                "YouTube требует вход с этого IP. Укажите YT_COOKIES_FILE (cookies.txt) или YT_PROXY в .env"
            ) from e
        raise
    duration = info.get("duration")
    if duration and duration > settings.max_video_minutes * 60:
        raise ValueError(f"Видео длиннее лимита {settings.max_video_minutes} мин")
    return VideoInfo(
        video_id=video_id,
        title=info.get("title") or video_id,
        channel=info.get("uploader"),
        duration_sec=duration,
        url=f"https://www.youtube.com/watch?v={video_id}",
    )


def _clean_vtt(raw: str) -> str:
    """Превращаем WebVTT в чистый текст без таймкодов и дублей строк."""
    return " ".join(c.text for c in _parse_vtt(raw))


def _parse_vtt(raw: str) -> list[Cue]:
    """Разбираем WebVTT в реплики с таймкодами начала.

    Заодно убираем разметку, служебные строки и подряд идущие дубли: у автосубтитров
    каждая фраза приходит и «накопительно», и отдельными кусками.
    """
    cues: list[Cue] = []
    start = 0.0
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        if "-->" in line:
            m = _VTT_TIME_RE.match(line)
            if m:
                start = _vtt_seconds(m.group(1))
            continue
        if line.isdigit():
            continue
        text = re.sub(r"<[^>]+>", "", line).strip()
        if not text:
            continue
        if cues and cues[-1].text.endswith(text):
            continue
        cues.append(Cue(start=start, text=text))
    return cues


def _vtt_seconds(value: str) -> float:
    """'01:02:03.500' -> 3723.5; WebVTT допускает и запятую как разделитель долей."""
    parts = value.replace(",", ".").split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return 0.0
    secs = 0.0
    for n in nums:
        secs = secs * 60 + n
    return secs


def download_subtitles(url: str, video_id: str) -> tuple[str, str] | None:
    """Пробуем скачать готовые (в т.ч. авто) субтитры. Возвращает (текст, язык) или None."""
    timed = download_subtitle_cues(url, video_id)
    if timed is None:
        return None
    cues, lang = timed
    return " ".join(c.text for c in cues), lang


def download_subtitle_cues(url: str, video_id: str) -> tuple[list[Cue], str] | None:
    """Как download_subtitles, но сохраняет таймкоды (нужны /summarize)."""
    langs = [x.strip() for x in settings.subtitle_langs.split(",") if x.strip()]
    out_dir: Path = settings.media_dir
    opts = {
        **_base_opts(),
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": langs,
        "subtitlesformat": "vtt",
        "outtmpl": str(out_dir / f"{video_id}.%(ext)s"),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        log.warning("Субтитры недоступны: %s", e)
        return None

    for lang in langs:
        f = out_dir / f"{video_id}.{lang}.vtt"
        if f.exists():
            cues = _parse_vtt(f.read_text(encoding="utf-8", errors="ignore"))
            f.unlink(missing_ok=True)
            if len(" ".join(c.text for c in cues)) > 50:
                log.info("Субтитры найдены (%s), %d реплик", lang, len(cues))
                return cues, lang
    return None


def download_audio(url: str, video_id: str) -> Path:
    """Скачиваем только аудио-дорожку и конвертируем в 16 kHz mono mp3 (маленький файл)."""
    target = settings.media_dir / f"{video_id}.mp3"
    if target.exists():
        return target
    opts = {
        **_base_opts(),
        "skip_download": False,
        "format": "bestaudio[abr<=64]/bestaudio/best",
        "outtmpl": str(settings.media_dir / f"{video_id}.%(ext)s"),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "48"}],
        "postprocessor_args": ["-ac", "1", "-ar", "16000"],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    if not target.exists():
        raise RuntimeError("Аудио не было скачано")
    log.info("Аудио скачано: %s (%.1f MB)", target.name, target.stat().st_size / 1e6)
    return target
