"""app/core/graph.py
===================
Knowledge Graph — hafızalar arası ilişkiler.

PDF: "Hafızalar birbirine referans vermeli. Örnek zincir:
    Kullanıcı -> Proje -> Repo -> Teknoloji -> Hedef -> Görevler
İzole hafızalar yerine sistem kademeli olarak BAĞLI bir graf inşa etmeli.
Gelecekteki getirme, semantik aramaya EK OLARAK graf gezinmesinden yararlanmalı."

Tasarım
-------
Kenarlar `related_memory_ids` üzerinde çift yönlü (simetrik) tutulur: A→B varsa
B→A da yazılır. Böylece hangi uçtan girilirse girilsin komşuluk bulunur.

İlişki kaynakları (üçü de otomatik):
  1. Etiket örtüşmesi   — ortak `tags` sayısı eşiği aşarsa.
  2. Semantik yakınlık  — vektör benzerliği eşiği aşarsa (dedup eşiğinin ALTINDA
                          kalanlar: "aynı şey değil ama ilişkili").
  3. Türetilmişlik      — supersede ve reflection zaten kaynağa referans yazar
                          (Faz 5 ve 6'da kuruldu).

Getirmede kullanım: semantik sonuçlar "tohum" (seed) kabul edilir, graf üzerinden
1-2 hop genişletilir. Genişletmeyle gelen kayıtların skoru mesafeye göre
sönümlenir (hop_decay), böylece doğrudan alakalı olanların önüne geçemezler.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence
from uuid import UUID

from app.core.models import Memory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphConfig:
    # --- kenar kurma eşikleri ---
    min_shared_tags: int = 2        # bu kadar ortak etiket -> ilişkili say
    min_similarity: float = 0.60    # bu benzerliğin üstü -> ilişkili say
    max_similarity: float = 0.82    # bu eşiğin üstü zaten DEDUP'ın işi (aynı şey)
    max_edges_per_memory: int = 12  # graf patlamasını önle

    # --- gezinme ---
    max_hops: int = 2               # kaç adım genişletilecek
    hop_decay: float = 0.55         # her adımda skor bu oranla sönümlenir
    max_expanded: int = 6           # genişletmeyle eklenecek azami kayıt


@dataclass
class GraphNeighbor:
    memory: Memory
    hops: int
    score: float          # tohum skoru * hop_decay^hops
    via: UUID             # hangi kayıttan ulaşıldı


def shared_tag_count(a: Memory, b: Memory) -> int:
    """Ortak etiket sayısı (yansıma/kalıcılık işaret etiketleri hariç)."""
    def clean(tags: Sequence[str]) -> set[str]:
        return {t for t in (tags or []) if ":" not in t}

    return len(clean(a.tags) & clean(b.tags))


def should_link(
    a: Memory, b: Memory, *, similarity: Optional[float], cfg: GraphConfig
) -> bool:
    """İki hafıza arasında kenar kurulmalı mı?"""
    if a.id == b.id:
        return False

    if shared_tag_count(a, b) >= cfg.min_shared_tags:
        return True

    if similarity is not None and cfg.min_similarity <= similarity < cfg.max_similarity:
        return True

    return False


def link(a: Memory, b: Memory, *, cfg: GraphConfig) -> bool:
    """Çift yönlü kenar ekler. Değişiklik olduysa True döner."""
    changed = False

    if b.id not in a.related_memory_ids and len(a.related_memory_ids) < cfg.max_edges_per_memory:
        a.related_memory_ids = list(a.related_memory_ids) + [b.id]
        changed = True

    if a.id not in b.related_memory_ids and len(b.related_memory_ids) < cfg.max_edges_per_memory:
        b.related_memory_ids = list(b.related_memory_ids) + [a.id]
        changed = True

    return changed


def traverse(
    seeds: Iterable[tuple[Memory, float]],
    fetch: "callable",
    *,
    cfg: GraphConfig,
) -> list[GraphNeighbor]:
    """Tohumlardan başlayarak grafta genişler.

    seeds: (memory, skor) — semantik aramadan gelen doğrudan sonuçlar.
    fetch: id listesi -> Memory listesi (repository'den okur).

    Tohumların kendisi SONUÇTA YER ALMAZ; yalnızca genişletmeyle bulunanlar döner.
    """
    seed_list = list(seeds)
    visited: set[UUID] = {m.id for m, _ in seed_list}
    found: dict[UUID, GraphNeighbor] = {}

    # frontier: (memory, tohum_skoru, hop, via)
    frontier: list[tuple[Memory, float, int, UUID]] = [
        (m, s, 0, m.id) for m, s in seed_list
    ]

    for hop in range(1, cfg.max_hops + 1):
        next_ids: dict[UUID, tuple[float, UUID]] = {}

        for memory, base_score, _, _ in frontier:
            for rid in memory.related_memory_ids or []:
                if rid in visited:
                    continue
                # base_score frontier düğümünün ZATEN sönümlenmiş skorudur;
                # bu yüzden adım başına yalnızca BİR kez sönümlenir.
                # (hop_decay ** hop uygulamak çift sönümleme olurdu.)
                score = base_score * cfg.hop_decay
                # Aynı komşuya birden çok yoldan ulaşıldıysa en iyi skoru tut.
                prev = next_ids.get(rid)
                if prev is None or score > prev[0]:
                    next_ids[rid] = (score, memory.id)

        if not next_ids:
            break

        neighbors = fetch(list(next_ids.keys()))
        new_frontier: list[tuple[Memory, float, int, UUID]] = []

        for nb in neighbors:
            score, via = next_ids[nb.id]
            visited.add(nb.id)
            existing = found.get(nb.id)
            if existing is None or score > existing.score:
                found[nb.id] = GraphNeighbor(nb, hops=hop, score=score, via=via)
            new_frontier.append((nb, score, hop, via))

        frontier = new_frontier

    result = sorted(found.values(), key=lambda n: n.score, reverse=True)
    return result[: cfg.max_expanded]
