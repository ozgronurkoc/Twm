"""app/core/executor.py
=======================
Arka plan iş yürütücüsü soyutlaması.

PDF: hafıza değerlendirmesi her mesajda çalışır ama yanıt gecikmesini artırmamalı.
Bu yüzden değerlendirme yanıt yolundan AYRILIR ve bir executor'a submit edilir.

- InlineExecutor: hemen çalıştırır (test/dev; deterministik).
- ThreadExecutor:  ayrı thread'de çalıştırır (production'da yanıtı bloklamaz).

Faz 6'da bu, kalıcı bir kuyruğa (Redis/Celery) çıkarılabilir — arayüz aynı kalır.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

logger = logging.getLogger(__name__)


class BackgroundExecutor(ABC):
    @abstractmethod
    def submit(self, fn: Callable[[], None]) -> None:
        ...


class InlineExecutor(BackgroundExecutor):
    def submit(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            logger.exception("Inline arka plan işi başarısız.")


class ThreadExecutor(BackgroundExecutor):
    def __init__(self, max_workers: int = 2) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers)

    def submit(self, fn: Callable[[], None]) -> None:
        def _wrapped():
            try:
                fn()
            except Exception:
                logger.exception("Arka plan işi başarısız.")

        self._pool.submit(_wrapped)
