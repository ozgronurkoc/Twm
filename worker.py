"""worker.py
=============
Arka plan worker giriş noktası.

PDF: bakım işleri kullanıcı isteklerinden bağımsız çalışmalıdır. Bu dosya, bot
process'inden AYRI bir süreç olarak çalıştırılır (Procfile'daki `worker:` satırı).

Kullanım:
    python worker.py            # zamanlayıcıyı başlat (sürekli çalışır)
    python worker.py --once     # tüm bakım işlerini bir kez çalıştır, çık
"""
from __future__ import annotations

import logging
import sys

import config
from app.factory import build_memory_manager
from app.workers import jobs
from app.workers.scheduler import run_blocking

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("twm.worker")


def main() -> None:
    manager = build_memory_manager()

    if "--once" in sys.argv:
        logger.info("Tek seferlik bakım turu başlıyor...")
        for result in jobs.run_nightly(manager):
            status = "✓" if result.ok else "✗"
            logger.info("  %s %s %s", status, result.name, result.detail)
        logger.info("Bakım turu bitti.")
        return

    run_blocking(manager, timezone=config.TIMEZONE)


if __name__ == "__main__":
    main()
