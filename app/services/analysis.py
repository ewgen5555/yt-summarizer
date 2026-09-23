"""Анализ транскрипта: структура, резюме, ключевые идеи. Промпты вынесены в константы."""
import logging
import re

from app.config import settings
from app.models import (
    AnalysisResult,
    Cue,
    KeyIdeasResult,
    Section,
    StructureResult,
    SummaryPoint,
    SummaryResult,
)
from app.services.llm import get_llm

log = logging.getLogger(__name__)

# Модель может ответить в свободной форме, поэтому принимаем только правдоподобный таймкод.
_TIMESTAMP_RE = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$")

PROMPT_CHUNK_SUMMARY = """Ниже фрагмент {i}/{n} транскрипта видео.
Сжато перескажи его содержание (5-8 предложений), сохраняя факты, цифры и имена.

ФРАГМЕНТ:
{text}"""

PROMPT_STRUCTURE = """Разбей содержание видео на логические разделы.
Верни строго JSON вида:
{{"topic": "главная тема одной фразой",
  "sections": [{{"title": "название раздела", "summary": "2-3 предложения о чём раздел"}}]}}
Разделов должно быть от 3 до 10.

ТЕКСТ:
{text}"""

PROMPT_SUMMARY = """Напиши краткое резюме видео (120-200 слов) — о чём оно, какие выводы делает автор.

ТЕКСТ:
{text}"""

PROMPT_IDEAS = """Выдели 5-10 ключевых идей/тезисов видео. Каждая — одно ёмкое предложение.
Верни строго JSON: {{"ideas": ["идея 1", "идея 2", ...]}}

ТЕКСТ:
{text}"""

# Таймкоды в промпте помечены как [mm:ss]; просим модель вернуть метку пункта, если она есть.
PROMPT_SUMMARIZE = """Сделай краткое структурированное резюме видео и выдели ключевые пункты.
Каждый пункт транскрипта начинается с метки времени вида [mm:ss] — используй её, чтобы указать,
к какому моменту относится пункт. Если для пункта метку подобрать нельзя, оставь timestamp пустым.
Верни строго JSON:
{{"summary": "резюме на 100-150 слов",
  "key_points": [{{"text": "ключевой пункт", "timestamp": "mm:ss"}}]}}
Ключевых пунктов должно быть от 3 до 8.

ТРАНСКРИПТ:
{text}"""


def _chunks(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


def condense(text: str) -> str:
    """Длинный транскрипт сжимаем по частям (map-reduce), чтобы уложиться в контекст дешёвой модели."""
    if len(text) <= settings.llm_chunk_chars:
        return text
    parts = _chunks(text, settings.llm_chunk_chars)
    log.info("Транскрипт длинный (%d симв.) — сжимаем %d фрагментов", len(text), len(parts))
    llm = get_llm()
    summaries = [
        llm.complete(PROMPT_CHUNK_SUMMARY.format(i=i + 1, n=len(parts), text=p)) for i, p in enumerate(parts)
    ]
    return "\n\n".join(summaries)


def _structure(text: str) -> StructureResult:
    data = get_llm().complete_json(PROMPT_STRUCTURE.format(text=text))
    sections = [Section(**s) for s in data.get("sections", [])]
    return StructureResult(topic=data.get("topic", ""), sections=sections)


def _summary(text: str) -> SummaryResult:
    return SummaryResult(summary=get_llm().complete(PROMPT_SUMMARY.format(text=text)))


def _key_ideas(text: str) -> KeyIdeasResult:
    data = get_llm().complete_json(PROMPT_IDEAS.format(text=text))
    return KeyIdeasResult(ideas=[str(i) for i in data.get("ideas", [])])


def structure(text: str) -> StructureResult:
    return _structure(condense(text))


def summary(text: str) -> SummaryResult:
    return _summary(condense(text))


def key_ideas(text: str) -> KeyIdeasResult:
    return _key_ideas(condense(text))


def analyze(text: str) -> AnalysisResult:
    """Полный анализ: сжимаем один раз, затем три запроса к модели."""
    condensed = condense(text)
    return AnalysisResult(
        structure=_structure(condensed),
        summary=_summary(condensed).summary,
        key_ideas=_key_ideas(condensed).ideas,
    )


def format_timestamp(seconds: float) -> str:
    """Секунды -> mm:ss, а для часовых видео hh:mm:ss."""
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _clean_timestamp(value: object) -> str | None:
    """Оставляем таймкод только если он похож на mm:ss или hh:mm:ss, иначе None."""
    if value is None:
        return None
    text = str(value).strip().strip("[]")
    m = _TIMESTAMP_RE.match(text)
    if not m:
        return None
    h, mm, ss = m.group(1), m.group(2), m.group(3)
    return f"{int(h):02d}:{mm}:{ss}" if h else f"{int(mm):02d}:{ss}"


def format_timed_text(cues: list[Cue]) -> str:
    """Склеиваем реплики в текст, начиная каждую строку с метки [mm:ss]."""
    return "\n".join(f"[{format_timestamp(c.start)}] {c.text}" for c in cues if c.text.strip())


def _cue_groups(cues: list[Cue], size: int) -> list[list[Cue]]:
    """Группируем реплики так, чтобы текст каждой группы укладывался в size символов."""
    groups: list[list[Cue]] = []
    current: list[Cue] = []
    length = 0
    for c in cues:
        if not c.text.strip():
            continue
        if current and length + len(c.text) > size:
            groups.append(current)
            current, length = [], 0
        current.append(c)
        length += len(c.text)
    if current:
        groups.append(current)
    return groups


def summarize_timed(cues: list[Cue]) -> tuple[str, list[SummaryPoint]]:
    """Резюме + ключевые пункты с таймкодами.

    Длинный транскрипт обрабатываем по частям, иначе он не влезет в контекст дешёвой модели.
    Таймкоды остаются верными, потому что группы нарезаны по исходным репликам.
    """
    groups = _cue_groups(cues, settings.llm_chunk_chars)
    if not groups:
        return "", []
    summaries: list[str] = []
    points: list[SummaryPoint] = []
    for group in groups:
        summary, group_points = _summarize_group(format_timed_text(group))
        summaries.append(summary)
        points.extend(group_points)
    return "\n\n".join(s for s in summaries if s), points


def _summarize_group(text: str) -> tuple[str, list[SummaryPoint]]:
    data = get_llm().complete_json(PROMPT_SUMMARIZE.format(text=text))
    points = [
        SummaryPoint(text=str(p.get("text", "")), timestamp=_clean_timestamp(p.get("timestamp")))
        for p in data.get("key_points", [])
        if str(p.get("text", "")).strip()
    ]
    return str(data.get("summary", "")), points
