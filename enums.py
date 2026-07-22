"""app/core/enums.py
====================
Hafıza sistemi genelinde kullanılan sabit değer kümeleri.

PDF kuralı: her hafıza TAM OLARAK bir kategoriye aittir (kategorisiz kayıt yasak).
Bu enum'lar hem uygulama katmanında hem de veritabanı seviyesinde (CHECK / enum)
bu kuralı zorlamak için kullanılır.
"""
from __future__ import annotations

from enum import Enum


class MemoryType(str, Enum):
    """PDF'teki 6 bağımsız katman."""

    IDENTITY = "identity"      # Layer 1 — neredeyse hiç değişmez, en yüksek önem
    PREFERENCE = "preference"  # Layer 2 — asistan nasıl davranmalı; yeni olan eskiyi ezer
    EPISODE = "episode"        # Layer 3 — önemli olaylar (konuşma değil)
    KNOWLEDGE = "knowledge"    # Layer 4 — kullanıcıya ait bilgi (RAG)
    TASK = "task"              # Layer 5 — operasyonel; backend'i Notion
    REFLECTION = "reflection"  # Layer 6 — otomatik üretilen konsolidasyon özetleri


class MemoryStatus(str, Enum):
    """Bir hafızanın yaşam döngüsü (forgetting sistemi bunu kullanır)."""

    ACTIVE = "active"
    ARCHIVED = "archived"      # düşük önem + düşük erişim -> önce buraya
    SUPERSEDED = "superseded"  # çakışma çözümünde eskisi bununla işaretlenir
    DELETED = "deleted"        # saklama süresi dolunca (Identity neredeyse hiç)


class ReflectionLevel(str, Enum):
    """Konsolidasyon seviyeleri (PDF: günlük -> haftalık -> aylık -> çeyreklik)."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"

    @property
    def source_level(self) -> "ReflectionLevel | None":
        """Bu seviyenin beslendiği bir alt seviye (DAILY doğrudan episode'lardan)."""
        return {
            ReflectionLevel.WEEKLY: ReflectionLevel.DAILY,
            ReflectionLevel.MONTHLY: ReflectionLevel.WEEKLY,
            ReflectionLevel.QUARTERLY: ReflectionLevel.MONTHLY,
        }.get(self)

    @property
    def window_days(self) -> int:
        return {
            ReflectionLevel.DAILY: 1,
            ReflectionLevel.WEEKLY: 7,
            ReflectionLevel.MONTHLY: 30,
            ReflectionLevel.QUARTERLY: 90,
        }[self]


# Reflection kayıtlarında seviyeyi işaretleyen etiket ön eki.
REFLECTION_TAG_PREFIX = "yansima:"


def reflection_tag(level: ReflectionLevel) -> str:
    return f"{REFLECTION_TAG_PREFIX}{level.value}"


def reflection_level_of(tags: list[str]) -> "ReflectionLevel | None":
    for t in tags or []:
        if t.startswith(REFLECTION_TAG_PREFIX):
            try:
                return ReflectionLevel(t[len(REFLECTION_TAG_PREFIX):])
            except ValueError:
                return None
    return None


# Semantik getirme YAPILMAYAN, her zaman deterministik yüklenen katmanlar.
# (PDF: kalıcı bilgi ile deneyimi ayır.)
ALWAYS_LOADED_TYPES = frozenset({MemoryType.IDENTITY, MemoryType.PREFERENCE})

# Layer 5 backend'i Notion olduğu için pgvector tablosu OLMAYAN tip.
NON_VECTOR_TYPES = frozenset({MemoryType.TASK})
