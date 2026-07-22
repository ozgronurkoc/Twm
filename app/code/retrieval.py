"""app/core/retrieval.py
========================
Pipeline getirme (retrieval) mantığı.

PDF: yeni mesaj gelince -> embedding üret -> vektör DB'de ara -> top adayları al
-> semantik benzerliğe göre sırala -> METADATA ile re-rank (importance, recency,
frequency, confidence). Yalnızca en iyileri enjekte et; asla tüm DB'yi değil.

Bu modül saftır (provider bilmez): bir VectorStore + repo'dan gelen adayları
alıp yeniden sıralar.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.models import Memory


@dataclass(frozen=True)
class RetrievalConfig:
    # katman başına vektör aramada çekilecek aday sayısı
    k_per_type: int = 8
    # enjeksiyonda katman başına tutulacak nihai sayı (minimum bağlam ilkesi)
    max_knowledge: int = 4
    max_episodes: int = 3
    max_reflections: int = 2
    # re-rank ağırlıkları (toplam ~1.0)
    w_similarity: float = 0.55
    w_importance: float = 0.20
    w_recency: float = 0.10
    w_frequency: float = 0.05
    w_confidence: float = 0.10
    # recency yarı-ömrü (gün): bu kadar günde recency skoru yarıya iner
    recency_halflife_days: float = 30.0
    # frequency doygunluğu: bu erişim sayısında 1.0'a ulaşır
    frequency_saturation: int = 10
    # ALAKA EŞİKLERİ — PDF: "yalnızca EN İYİ hafızaları enjekte et".
    # Eşiğin altındaki adaylar, sayı kotası dolmasa bile bağlama GİRMEZ.
    # Böylece alakasız hafızalar bağlamı kirletmez ve token israfı olmaz.
    min_similarity: float = 0.55   # ham semantik benzerlik alt sınırı
    min_final_score: float = 0.40  # re-rank sonrası nihai skor alt sınırı


@dataclass
class RankedMemory:
    memory: Memory
    similarity: float
    recency: float
    frequency: float
    final_score: float


def _recency_score(m: Memory, *, now: datetime, halflife_days: float) -> float:
    ref = m.last_accessed or m.created_at
    if ref is None:
        return 0.0
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    age_days = max((now - ref).total_seconds() / 86400.0, 0.0)
    return 0.5 ** (age_days / max(halflife_days, 0.1))


def _frequency_score(m: Memory, *, saturation: int) -> float:
    return min(m.access_count / max(saturation, 1), 1.0)


def rerank(
    candidates: list[tuple[Memory, float]],
    *,
    cfg: RetrievalConfig,
    now: datetime | None = None,
) -> list[RankedMemory]:
    """candidates: (memory, ham_semantik_benzerlik) listesi. Metadata ile
    yeniden sıralanmış RankedMemory listesi döner (yüksek -> düşük)."""
    now = now or datetime.now(timezone.utc)
    ranked: list[RankedMemory] = []
    for memory, sim in candidates:
        rec = _recency_score(memory, now=now, halflife_days=cfg.recency_halflife_days)
        freq = _frequency_score(memory, saturation=cfg.frequency_saturation)
        final = (
            cfg.w_similarity * sim
            + cfg.w_importance * memory.importance
            + cfg.w_recency * rec
            + cfg.w_frequency * freq
            + cfg.w_confidence * memory.confidence
        )
        ranked.append(RankedMemory(memory, sim, rec, freq, final))
    ranked.sort(key=lambda r: r.final_score, reverse=True)
    return ranked


@dataclass
class RetrievedContext:
    """Öncelik sırasına hazır, katmanlara ayrılmış bağlam."""

    identity: list[Memory] = field(default_factory=list)
    preferences: list[Memory] = field(default_factory=list)
    persistent: list[Memory] = field(default_factory=list)   # her zaman yüklenen knowledge
    active_tasks: list = field(default_factory=list)          # list[Task]
    knowledge: list[RankedMemory] = field(default_factory=list)
    episodes: list[RankedMemory] = field(default_factory=list)
    reflections: list[RankedMemory] = field(default_factory=list)
    # Graf gezinmesiyle bulunan, semantik aramanın doğrudan getiremediği
    # ama ilişkili olan hafızalar (PDF Faz 7).
    graph_related: list = field(default_factory=list)   # list[GraphNeighbor]

    def injected_memories(self) -> list[Memory]:
        """Erişim istatistiği güncellenecek tüm hafızalar (task hariç)."""
        out = list(self.identity) + list(self.preferences) + list(self.persistent)
        out += [r.memory for r in self.knowledge + self.episodes + self.reflections]
        out += [n.memory for n in self.graph_related]
        # id bazında tekilleştir
        seen, uniq = set(), []
        for m in out:
            if m.id not in seen:
                seen.add(m.id)
                uniq.append(m)
        return uniq
