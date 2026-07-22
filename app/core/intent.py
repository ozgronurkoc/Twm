"""app/core/intent.py
=====================
Pipeline adım 1: Intent Detection.

Kullanıcının serbest mesajını yapısal bir `IntentResult`'a çevirir. Mevcut
ai_service.py'nin görev-yorumlama davranışını, LLMProvider arayüzü arkasında
yeniden üretir (provider-bağımsız).
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ValidationError, field_validator, model_validator

from app.core.prompts import SYSTEM_PROMPT_INTENT
from app.domain.llm import LLMProvider

logger = logging.getLogger(__name__)


class Action(str, Enum):
    ADD = "task_add"
    COMPLETE = "task_complete"
    CANCEL = "task_cancel"
    NOTE = "task_note"
    LIST = "task_list"
    CHAT = "chat"


_TARIH = ["bugün", "yarın"]
_ONCELIK = ["🔴 Yüksek", "🟡 Orta", "🟢 Düşük"]
_KATEGORI = ["💼 İş", "🏠 Ev", "❤️ Sağlık", "👤 Kişisel", "📌 Diğer"]
_KAPSAM = ["bugün", "yarın", "hafta", "tümü", "gecikmiş"]

_GOREV_GEREKLI = {Action.ADD, Action.COMPLETE, Action.CANCEL, Action.NOTE}


class IntentResult(BaseModel):
    action: Action
    gorev_metni: Optional[str] = None
    not_metni: Optional[str] = None
    tarih: str = "bugün"
    oncelik: str = "🟡 Orta"
    kategori: str = "📌 Diğer"
    kapsam: str = "bugün"
    yanit: str = "Tamamdır."

    @field_validator("gorev_metni", "not_metni", mode="before")
    @classmethod
    def _strip_optional(cls, v):
        if v is None:
            return None
        v = " ".join(str(v).split())
        return v or None

    @field_validator("yanit", mode="before")
    @classmethod
    def _strip_required(cls, v):
        # `yanit` zorunlu bir string; boş gelse bile None'a düşmemeli
        # (aksi halde IntentResult(yanit="") doğrulama hatası verir).
        if v is None:
            return ""
        return " ".join(str(v).split())

    @model_validator(mode="after")
    def _check(self):
        if self.action in _GOREV_GEREKLI and not self.gorev_metni:
            raise ValueError(f"{self.action.value} için gorev_metni gerekli.")
        if self.action is Action.NOTE and not self.not_metni:
            raise ValueError("task_note için not_metni gerekli.")
        return self


_TOOL_SCHEMA = {
    "description": "Kullanıcı mesajından çıkarılan görev/sohbet niyeti.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": [a.value for a in Action]},
            "gorev_metni": {"type": "string", "description": "Normalize edilmiş görev (edilgen/gelecek, küçük harf)."},
            "not_metni": {"type": "string", "description": "Yalnızca task_note'ta: not içeriği."},
            "tarih": {"type": "string", "enum": _TARIH},
            "oncelik": {"type": "string", "enum": _ONCELIK},
            "kategori": {"type": "string", "enum": _KATEGORI},
            "kapsam": {"type": "string", "enum": _KAPSAM},
            "yanit": {"type": "string", "description": "Kullanıcıya doğal, sıcak Türkçe cevap."},
        },
        "required": ["action", "yanit"],
    },
}


def detect_intent(
    llm: LLMProvider,
    message: str,
    history: Optional[list[dict]] = None,
    context: Optional[str] = None,
) -> IntentResult:
    system = SYSTEM_PROMPT_INTENT
    if context:
        # Hatırlanan bağlamı system prompt'a ekle -> kişiselleştirilmiş yanıt.
        system = f"{SYSTEM_PROMPT_INTENT}\n\n# HATIRLANAN BAĞLAM\n{context}"
    messages = list(history or [])
    messages.append({"role": "user", "content": message})
    raw = llm.complete_structured(
        system=system,
        messages=messages,
        tool_schema=_TOOL_SCHEMA,
        tool_name="analiz_et",
    )
    if not raw:
        return IntentResult(action=Action.CHAT, yanit="Tam anlayamadım, biraz açar mısın?")
    try:
        return IntentResult.model_validate(raw)
    except (ValidationError, ValueError) as exc:
        logger.warning("Intent doğrulanamadı (%s): %s", exc, raw)
        return IntentResult(action=Action.CHAT, yanit="Tam anlayamadım, biraz açar mısın?")
