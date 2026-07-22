"""app/bot/handlers.py
======================
Telegram Chat Engine.

PDF: Chat Engine, hafıza veritabanına ASLA doğrudan erişmez; her şey Memory
Manager üzerinden geçer. Bu modül yalnızca Telegram'a özgü işleri yapar
(komut kaydı, mesaj alma, uzun yanıtı bölme) ve gerisini `MessagePipeline`'a
devreder.

Eski `memory_service.py` (ham mesajları SQLite'a yazan sohbet logu) burada
KULLANILMAZ — emekliye ayrılmıştır. Yerine kalıcı, yapılandırılmış hafıza
sistemi geçmiştir.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.core.pipeline import MessagePipeline

logger = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096  # Telegram tek mesaj karakter sınırı

WELCOME = """\
Merhaba! Ben senin kişisel asistanınım. 👋

Bana günlük dille yaz, gerisini ben hallederim:
• "yarın 3'te dişçi randevum var" → görevi eklerim
• "faturayı ödedim" → tamamlandı işaretlerim
• "bugün ne var?" → listeni gösteririm

Seni zamanla tanırım: tercihlerini, projelerini ve önemli olayları hatırlarım.

📌 Kalıcı bağlam
/kural, /proje, /dokuman, /prompt — her sohbette geçerli bilgi ekle
/kalici — listele · /kalici_sil <no> — kaldır

🧠 Hafıza
/hafiza — ne biliyorum? · /hafiza <kelime> — ara
/duzenle <no> <metin> — düzelt · /hafiza_sil <no> — unut
/disaktar — yedek al (md ekle: okunur döküm)
"""


def _split(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Uzun yanıtı Telegram sınırına göre böler (satır sınırlarını korur)."""
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            if current:
                parts.append(current)
            # Tek satır bile sınırı aşıyorsa sert böl.
            while len(line) > limit:
                parts.append(line[:limit])
                line = line[limit:]
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return parts


async def _reply(update: Update, text: str) -> None:
    for chunk in _split(text):
        await update.message.reply_text(chunk)


def build_application(token: str, pipeline: MessagePipeline) -> Application:
    """Telegram uygulamasını kurar ve handler'ları bağlar."""

    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await _reply(update, WELCOME)

    async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message.text or ""
        chat_id = str(update.effective_chat.id)

        # "yazıyor..." göstergesi — algılanan gecikmeyi azaltır.
        try:
            await ctx.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass

        try:
            result = pipeline.handle(message, chat_id=chat_id)
            await _reply(update, result.text)
        except Exception:
            logger.exception("Mesaj işlenemedi chat=%s", chat_id)
            await _reply(
                update,
                "Bir şeyler ters gitti, kaydedemedim. Birazdan tekrar dener misin?",
            )

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    # Diğer tüm komutlar (/kural, /hafiza, ...) pipeline içindeki komut
    # katmanında işlendiği için buraya ayrı handler eklemiyoruz: metin
    # handler'ı komutları da yakalar.
    app.add_handler(MessageHandler(filters.TEXT, on_message))
    return app
