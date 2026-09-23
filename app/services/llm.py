"""Клиент к ИИ-модели. Любой OpenAI-совместимый API — провайдер меняется через .env."""
import json
import logging
import re
from typing import Any

from openai import OpenAI

from app.config import settings

log = logging.getLogger(__name__)

# Готовые base_url для популярных провайдеров; для "custom" берётся LLM_BASE_URL как есть.
PROVIDER_URLS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "inception": "https://api.inceptionlabs.ai/v1",
    "ollama": "http://localhost:11434/v1",
    # Локальный бесплатный шлюз (Ametist298/deepseek-free-api): запускается рядом и
    # проксирует DeepSeek в формате OpenAI. Ключ не нужен, но нужна браузерная сессия DeepSeek.
    "deepseek-free": "http://localhost:18632/v1",
}

SYSTEM_PROMPT = (
    "Ты аналитик видео-контента. Отвечай на языке транскрипта (если он русский — по-русски). "
    "Пиши кратко, по делу, без вступлений."
)


class LLMClient:
    def __init__(self) -> None:
        base_url = settings.llm_base_url
        if settings.llm_provider in PROVIDER_URLS and base_url == PROVIDER_URLS["openai"]:
            base_url = PROVIDER_URLS[settings.llm_provider]
        # у Ollama ключ не нужен, но библиотека требует непустую строку
        api_key = settings.llm_api_key or "ollama"
        self.model = settings.llm_model
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=settings.llm_timeout)
        log.info("LLM: provider=%s model=%s base_url=%s", settings.llm_provider, self.model, base_url)

    def complete(self, prompt: str, *, json_mode: bool = False) -> str:
        kwargs: dict[str, Any] = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            **kwargs,
        )
        choice = resp.choices[0]
        content = (choice.message.content or "").strip()
        usage = resp.usage
        if usage:
            log.info("LLM tokens: prompt=%s completion=%s finish=%s", usage.prompt_tokens,
                     usage.completion_tokens, choice.finish_reason)
        if not content:
            raise RuntimeError(f"Модель вернула пустой ответ (finish_reason={choice.finish_reason}); "
                               "попробуйте увеличить LLM_MAX_TOKENS")
        if choice.finish_reason == "length":
            log.warning("Ответ обрезан по LLM_MAX_TOKENS=%s", settings.llm_max_tokens)
        return content

    def complete_json(self, prompt: str) -> dict:
        """Просим JSON; если провайдер не поддерживает json_mode — парсим вручную."""
        try:
            raw = self.complete(prompt, json_mode=True)
        except Exception as e:  # noqa: BLE001 — не все провайдеры знают response_format
            log.warning("json_mode не поддержан (%s), повтор без него", e)
            raw = self.complete(prompt)
        return _parse_json(raw)


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not m:
            raise ValueError(f"Модель вернула не JSON: {raw[:200]}") from None
        return json.loads(m.group(0))


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
