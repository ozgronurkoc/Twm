"""app/domain/repositories.py
=============================
Kalıcılık PORT'u (arayüz).

PDF: "Tüm depolama implementasyonları repository arayüzlerinin arkasında
gizli kalmalı. İş mantığı belirli bir DB'ye bağımlı olmamalı."

core/ katmanı yalnızca bu soyut sınıfı bilir; PostgresMemoryRepository ya da
SqliteMemoryRepository gibi somut sınıfları değil. Sağlayıcı değiştirmek =
yalnızca infra katmanında yeni bir implementasyon.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Sequence
from uuid import UUID

from app.core.enums import MemoryType
from app.core.models import Memory


class MemoryRepository(ABC):
    """Hafıza kayıtlarının CRUD + basit sorgu arayüzü.

    Not: Semantik (vektör) arama ayrı bir port'tur (VectorStore). Bu repository
    ilişkisel/metadata tarafından sorumludur.
    """

    @abstractmethod
    def add(self, memory: Memory) -> Memory:
        ...

    @abstractmethod
    def get(self, memory_id: UUID) -> Optional[Memory]:
        ...

    @abstractmethod
    def update(self, memory: Memory) -> Memory:
        ...

    @abstractmethod
    def delete(self, memory_id: UUID, *, hard: bool = False) -> None:
        """hard=False -> status=deleted (yumuşak). hard=True -> fiziksel silme."""
        ...

    @abstractmethod
    def list_by_type(
        self,
        mem_type: MemoryType,
        *,
        only_active: bool = True,
        persistent_only: bool = False,
        limit: Optional[int] = None,
    ) -> Sequence[Memory]:
        """Bir katmanın kayıtlarını döner (ör. tüm Identity/Preference)."""
        ...

    @abstractmethod
    def get_by_ids(self, ids: Sequence[UUID]) -> Sequence[Memory]:
        """Graf gezinme / related_memory_ids çözümü için toplu getirme."""
        ...

    @abstractmethod
    def list_created_between(
        self,
        mem_type: MemoryType,
        start: datetime,
        end: datetime,
        *,
        only_active: bool = True,
        tag: Optional[str] = None,
    ) -> Sequence[Memory]:
        """Bir zaman penceresinde oluşturulmuş kayıtlar (reflection için).

        `tag` verilirse yalnızca o etiketi taşıyanlar döner — böylece haftalık
        özet üretirken yalnızca GÜNLÜK özetler kaynak alınabilir.
        """
        ...

    @abstractmethod
    def list_decay_candidates(
        self,
        *,
        max_importance: float,
        max_access_count: int,
        not_accessed_before: datetime,
        exclude_types: Sequence[MemoryType] = (),
        limit: Optional[int] = None,
    ) -> Sequence[Memory]:
        """Unutma (forgetting) adayları: düşük önem + düşük erişim + eskimiş."""
        ...

    @abstractmethod
    def list_archived_before(
        self, cutoff: datetime, *, exclude_types: Sequence[MemoryType] = ()
    ) -> Sequence[Memory]:
        """Saklama süresi dolmuş arşiv kayıtları (kalıcı silme adayları)."""
        ...

    @abstractmethod
    def health_check(self) -> bool:
        ...
