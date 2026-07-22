"""app/workers/jobs.py
======================
Arka plan işleri.

PDF: "Backend zamanlanmış worker'lar içermeli: embedding üretimi, reflection
üretimi, duplicate detection, memory decay, arşivleme, knowledge graph bakımı.
Bu işler kullanıcı isteklerinden BAĞIMSIZ çalışmalıdır."

Her iş saf bir fonksiyondur: bir MemoryManager alır, sonucunu raporlar. Böylece
hangi zamanlayıcının (APScheduler / cron / Celery) kullanıldığından bağımsızdır.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.core.enums import MemoryType, ReflectionLevel
from app.core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


@dataclass
class JobResult:
    name: str
    ok: bool
    detail: str = ""


def _run(name: str, fn) -> JobResult:
    try:
        detail = fn() or ""
        logger.info("İş tamamlandı: %s %s", name, detail)
        return JobResult(name, True, str(detail))
    except Exception as exc:  # worker turu asla çökmemeli
        logger.exception("İş başarısız: %s", name)
        return JobResult(name, False, str(exc))


# --------------------------------------------------------------------------- #
# 1) Embedding üretimi — eksik/boş embedding'leri tamamlar.
# --------------------------------------------------------------------------- #
def job_backfill_embeddings(manager: MemoryManager, *, limit: int = 200) -> JobResult:
    def _do() -> str:
        fixed = 0
        for mem_type in (MemoryType.IDENTITY, MemoryType.PREFERENCE,
                         MemoryType.EPISODE, MemoryType.KNOWLEDGE,
                         MemoryType.REFLECTION):
            for m in manager._repo.list_by_type(mem_type, only_active=True, limit=limit):
                text = m.summary or m.content
                if not text:
                    continue
                # Vektör index'te yoksa yeniden yaz (idempotent upsert).
                manager._vectors.upsert(m.id, m.type, manager._embedder.embed(text))
                fixed += 1
        return f"({fixed} kayıt indekslendi)"

    return _run("embedding_backfill", _do)


# --------------------------------------------------------------------------- #
# 2) Reflection üretimi — konsolidasyon zinciri.
# --------------------------------------------------------------------------- #
def job_reflection(
    manager: MemoryManager,
    level: ReflectionLevel | str = ReflectionLevel.DAILY,
    *,
    ref: Optional[datetime] = None,
) -> JobResult:
    lvl = ReflectionLevel(level) if isinstance(level, str) else level

    def _do() -> str:
        created = manager.run_reflection(lvl, ref=ref or datetime.now(timezone.utc))
        return f"({lvl.value}: {'özet üretildi' if created else 'kaynak yok'})"

    return _run(f"reflection_{lvl.value}", _do)


# --------------------------------------------------------------------------- #
# 3) Duplicate detection — aktif katmanlarda çakışma taraması.
# --------------------------------------------------------------------------- #
def job_detect_duplicates(manager: MemoryManager) -> JobResult:
    from app.core.dedup import DuplicateAction
    from app.core.models import MemoryDraft

    def _do() -> str:
        found = 0
        for mem_type in (MemoryType.PREFERENCE, MemoryType.KNOWLEDGE,
                         MemoryType.IDENTITY):
            items = list(manager._repo.list_by_type(mem_type, only_active=True))
            for m in items:
                draft = MemoryDraft(
                    type=m.type, title=m.title, content=m.content,
                    summary=m.summary, confidence=m.confidence,
                )
                verdict = manager.detect_duplicates(draft)
                # Kaydın kendisiyle eşleşmesini yok say.
                if (verdict.existing is not None
                        and verdict.existing.id != m.id
                        and verdict.action is not DuplicateAction.NONE):
                    found += 1
        return f"({found} olası çakışma)"

    return _run("duplicate_detection", _do)


# --------------------------------------------------------------------------- #
# 4+5) Decay ve arşivleme (tek turda, PDF'teki yaşam döngüsü).
# --------------------------------------------------------------------------- #
def job_decay(manager: MemoryManager) -> JobResult:
    def _do() -> str:
        report = manager.decay_and_archive()
        return f"({report.summary})"

    return _run("decay_archive", _do)


# --------------------------------------------------------------------------- #
# 6) Knowledge graph bakımı — Faz 7'de dolacak.
# --------------------------------------------------------------------------- #
def job_graph_maintenance(manager: MemoryManager) -> JobResult:
    def _do() -> str:
        edges = manager.maintain_graph()
        return f"({edges} yeni kenar)"

    return _run("graph_maintenance", _do)


# --------------------------------------------------------------------------- #
# 7) Yedekleme — dışa aktarımı diske yazar (PDF: backup edilebilir olmalı).
# --------------------------------------------------------------------------- #
def job_backup(manager: MemoryManager, *, directory: str = "backups") -> JobResult:
    def _do() -> str:
        import os
        from datetime import datetime as _dt

        os.makedirs(directory, exist_ok=True)
        stamp = _dt.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = os.path.join(directory, f"twm-memory-{stamp}.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(manager.export_json(include_inactive=True, note="otomatik yedek"))
        return f"({path})"

    return _run("backup", _do)


def run_nightly(manager: MemoryManager) -> list[JobResult]:
    """Gecelik bakım turu: sırayla tüm işler."""
    return [
        job_backfill_embeddings(manager),
        job_reflection(manager, ReflectionLevel.DAILY),
        job_detect_duplicates(manager),
        job_decay(manager),
        job_graph_maintenance(manager),
        job_backup(manager),
    ]
