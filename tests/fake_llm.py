"""tests/fake_llm.py
====================
Scriptli sahte LLMProvider. tool_name'e göre önceden hazırlanmış yanıtları
döner; gerçek OpenAI çağrısı yapmaz. Böylece pipeline deterministik test edilir.
"""
from __future__ import annotations

from collections import deque

from app.domain.llm import LLMProvider


class ScriptedLLM(LLMProvider):
    def __init__(self, intents=None, evals=None):
        # her biri dict kuyruğu
        self._intents = deque(intents or [])
        self._evals = deque(evals or [])

    def complete(self, *, system, messages, temperature=0.0):
        return "ok"

    def complete_structured(self, *, system, messages, tool_schema, tool_name, temperature=0.0):
        if tool_name == "analiz_et":
            return self._intents.popleft() if self._intents else {
                "action": "chat", "yanit": "peki"
            }
        if tool_name == "hafiza_degerlendir":
            return self._evals.popleft() if self._evals else {"memories": []}
        return None
