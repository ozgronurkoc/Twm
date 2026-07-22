"""app/core/persistent_context.py
================================
Kalıcı Bağlam Kaynakları (Claude Projects ilhamı).

PDF: "Bazı bilgiler, mevcut konuşmadan bağımsız olarak HER ZAMAN erişilebilir
olmalı: kişisel kurallar, uzun vadeli projeler, dokümantasyon, bilgi tabanları,
prompt koleksiyonları, kalıcı talimatlar. Bunlar sıradan hafıza gibi ele
alınmamalı; konuşma hafızasından BAĞIMSIZ yüklenen kalıcı bağlam kaynakları
olmalıdır. Asistan kalıcı bilgi ile deneyimi ayırt etmelidir."

Uygulama: bu kayıtlar Knowledge katmanında `is_persistent=True` bayrağıyla
tutulur ve semantik alaka aranmadan HER ZAMAN bağlama girer. Böylece:
  - depolama tek ve tutarlı kalır (aynı 17 alanlı şema, aynı Memory Manager),
  - ama getirme davranışı deneyim hafızalarından tamamen ayrışır.
"""
from __future__ import annotations

from enum import Enum


class ContextKind(str, Enum):
    """Kalıcı bağlamın alt türleri. `tags` üzerinden işaretlenir; getirme
    davranışları aynıdır (hepsi her zaman yüklenir), ama listeleme/düzenleme
    ve gösterimde ayrıştırılabilir olmaları gerekir."""

    RULE = "kural"           # kişisel kurallar / kalıcı talimatlar
    PROJECT = "proje"        # uzun vadeli projeler
    DOC = "dokuman"          # dokümantasyon / bilgi tabanı
    PROMPT = "prompt"        # prompt koleksiyonları

    @classmethod
    def from_text(cls, value: str) -> "ContextKind":
        v = (value or "").strip().lower()
        for k in cls:
            if v == k.value or v.startswith(k.value):
                return k
        return cls.RULE


# tags içinde kalıcı bağlamı işaretleyen ön ek
PERSISTENT_TAG_PREFIX = "kalici:"


def kind_tag(kind: ContextKind) -> str:
    return f"{PERSISTENT_TAG_PREFIX}{kind.value}"


def kind_of(tags: list[str]) -> ContextKind | None:
    for t in tags or []:
        if t.startswith(PERSISTENT_TAG_PREFIX):
            return ContextKind.from_text(t[len(PERSISTENT_TAG_PREFIX):])
    return None


# Kalıcı bağlam bir "deneyim" değildir: yüksek önem, süresiz.
DEFAULT_IMPORTANCE = 0.9
DEFAULT_CONFIDENCE = 1.0   # kullanıcı doğrudan söylediği için kesin
