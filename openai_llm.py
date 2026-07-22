"""app/infra/llm/openai_llm.py
==============================
LLMProvider'ın OpenAI (Chat Completions + function calling) implementasyonu.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from openai import OpenAI, OpenAIError

from app.domain.llm import LLMProvider

logger = logging.getLogger(__name__)


class OpenAILLMProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini",
        timeout: float = 30.0,
        max_retries: int = 3,
        client: OpenAI | None = None,
    ) -> None:
        self._model = model
        self._client = client or OpenAI(
            api_key=api_key, timeout=timeout, max_retries=max_retries
        )

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
    ) -> str:
        payload = [{"role": "system", "content": system}, *messages]
        try:
            resp = self._client.chat.completions.create(
                model=self._model, temperature=temperature, messages=payload
            )
        except OpenAIError:
            logger.exception("OpenAI complete() başarısız.")
            raise
        return resp.choices[0].message.content or ""

    def complete_structured(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        tool_schema: dict[str, Any],
        tool_name: str,
        temperature: float = 0.0,
    ) -> Optional[dict[str, Any]]:
        payload = [{"role": "system", "content": system}, *messages]
        tools = [{"type": "function", "function": {"name": tool_name, **tool_schema}}]
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                temperature=temperature,
                messages=payload,
                tools=tools,
                tool_choice={"type": "function", "function": {"name": tool_name}},
            )
        except OpenAIError:
            logger.exception("OpenAI complete_structured() başarısız.")
            raise

        tool_calls = resp.choices[0].message.tool_calls
        if not tool_calls:
            logger.warning("Yapısal yanıt dönmedi.")
            return None
        try:
            return json.loads(tool_calls[0].function.arguments)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Yapısal argümanlar parse edilemedi.")
            return None
