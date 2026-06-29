"""Tests for shared LLM client fallback chain."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.llm.llm_client import generate_text_completion


def _chat_response(content: str) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
    }
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def _gemini_response(content: str) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": content}]}}],
    }
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


@pytest.mark.asyncio
async def test_llm_client_prefers_gemini_over_groq_and_openrouter():
    payload = json.dumps({"ok": True})
    with (
        patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=_gemini_response(payload),
        ) as mock_post,
        patch("src.core.config.settings.settings.GEMINI_API_KEY", "gemini-key"),
        patch("src.core.config.settings.settings.GROQ_API_KEY", "groq-key"),
        patch("src.core.config.settings.settings.OPENROUTER_API_KEY", "or-key"),
    ):
        result = await generate_text_completion(
            prompt="test",
            agent_label="unit test",
        )

    assert result is not None
    assert result.provider == "Direct Gemini"
    assert mock_post.call_count == 1
    assert "generativelanguage.googleapis.com" in str(mock_post.call_args[0][0])


@pytest.mark.asyncio
async def test_llm_client_falls_back_to_groq_when_gemini_fails():
    payload = json.dumps({"ok": True})

    async def side_effect(url, *args, **kwargs):
        if "generativelanguage.googleapis.com" in url:
            raise RuntimeError("gemini down")
        return _chat_response(payload)

    with (
        patch(
            "httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=side_effect
        ),
        patch("src.core.config.settings.settings.GEMINI_API_KEY", "gemini-key"),
        patch("src.core.config.settings.settings.GROQ_API_KEY", "groq-key"),
        patch("src.core.config.settings.settings.OPENROUTER_API_KEY", "or-key"),
    ):
        result = await generate_text_completion(
            prompt="test",
            agent_label="unit test",
        )

    assert result is not None
    assert result.provider == "Groq"


@pytest.mark.asyncio
async def test_llm_client_falls_back_to_openrouter_when_gemini_and_groq_fail():
    payload = json.dumps({"ok": True})

    async def side_effect(url, *args, **kwargs):
        if "generativelanguage.googleapis.com" in url:
            raise RuntimeError("gemini down")
        if "api.groq.com" in url:
            raise RuntimeError("groq down")
        return _chat_response(payload)

    with (
        patch(
            "httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=side_effect
        ),
        patch("src.core.config.settings.settings.GEMINI_API_KEY", "gemini-key"),
        patch("src.core.config.settings.settings.GROQ_API_KEY", "groq-key"),
        patch("src.core.config.settings.settings.OPENROUTER_API_KEY", "or-key"),
    ):
        result = await generate_text_completion(
            prompt="test",
            agent_label="unit test",
        )

    assert result is not None
    assert result.provider == "OpenRouter"
