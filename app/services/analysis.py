"""Анализ транскрипта: структура, резюме, ключевые идеи. Промпты вынесены в константы."""
import logging

from app.config import settings
from app.models import AnalysisResult, KeyIdeasResult, Section, StructureResult, SummaryResult
from app.services.llm import get_llm

log = logging.getLogger(__name__)

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
