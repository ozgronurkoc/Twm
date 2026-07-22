"""app/infra/vector/noop_store.py
=================================
Yerel dev (SQLite) için sahte vektör store. Gerçek semantik arama yapmaz;
sadece akışın kırılmadan çalışmasını sağlar. Production'da ASLA kullanılmaz.
"""
from __future__ import annotations

from typing import Sequence
from uuid import UUID

from app.core.enums import MemoryType
from app.domain.vector_store import VectorHit, VectorStore


class NoopVectorStore(VectorStore):
    def upsert(self, memory_id: UUID, mem_type: MemoryType, embedding: list[float]) -> None:
        return None

    def delete(self, memory_id: UUID, mem_type: MemoryType) -> None:
        return None

    def search(self, embedding, *, types: Sequence[MemoryType], k: int = 10) -> list[VectorHit]:
        return []

    def health_check(self) -> bool:
        return True
