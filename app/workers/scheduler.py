"""app/workers/scheduler.py
===========================
Zamanlanmış worker'lar (APScheduler).

PDF: bu işler kullanıcı isteklerinden bağımsız çalışmalıdır. Bu yüzden bot
process'inden ayrı bir entry point (`worker.py`) ile de çalıştırılabilir.

Takvim (UTC değil, yapılandırılan saat dilimi):
  - 03:10 her gün      -> embedding backfill
  - 03:20 her gün      -> GÜNLÜK reflection
  - 03:40 Pazartesi    -> HAFTALIK reflection
  - 04:00 ayın 1'i     -> AYLIK reflection
  - 04:20 Oca/Nis/Tem/Eki 1'i -> ÇEYREKLİK reflection
  - 04:40 her gün      -> duplicate detection
  - 05:00 her gün      -> decay + arşivleme
  - 05:20 Pazar        -> knowledge graph bakımı

Ölçeklenmek gerekirse aynı `jobs` fonksiyonları Celery/RQ task'ı olarak
sarılabilir; bu dosya dışında hiçbir şey değişmez.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.enums import ReflectionLevel
from app.core.memory_manager import MemoryManager
from app.workers import jobs

logger = logging.getLogger(__name__)


def _register(scheduler, manager: MemoryManager, timezone: str) -> None:
    def cron(**kw) -> CronTrigger:
        return CronTrigger(timezone=timezone, **kw)

    scheduler.add_job(
        jobs.job_backfill_embeddings, cron(hour=3, minute=10), args=[manager],
        id="embedding_backfill", replace_existing=True,
    )
    scheduler.add_job(
        jobs.job_reflection, cron(hour=3, minute=20),
        args=[manager, ReflectionLevel.DAILY],
        id="reflection_daily", replace_existing=True,
    )
    scheduler.add_job(
        jobs.job_reflection, cron(day_of_week="mon", hour=3, minute=40),
        args=[manager, ReflectionLevel.WEEKLY],
        id="reflection_weekly", replace_existing=True,
    )
    scheduler.add_job(
        jobs.job_reflection, cron(day=1, hour=4, minute=0),
        args=[manager, ReflectionLevel.MONTHLY],
        id="reflection_monthly", replace_existing=True,
    )
    scheduler.add_job(
        jobs.job_reflection, cron(month="1,4,7,10", day=1, hour=4, minute=20),
        args=[manager, ReflectionLevel.QUARTERLY],
        id="reflection_quarterly", replace_existing=True,
    )
    scheduler.add_job(
        jobs.job_detect_duplicates, cron(hour=4, minute=40), args=[manager],
        id="duplicate_detection", replace_existing=True,
    )
    scheduler.add_job(
        jobs.job_decay, cron(hour=5, minute=0), args=[manager],
        id="decay_archive", replace_existing=True,
    )
    scheduler.add_job(
        jobs.job_graph_maintenance, cron(day_of_week="sun", hour=5, minute=20),
        args=[manager], id="graph_maintenance", replace_existing=True,
    )
    scheduler.add_job(
        jobs.job_backup, cron(hour=5, minute=40), args=[manager],
        id="backup", replace_existing=True,
    )


def start_background(manager: MemoryManager, *, timezone: str = "Europe/Istanbul"):
    """Bot process'i içinde arka planda çalıştırır (tek dyno kurulumu)."""
    scheduler = BackgroundScheduler(timezone=timezone)
    _register(scheduler, manager, timezone)
    scheduler.start()
    logger.info("Arka plan zamanlayıcı başladı (%d iş).", len(scheduler.get_jobs()))
    return scheduler


def run_blocking(manager: MemoryManager, *, timezone: str = "Europe/Istanbul") -> None:
    """Ayrı bir worker process'i olarak çalıştırır (önerilen)."""
    scheduler = BlockingScheduler(timezone=timezone)
    _register(scheduler, manager, timezone)
    logger.info("Worker başlatıldı (%d iş). Ctrl+C ile durdurulur.", len(scheduler.get_jobs()))
    scheduler.start()
