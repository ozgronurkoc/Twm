"""main.py
==========
Bot giriş noktası (production).

Akış:
    config -> factory (DI) -> MemoryManager -> MessagePipeline -> Telegram

Kullanım:
    python main.py            # botu çalıştır
    python main.py --health   # yalnızca sağlık kontrolü yap ve çık

Arka plan bakım işleri AYRI bir process'tedir: `python worker.py`
(bkz. Procfile). Tek dyno ile çalışmak zorundaysan RUN_SCHEDULER_IN_BOT=true
yaparak zamanlayıcıyı bu process içinde de başlatabilirsin.
"""
from __future__ import annotations

import logging
import os
import sys

import config
from app.bot.handlers import build_application
from app.core.executor import ThreadExecutor
from app.core.guardrails import Guardrails
from app.core.pipeline import MessagePipeline
from app.factory import build_memory_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# Kütüphane gürültüsünü kıs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

logger = logging.getLogger("twm")


def _health(manager) -> bool:
    health = manager.health_check()
    for name, ok in health.items():
        logger.info("  %s %s", "OK" if ok else "HATA", name)
    return all(health.values())


def main() -> None:
    logger.info("Twm baslatiliyor...")
    manager = build_memory_manager()

    logger.info("Saglayici saglik kontrolu:")
    healthy = _health(manager)

    if "--health" in sys.argv:
        sys.exit(0 if healthy else 1)

    if not healthy:
        logger.error(
            "Saglayicilar hazir degil. .env ve migration'i kontrol et "
            "(migrations/001_init.sql)."
        )
        sys.exit(1)

    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN tanimli degil.")
        sys.exit(1)

    pipeline = MessagePipeline(
        manager,
        # Production'da cikarim yaniti bloklamasin diye thread havuzu.
        executor=ThreadExecutor(max_workers=2),
        timezone=config.TIMEZONE,
        guardrails=Guardrails(),
    )

    # Istege bagli: tek process kurulumunda zamanlayiciyi burada baslat.
    if os.environ.get("RUN_SCHEDULER_IN_BOT", "").lower() == "true":
        from app.workers.scheduler import start_background

        start_background(manager, timezone=config.TIMEZONE)
        logger.info("Zamanlayici bot process'i icinde baslatildi.")

    app = build_application(config.TELEGRAM_BOT_TOKEN, pipeline)
    logger.info("Bot hazir, mesaj bekleniyor.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
