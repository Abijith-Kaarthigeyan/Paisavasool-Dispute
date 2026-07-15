"""Shared LLM client: Direct Gemini → Groq → Groq API 2 → OpenRouter fallback chain."""

import time
from dataclasses import dataclass
from typing import Any

import httpx

from src.core.config.settings import settings
from src.observability.logging.logger import logger

OPENROUTER_MODELS = (
    "google/gemini-2.5-flash",
    "deepseek/deepseek-chat",
    "qwen/qwen-2.5-72b-instruct",
)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


@dataclass(frozen=True)
class LLMCompletionResult:
    content: str
    latency: float
    model: str
    provider: str


def _key_usable(key: str | None) -> bool:
    return bool(key and not key.startswith("mock-") and key.strip())


async def _try_gemini(
    *,
    prompt: str,
    temperature: float,
    json_mode: bool,
    agent_label: str,
    timeout: float,
) -> LLMCompletionResult | None:
    gemini_key = settings.GEMINI_API_KEY.strip()
    model = settings.GEMINI_MODEL_NAME
    start_time = time.time()
    try:
        logger.info("Attempting %s with Direct Gemini model: %s", agent_label, model)
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={gemini_key}"
        )
        generation_config: dict[str, Any] = {"temperature": temperature}
        if json_mode:
            generation_config["responseMimeType"] = "application/json"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=timeout)
            resp.raise_for_status()
            result_json = resp.json()
            content = result_json["candidates"][0]["content"]["parts"][0]["text"]

        latency = time.time() - start_time
        logger.info("%s succeeded with Direct Gemini model: %s", agent_label, model)
        return LLMCompletionResult(
            content=content,
            latency=latency,
            model=model,
            provider="Direct Gemini",
        )
    except Exception as e:
        logger.warning("Direct Gemini %s failed: %s", agent_label, str(e))
        return None


def _iter_groq_api_keys() -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    if _key_usable(settings.GROQ_API_KEY):
        keys.append((settings.GROQ_API_KEY.strip(), "Groq"))
    if _key_usable(settings.GROQ_API_KEY_2):
        keys.append((settings.GROQ_API_KEY_2.strip(), "Groq API 2"))
    return keys


async def _try_groq(
    *,
    prompt: str,
    temperature: float,
    json_mode: bool,
    agent_label: str,
    timeout: float,
    api_key: str,
    provider: str,
) -> LLMCompletionResult | None:
    model = settings.GROQ_MODEL_NAME
    start_time = time.time()
    try:
        logger.info("Attempting %s with %s model: %s", agent_label, provider, model)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                GROQ_API_URL,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            result_json = resp.json()
            content = result_json["choices"][0]["message"]["content"]

        latency = time.time() - start_time
        logger.info("%s succeeded with %s model: %s", agent_label, provider, model)
        return LLMCompletionResult(
            content=content,
            latency=latency,
            model=model,
            provider=provider,
        )
    except Exception as e:
        logger.warning(
            "%s %s failed with model %s: %s", provider, agent_label, model, str(e)
        )
        return None


async def _try_openrouter(
    *,
    prompt: str,
    temperature: float,
    json_mode: bool,
    agent_label: str,
    model: str,
    timeout: float,
) -> LLMCompletionResult | None:
    openrouter_key = settings.OPENROUTER_API_KEY.strip()
    start_time = time.time()
    try:
        logger.info("Attempting %s with OpenRouter model: %s", agent_label, model)
        headers = {
            "Authorization": f"Bearer {openrouter_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                OPENROUTER_API_URL,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            result_json = resp.json()
            content = result_json["choices"][0]["message"]["content"]

        latency = time.time() - start_time
        logger.info("%s succeeded with OpenRouter model: %s", agent_label, model)
        return LLMCompletionResult(
            content=content,
            latency=latency,
            model=model,
            provider="OpenRouter",
        )
    except Exception as e:
        logger.warning(
            "OpenRouter %s failed with model %s: %s", agent_label, model, str(e)
        )
        return None


async def generate_text_completion(
    *,
    prompt: str,
    temperature: float = 0.0,
    json_mode: bool = True,
    agent_label: str = "LLM request",
    timeout: float = 15.0,
) -> LLMCompletionResult | None:
    """Run prompt through Gemini → Groq → Groq API 2 → OpenRouter. Returns None if all providers fail."""
    if _key_usable(settings.GEMINI_API_KEY):
        result = await _try_gemini(
            prompt=prompt,
            temperature=temperature,
            json_mode=json_mode,
            agent_label=agent_label,
            timeout=timeout,
        )
        if result:
            return result

    for groq_key, provider in _iter_groq_api_keys():
        result = await _try_groq(
            prompt=prompt,
            temperature=temperature,
            json_mode=json_mode,
            agent_label=agent_label,
            timeout=timeout,
            api_key=groq_key,
            provider=provider,
        )
        if result:
            return result

    if _key_usable(settings.OPENROUTER_API_KEY):
        for model in OPENROUTER_MODELS:
            result = await _try_openrouter(
                prompt=prompt,
                temperature=temperature,
                json_mode=json_mode,
                agent_label=agent_label,
                model=model,
                timeout=timeout,
            )
            if result:
                return result

    return None
