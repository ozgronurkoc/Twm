"""app/infra/llm/openai_llm.py
==============================
LLMProvider'ın OpenAI implementasyonu.

DÜZELTME (gpt-5.6-luna desteği):
--------------------------------
gpt-5.6 ailesi (Sol/Terra/Luna) bir REASONING model ailesidir. İki önemli
sonucu var:

1. Function calling (tool use) bu ailede yalnızca `/v1/responses` (Responses
   API) üzerinden destekleniyor; eski `/v1/chat/completions` (Chat Completions
   API) ile birlikte kullanılınca "Function tools with reasoning_effort are
   not supported ... use /v1/responses" hatası dönüyor. Bu yüzden bu dosya
   artık `client.chat.completions.create()` yerine `client.responses.create()`
   kullanıyor.

2. Reasoning modelleri `temperature` parametresini desteklemiyor (yalnızca
   varsayılan olan 1 kabul ediliyor, başka değer 400 hatası veriyor). Bu
   yüzden reasoning model tespit edilirse `temperature` hiç gönderilmiyor;
   bunun yerine (isteğe bağlı) `reasoning.effort` gönderiliyor.

Bu iki değişiklik dışında dış arayüz (LLMProvider) ve davranış aynı kalıyor;
`intent.py` ve `evaluation.py` gibi çağıran kodların hiçbiri değişmedi.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from openai import OpenAI, OpenAIError

from app.domain.llm import LLMProvider

logger = logging.getLogger(__name__)

# Reasoning model aileleri: bunlarda temperature YOK, tool calling Responses
# API üzerinden çalışır. gpt-5.6-luna bu kapsamda; ileride yeni reasoning
# modelleri eklenirse buraya önek eklemek yeterli.
_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _is_reasoning_model(model: str) -> bool:
    m = model.lower()
    return any(m.startswith(p) for p in _REASONING_MODEL_PREFIXES)


class OpenAILLMProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-5.6-luna",
        timeout: float = 30.0,
        max_retries: int = 3,
        reasoning_effort: str = "minimal",
        client: OpenAI | None = None,
    ) -> None:
        self._model = model
        self._is_reasoning = _is_reasoning_model(model)
        # Yalnızca reasoning modellerinde anlamlı; diğerlerinde yok sayılır.
        self._reasoning_effort = reasoning_effort
        self._client = client or OpenAI(
            api_key=api_key, timeout=timeout, max_retries=max_retries
        )

    # ------------------------------------------------------------------ #
    # Ortak yardımcı: reasoning modeline göre temperature/reasoning ekle.
    # ------------------------------------------------------------------ #
    def _base_kwargs(self, *, temperature: float) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": self._model}
        if self._is_reasoning:
            # Reasoning modelleri temperature'ı reddeder (yalnızca varsayılan
            # 1 kabul edilir). Bunun yerine reasoning effort ayarlanabilir.
            if self._reasoning_effort:
                kwargs["reasoning"] = {"effort": self._reasoning_effort}
        else:
            kwargs["temperature"] = temperature
        return kwargs

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
    ) -> str:
        kwargs = self._base_kwargs(temperature=temperature)
        kwargs["instructions"] = system
        kwargs["input"] = messages
        try:
            resp = self._client.responses.create(**kwargs)
        except OpenAIError:
            logger.exception("OpenAI complete() başarısız.")
            raise
        return resp.output_text or ""

    def complete_structured(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        tool_schema: dict[str, Any],
        tool_name: str,
        temperature: float = 0.0,
    ) -> Optional[dict[str, Any]]:
        # Responses API'de tool tanımı DÜZ (Chat Completions'taki gibi
        # "function" anahtarı altında iç içe değil): {"type": "function",
        # "name": ..., "description": ..., "parameters": ...}
        tools = [{"type": "function", "name": tool_name, **tool_schema}]

        kwargs = self._base_kwargs(temperature=temperature)
        kwargs.update(
            instructions=system,
            input=messages,
            tools=tools,
            # Belirli bir fonksiyonu zorla çağırt (Chat Completions'taki
            # {"type": "function", "function": {"name": ...}} değil, düz hali).
            tool_choice={"type": "function", "name": tool_name},
        )

        try:
            resp = self._client.responses.create(**kwargs)
        except OpenAIError:
            logger.exception("OpenAI complete_structured() başarısız.")
            raise

        # response.output bir liste: reasoning item'ları ve function_call
        # item'ları içerebilir. İstediğimiz fonksiyon çağrısını arıyoruz.
        for item in getattr(resp, "output", []) or []:
            if getattr(item, "type", None) == "function_call" and item.name == tool_name:
                try:
                    return json.loads(item.arguments)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Yapısal argümanlar parse edilemedi.")
                    return None

        logger.warning("Yapısal yanıt (function_call) dönmedi.")
        return None
