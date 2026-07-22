"""app/domain/llm.py
=====================
LLM sağlayıcı PORT'u.

PDF: LLM sağlayıcısı iş mantığına dokunmadan değiştirilebilmeli. Pipeline'daki
intent detection, memory evaluation, classification ve reflection üretimi bu
arayüz üzerinden konuşur.

Not: Mevcut ai_service.py'nin function-calling mantığı ileride bu arayüzün
OpenAI implementasyonuna taşınacak.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class LLMProvider(ABC):
    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
    ) -> str:
        """Serbest metin tamamlaması döner."""
        ...

    @abstractmethod
    def complete_structured(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        tool_schema: dict[str, Any],
        tool_name: str,
        temperature: float = 0.0,
    ) -> Optional[dict[str, Any]]:
        """Function-calling ile yapısal (JSON) çıktı döner.

        Evaluation/classification gibi 'AI muhakemesi' adımları bunu kullanır.
        Model yapısal yanıt vermezse None döner (çağıran 'belirsiz'e düşer).
        """
        ...
