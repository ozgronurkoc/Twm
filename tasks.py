"""app/domain/tasks.py
======================
Layer 5 (Task) için PORT.

Task katmanı diğer hafıza katmanlarından ayrıdır: backend'i Notion, kendi durum
(Yapılacak/Yapıldı/İptal), tarih, öncelik ve kategori alanları vardır. Bu yüzden
genel MemoryRepository yerine kendi arayüzü olur.

PDF: Notion Integration da Memory Manager üzerinden geçmeli. Bu arayüz o
soyutlamayı sağlar; iş mantığı Notion'ı doğrudan bilmez.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Sequence

from app.core.models import Task, TaskMatch


class TaskRepository(ABC):
    @abstractmethod
    def add(self, text: str, date_str: str, *, oncelik: str, kategori: str) -> Task:
        ...

    @abstractmethod
    def set_status(self, text: str, status: str, date_str: Optional[str] = None) -> TaskMatch:
        """status: 'Yapıldı' veya 'İptal'."""
        ...

    @abstractmethod
    def add_note(self, text: str, note: str, date_str: Optional[str] = None) -> TaskMatch:
        ...

    @abstractmethod
    def list_for_date(self, date_str: str) -> Sequence[Task]:
        ...

    @abstractmethod
    def list_overdue(self, before_date_str: str) -> Sequence[Task]:
        ...

    @abstractmethod
    def list_range(self, start_date_str: str, end_date_str: str) -> Sequence[Task]:
        ...

    @abstractmethod
    def list_all_open(self) -> Sequence[Task]:
        ...

    @abstractmethod
    def health_check(self) -> bool:
        ...
