"""app/core/dedup.py
===================
Yinelenen ve çakışan hafızaların çözümü.

PDF: "Yeni bilgi eski bir hafızayla çakışırsa: confidence karşılaştır, timestamp
karşılaştır, semantik benzerlik karşılaştır. Ardından ya mevcut hafızayı
GÜNCELLE, ya BİRLEŞTİR, ya da eskisini SUPERSEDED işaretle. Çakışan kalıcı
hafızalar asla bir arada tutulmaz."

Bu modül saf karar mantığıdır: DB/vektör bilmez. Memory Manager kararı uygular.

Karar ağacı
-----------
1. Benzerlik < near_duplicate  -> ÇAKIŞMA YOK (yeni bağımsız hafıza).
2. Benzerlik >= identical      -> neredeyse aynı içerik:
     - yeni confidence belirgin yüksekse  -> UPDATE (içerik tazelenir)
     - değilse                            -> SKIP (yeni kayıt açma, mevcudu güçlendir)
3. near_duplicate <= benzerlik < identical -> aynı konu, farklı ifade:
     - Preference katmanı ya da açık ezme sinyali -> SUPERSEDE (yeni kazanır)
     - Identity ve yeni confidence daha yüksek    -> UPDATE
     - aksi halde                                  -> MERGE (bilgiyi birleştir)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.core.enums import MemoryType
from app.core.models import Memory


class DuplicateAction(str, Enum):
    NONE = "none"            # çakışma yok, yeni hafıza olarak yaz
    SKIP = "skip"            # zaten biliniyor; yeni kayıt açma
    UPDATE = "update"        # mevcut kaydın içeriğini tazele
    MERGE = "merge"          # iki bilgiyi tek kayıtta birleştir
    SUPERSEDE = "supersede"  # eskisini superseded yap, yenisini yaz


@dataclass(frozen=True)
class DedupConfig:
    near_duplicate: float = 0.82   # bu eşiğin üstü "aynı konu"
    identical: float = 0.95        # bu eşiğin üstü "neredeyse aynı içerik"
    confidence_margin: float = 0.15  # "belirgin yüksek" farkı


@dataclass
class DuplicateVerdict:
    action: DuplicateAction
    existing: Optional[Memory] = None
    similarity: float = 0.0
    reason: str = ""
    merged_content: Optional[str] = None


def decide(
    *,
    new_type: MemoryType,
    new_content: str,
    new_confidence: float,
    existing: Optional[Memory],
    similarity: float,
    should_overwrite: bool = False,
    cfg: Optional[DedupConfig] = None,
) -> DuplicateVerdict:
    cfg = cfg or DedupConfig()

    if existing is None or similarity < cfg.near_duplicate:
        return DuplicateVerdict(DuplicateAction.NONE, None, similarity, "çakışma yok")

    # --- 1.5) Açık ezme sinyali -----------------------------------------
    # Kullanıcı bir tercihi/bilgiyi bilinçli olarak değiştirdiyse, benzerlik ne
    # kadar yüksek olursa olsun eskisi geçerliliğini yitirir. Bu, "zaten
    # biliniyor" (SKIP) yorumundan ÖNCE değerlendirilmelidir; aksi halde tercih
    # değişimi sessizce yutulur.
    if should_overwrite:
        return DuplicateVerdict(
            DuplicateAction.SUPERSEDE, existing, similarity,
            "açık ezme sinyali -> eskisi superseded",
        )

    # --- 2) Neredeyse aynı içerik --------------------------------------
    if similarity >= cfg.identical:
        # Tercihler değişkendir: içerik farklıysa yenisi kazanır.
        if new_type is MemoryType.PREFERENCE and _differs(existing.content, new_content):
            return DuplicateVerdict(
                DuplicateAction.SUPERSEDE, existing, similarity,
                "tercih değişimi -> eskisi superseded",
            )
        if new_confidence >= existing.confidence + cfg.confidence_margin:
            return DuplicateVerdict(
                DuplicateAction.UPDATE, existing, similarity,
                "aynı bilgi, yeni kayıt daha güvenilir -> güncelle",
            )
        return DuplicateVerdict(
            DuplicateAction.SKIP, existing, similarity,
            "zaten biliniyor -> yeni kayıt açma",
        )

    # --- 3) Aynı konu, farklı ifade ------------------------------------
    # Tercihler değişkendir: yenisi eskisini ezer (PDF: tekrar olmamalı).
    if new_type is MemoryType.PREFERENCE:
        return DuplicateVerdict(
            DuplicateAction.SUPERSEDE, existing, similarity,
            "tercih güncellemesi -> eskisi superseded",
        )

    # Kimlik bilgisi çelişiyorsa daha güvenilir olan kazanır.
    if new_type is MemoryType.IDENTITY:
        if new_confidence >= existing.confidence:
            return DuplicateVerdict(
                DuplicateAction.UPDATE, existing, similarity,
                "kimlik güncellemesi -> mevcut kaydı tazele",
            )
        return DuplicateVerdict(
            DuplicateAction.SKIP, existing, similarity,
            "mevcut kimlik daha güvenilir -> koru",
        )

    merged = merge_content(existing.content, new_content)
    return DuplicateVerdict(
        DuplicateAction.MERGE, existing, similarity,
        "aynı konu -> bilgileri birleştir", merged_content=merged,
    )


def _differs(a: str, b: str) -> bool:
    """İki metin anlamlı biçimde farklı mı (boşluk/büyük-küçük harf duyarsız)."""
    return " ".join((a or "").lower().split()) != " ".join((b or "").lower().split())


def merge_content(old: str, new: str) -> str:
    """İki bilgiyi tek metinde birleştirir; aynı cümleyi iki kez yazmaz."""
    old_s = (old or "").strip()
    new_s = (new or "").strip()
    if not old_s:
        return new_s
    if not new_s or new_s.lower() in old_s.lower():
        return old_s
    if old_s.lower() in new_s.lower():
        return new_s
    sep = " " if old_s.endswith((".", "!", "?")) else ". "
    return f"{old_s}{sep}{new_s}"
