"""Регрессия на баг «пустой транскрипт».

Речь поверх музыки (клипы, заставки, интро) — обычный случай для YouTube. Если включить
vad_filter, встроенный Silero VAD может не найти ни одного речевого сегмента и вернуть
пустой текст, хотя без VAD та же дорожка распознаётся. Тест воспроизводит это на
синтетической дорожке и проверяет, что сервис возвращает текст.

Тест скипается, если нет faster-whisper или синтезатора речи.
"""
import math
import os
import random
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from app.services import transcribe

SAMPLE_RATE = 16000
SPEECH = "Today we will discuss three practical ways to improve your focus."


def _skip_or_fail(reason: str) -> None:
    """Локально тест может скипнуться, в CI — падать: иначе баг снова пройдёт незамеченным."""
    if os.getenv("CI"):
        pytest.fail(f"регрессионный тест не смог запуститься в CI: {reason}")
    pytest.skip(reason)


def _module_available(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


def _write_wav(path: Path, samples: list[float]) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32000)) for s in samples))


def _read_wav(path: Path) -> list[float]:
    with wave.open(str(path), "rb") as w:
        n, ch = w.getnframes(), w.getnchannels()
        raw = w.readframes(n)
    return [v / 32768.0 for v in struct.unpack(f"<{n * ch}h", raw)]


@pytest.fixture(scope="module")
def audio_with_music(tmp_path_factory) -> Path:
    """Речь с музыкальной подложкой: без VAD распознаётся, с VAD — теряется."""
    if not _module_available("faster_whisper"):
        _skip_or_fail("нет faster-whisper")
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    if espeak is None:
        _skip_or_fail("espeak-ng не установлен")

    tmp = tmp_path_factory.mktemp("media")
    spoken = tmp / "spoken.wav"
    subprocess.run([espeak, "-v", "en-us", "-s", "150", "-w", str(spoken), SPEECH], check=True)
    speech = _read_wav(spoken)

    rng = random.Random(0)
    mixed = []
    for i in range(len(speech) + SAMPLE_RATE // 2):
        base = speech[i] if i < len(speech) else 0.0
        music = (0.35 * math.sin(2 * math.pi * 220 * i / SAMPLE_RATE)
                 + 0.5 * 0.35 * math.sin(2 * math.pi * 330 * i / SAMPLE_RATE)
                 + 0.4 * 0.35 * (rng.random() * 2 - 1))
        mixed.append(max(-1.0, min(1.0, base * 1.6 + music)))

    out = tmp / "music_speech.wav"
    _write_wav(out, mixed)
    return out


def test_transcribe_returns_speech_over_music(audio_with_music, monkeypatch):
    """Главная проверка: сервис обязан вернуть текст, а не пустоту."""
    text, lang = transcribe.transcribe(audio_with_music)
    assert len(text.strip()) > 20, f"транскрипт пустой (lang={lang})"
    assert any(w in text.lower() for w in ("improve", "focus", "discuss", "ways", "practical"))


def test_vad_would_have_dropped_the_speech(audio_with_music, monkeypatch):
    """Фиксируем саму причину поломки: с VAD текста заметно меньше, чем без него."""
    from faster_whisper import WhisperModel

    model = WhisperModel("base", device="cpu", compute_type="int8")

    def run(vad: bool) -> str:
        segments, _ = model.transcribe(str(audio_with_music), language="en", vad_filter=vad, beam_size=1)
        return " ".join(s.text for s in segments).strip()

    without_vad = run(False)
    with_vad = run(True)
    assert len(without_vad) > 20
    assert len(without_vad) > len(with_vad)
