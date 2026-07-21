import logging
from datetime import datetime, timedelta

import pytz
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, CommandHandler, filters

import config
import ai_service
import memory_service
import notion_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TZ = pytz.timezone(config.TIMEZONE)


def _date_str(gun: str) -> str:
    today = datetime.now(TZ).date()
    if gun == "yarın":
        return (today + timedelta(days=1)).isoformat()
    return today.isoformat()


async def _reply(update: Update, chat_id, text: str) -> None:
    """Kullanıcıya cevap gönderir ve hafızaya asistan turu olarak kaydeder."""
    await update.message.reply_text(text)
    memory_service.add_message(chat_id, "assistant", text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Merhaba! Görevlerini bana yazman yeterli.\n\n"
        "Örnekler:\n"
        "- \"bugün faturayı ödemem lazım\"\n"
        "- \"yarın doktora gitmem lazım\"\n"
        "- \"fatura ödeme işi iptal oldu\"\n"
        "- \"faturayı ödedim\"\n"
        "- /liste yazarak bugünkü listeni görebilirsin\n"
        "- /sifirla yazarak hafızamı temizleyebilirsin"
    )


async def sifirla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory_service.clear_history(update.effective_chat.id)
    await update.message.reply_text("Tamamdır, önceki konuşmaları unuttum. Baştan başlıyoruz!")


async def liste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date_str = _date_str("bugün")
    tasks = notion_service.list_tasks(date_str)
    if not tasks:
        await update.message.reply_text("Bugün için henüz bir görev yok.")
        return

    lines = ["📋 Bugünün listesi:\n"]
    emoji = {"Yapılacak": "🔲", "Yapıldı": "✅", "İptal": "❌"}
    for t in tasks:
        lines.append(f"{emoji.get(t['durum'], '🔲')} {t['görev']} ({t['durum']})")
    await update.message.reply_text("\n".join(lines))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.effective_chat.id

    if not user_text or not user_text.strip():
        await update.message.reply_text("Boş bir mesaj gönderdin, tekrar dener misin?")
        return

    memory_service.add_message(chat_id, "user", user_text)
    # Az önce eklediğimiz mevcut mesaj, ai_service'e ayrıca gönderileceği için
    # geçmiş listesinden çıkarıyoruz (tekrar etmesin diye).
    history = memory_service.get_recent_messages(
        chat_id, limit=memory_service.DEFAULT_HISTORY_LIMIT + 1
    )[:-1]

    try:
        komut = ai_service.interpret_message(user_text, history=history)
    except ai_service.AIServiceError:
        logger.exception("AI yorumlama hatası")
        await _reply(
            update, chat_id,
            "Şu an isteğini işleyemedim (servis hatası), birazdan tekrar dener misin?",
        )
        return

    islem = komut.islem
    gorev_metni = komut.gorev_metni
    date_str = _date_str(komut.tarih.value)
    yanit = komut.yanit or "Tamamdır."

    if islem is ai_service.Islem.EKLE:
        notion_service.add_task(gorev_metni, date_str)
        await _reply(update, chat_id, yanit)

    elif islem is ai_service.Islem.TAMAMLA:
        found = notion_service.mark_task_status(gorev_metni, "Yapıldı", date_str)
        if found:
            await _reply(update, chat_id, yanit)
        else:
            await _reply(update, chat_id, f"\"{gorev_metni}\" ile eşleşen bir görev bulamadım.")

    elif islem is ai_service.Islem.IPTAL:
        found = notion_service.mark_task_status(gorev_metni, "İptal", date_str)
        if found:
            await _reply(update, chat_id, yanit)
        else:
            await _reply(update, chat_id, f"\"{gorev_metni}\" ile eşleşen bir görev bulamadım.")

    elif islem is ai_service.Islem.NOT_EKLE:
        not_metni = komut.not_metni or ""
        found = notion_service.add_note_to_task(gorev_metni, not_metni, date_str)
        if found:
            await _reply(update, chat_id, yanit)
        else:
            await _reply(update, chat_id, f"\"{gorev_metni}\" ile eşleşen bir görev bulamadım.")

    elif islem is ai_service.Islem.LISTELE:
        await _reply(update, chat_id, yanit)
        await liste(update, context)

    else:
        await _reply(update, chat_id, yanit)


def main():
    memory_service.init_db()
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("liste", liste))
    app.add_handler(CommandHandler("sifirla", sifirla))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot başlatılıyor...")
    app.run_polling()


if __name__ == "__main__":
    main()
