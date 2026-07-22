"""app/infra/vector/inmemory_store.py
=====================================
VectorStore'un bellek-içi (brute-force cosine) implementasyonu.

Postgres/pgvector kurmadan semantik akışı YEREL olarak denemek için. Katman
başına ayrı sözlük tutar (PDF: katmanları karıştırma). Production'da kullanılmaz;
kalıcı değildir ve büyük veri için verimsizdir.
"""
from __future__ import annotations

import math
from typing import Sequence
from uuid import UUID

from app.core.enums import MemoryType
from app.domain.vector_store import VectorHit, VectorStore


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class InMemoryVectorStore(VectorStore):
    def __init__(self) -> None:
        self._store: dict[MemoryType, dict[UUID, list[float]]] = {
            t: {} for t in MemoryType
        }

    def upsert(self, memory_id: UUID, mem_type: MemoryType, embedding: list[float]) -> None:
        self._store[mem_type][memory_id] = list(embedding)

    def delete(self, memory_id: UUID, mem_type: MemoryType) -> None:
        self._store[mem_type].pop(memory_id, None)

    def search(
        self, embedding, *, types: Sequence[MemoryType], k: int = 10
    ) -> list[VectorHit]:
        hits: list[VectorHit] = []
        for mem_type in types:
            for mid, vec in self._store[mem_type].items():
                score = (_cosine(embedding, vec) + 1) / 2  # 0-1 aralığına taşı
                hits.append(VectorHit(memory_id=mid, score=score, mem_type=mem_type))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def health_check(self) -> bool:
        return True
