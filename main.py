import logging
from datetime import datetime, timedelta

import pytz
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, CommandHandler, filters

import config
import ai_service
import notion_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TZ = pytz.timezone(config.TIMEZONE)


def _date_str(gun: str) -> str:
    today = datetime.now(TZ).date()
    if gun == "yarın":
        return (today + timedelta(days=1)).isoformat()
    return today.isoformat()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Merhaba! Görevlerini bana yazman yeterli.\n\n"
        "Örnekler:\n"
        "- \"bugün faturayı ödemem lazım\"\n"
        "- \"yarın doktora gitmem lazım\"\n"
        "- \"fatura ödeme işi iptal oldu\"\n"
        "- \"faturayı ödedim\"\n"
        "- /liste yazarak bugünkü listeni görebilirsin"
    )


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
    try:
        result = ai_service.interpret_message(user_text)
    except Exception:
        logger.exception("AI yorumlama hatası")
        await update.message.reply_text("Mesajını anlayamadım, tekrar dener misin?")
        return

    islem = result.get("islem")
    gorev_metni = result.get("gorev_metni")
    date_str = _date_str(result.get("tarih", "bugün"))

    if islem == "ekle":
        notion_service.add_task(gorev_metni, date_str)
        await update.message.reply_text(f"✅ Eklendi: \"{gorev_metni}\"")

    elif islem == "tamamla":
        found = notion_service.mark_task_status(gorev_metni, "Yapıldı", date_str)
        if found:
            await update.message.reply_text(f"✅ Tamamlandı olarak işaretlendi: \"{gorev_metni}\"")
        else:
            await update.message.reply_text(f"\"{gorev_metni}\" ile eşleşen bir görev bulamadım.")

    elif islem == "iptal":
        found = notion_service.mark_task_status(gorev_metni, "İptal", date_str)
        if found:
            await update.message.reply_text(f"❌ İptal edildi olarak işaretlendi: \"{gorev_metni}\"")
        else:
            await update.message.reply_text(f"\"{gorev_metni}\" ile eşleşen bir görev bulamadım.")

    elif islem == "not_ekle":
        not_metni = result.get("not_metni", "")
        found = notion_service.add_note_to_task(gorev_metni, not_metni, date_str)
        if found:
            await update.message.reply_text(f"📝 Not eklendi: \"{gorev_metni}\" -> {not_metni}")
        else:
            await update.message.reply_text(f"\"{gorev_metni}\" ile eşleşen bir görev bulamadım.")

    elif islem == "listele":
        await liste(update, context)

    else:
        await update.message.reply_text(
            "Ne yapmak istediğini tam anlayamadım. Biraz daha açık yazar mısın?"
        )


def main():
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("liste", liste))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot başlatılıyor...")
    app.run_polling()


if __name__ == "__main__":
    main()
