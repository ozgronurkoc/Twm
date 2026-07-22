"""app/domain/vector_store.py
==============================
Vektör arama PORT'u.

PDF: pgvector / Qdrant / Pinecone / ChromaDB / Weaviate birbirinin yerine
geçebilmeli. İş mantığı hangisinin kullanıldığını bilmez.

Ayrıca PDF: "Katmanları tek vektör DB'ye karıştırmak yasak." Bu yüzden arama
her zaman `types` ile kapsamlandırılır (type-scoped); Identity ile Episode
aynı sonuç kümesinde karışmaz.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Sequence
from uuid import UUID

from app.core.enums import MemoryType


@dataclass(frozen=True)
class VectorHit:
    memory_id: UUID
    score: float          # ham semantik benzerlik (0-1); re-rank sonradan yapılır
    mem_type: MemoryType


class VectorStore(ABC):
    @abstractmethod
    def upsert(self, memory_id: UUID, mem_type: MemoryType, embedding: list[float]) -> None:
        ...

    @abstractmethod
    def delete(self, memory_id: UUID, mem_type: MemoryType) -> None:
        ...

    @abstractmethod
    def search(
        self,
        embedding: list[float],
        *,
        types: Sequence[MemoryType],
        k: int = 10,
    ) -> list[VectorHit]:
        """Verilen katman(lar) içinde en yakın k adayı döner (ham skorla)."""
        ...

    @abstractmethod
    def health_check(self) -> bool:
        ...
