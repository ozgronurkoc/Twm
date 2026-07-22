"""app/core/evaluation.py
=========================
Pipeline adım 2-3: Memory Evaluation + Classification.

PDF: "Hafıza yaratmadan önce AI içsel olarak sorar: önemli mi? geçici mi? zaten
biliniyor mu? gelecekte yararlı mı? mevcut hafızayı ezmeli mi? göreve mi
dönüşmeli? yok mu sayılmalı?" — ancak bu muhakemeden sonra hafıza üretilir.

Çıktı: yapılandırılmış `EvaluationResult`. Ham mesaj ASLA kopyalanmaz; her
hafıza kalıcı, üçüncü-tekil bir bilgi cümlesine dönüştürülür ve TAM olarak bir
kategoriye (identity/preference/episode/knowledge) atanır.
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from app.core.enums import MemoryType
from app.core.prompts import SYSTEM_PROMPT_EVAL
from app.domain.llm import LLMProvider

logger = logging.getLogger(__name__)

# Evaluation yalnızca bu 4 kategoriyi üretebilir (task -> intent; reflection -> otomatik).
_EVAL_TYPES = ["identity", "preference", "episode", "knowledge"]


class EvaluatedMemory(BaseModel):
    type: MemoryType
    title: str
    content: str
    summary: Optional[str] = None
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    is_persistent: bool = False
    should_overwrite: bool = False


class EvaluationResult(BaseModel):
    # 7 sorunun izlenebilir cevapları (OpenClaw şeffaflık ilkesi).
    is_important: bool = False
    is_temporary: bool = False
    already_known: bool = False
    useful_future: bool = False
    should_be_task: bool = False
    reasoning: Optional[str] = None
    memories: list[EvaluatedMemory] = Field(default_factory=list)


_TOOL_SCHEMA = {
    "description": "Mesajdan çıkarılan kalıcı hafıza değerlendirmesi.",
    "parameters": {
        "type": "object",
        "properties": {
            "is_important": {"type": "boolean"},
            "is_temporary": {"type": "boolean"},
            "already_known": {"type": "boolean"},
            "useful_future": {"type": "boolean"},
            "should_be_task": {"type": "boolean", "description": "Bir görev olarak ele alınmalıysa true (hafıza üretme)."},
            "reasoning": {"type": "string", "description": "Kısa gerekçe."},
            "memories": {
                "type": "array",
                "description": "Üretilecek kalıcı hafızalar. Sohbet/geçici/zaten bilinen ise BOŞ.",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": _EVAL_TYPES},
                        "title": {"type": "string"},
                        "content": {"type": "string", "description": "Kalıcı, üçüncü-tekil bilgi cümlesi (ham mesaj değil)."},
                        "summary": {"type": "string"},
                        "importance": {"type": "number"},
                        "confidence": {"type": "number"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "is_persistent": {"type": "boolean"},
                        "should_overwrite": {"type": "boolean"},
                    },
                    "required": ["type", "title", "content"],
                },
            },
        },
        "required": ["memories"],
    },
}


def evaluate(
    llm: LLMProvider, message: str, known_context: Optional[str] = None
) -> EvaluationResult:
    user_content = message
    if known_context:
        user_content = f"[BİLİNEN BAĞLAM]\n{known_context}\n\n[YENİ MESAJ]\n{message}"

    raw = llm.complete_structured(
        system=SYSTEM_PROMPT_EVAL,
        messages=[{"role": "user", "content": user_content}],
        tool_schema=_TOOL_SCHEMA,
        tool_name="hafiza_degerlendir",
    )
    if not raw:
        return EvaluationResult()
    try:
        return EvaluationResult.model_validate(raw)
    except (ValidationError, ValueError) as exc:
        logger.warning("Evaluation doğrulanamadı (%s): %s", exc, raw)
        return EvaluationResult()
