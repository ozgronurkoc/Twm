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
        komut = ai_service.interpret_message(user_text)
    except ValueError:
        await update.message.reply_text("Boş bir mesaj gönderdin, tekrar dener misin?")
        return
    except ai_service.AIServiceError:
        logger.exception("AI yorumlama hatası")
        await update.message.reply_text(
            "Şu an isteğini işleyemedim (servis hatası), birazdan tekrar dener misin?"
        )
        return

    islem = komut.islem
    gorev_metni = komut.gorev_metni
    # Tarih hesaplamasını tek yerden (İstanbul saatiyle) yapmaya devam ediyoruz.
    date_str = _date_str(komut.tarih.value)

    if islem is ai_service.Islem.EKLE:
        notion_service.add_task(gorev_metni, date_str)
        await update.message.reply_text(f"✅ Eklendi: \"{gorev_metni}\"")

    elif islem is ai_service.Islem.TAMAMLA:
        found = notion_service.mark_task_status(gorev_metni, "Yapıldı", date_str)
        if found:
            await update.message.reply_text(f"✅ Tamamlandı olarak işaretlendi: \"{gorev_metni}\"")
        else:
            await update.message.reply_text(f"\"{gorev_metni}\" ile eşleşen bir görev bulamadım.")

    elif islem is ai_service.Islem.IPTAL:
        found = notion_service.mark_task_status(gorev_metni, "İptal", date_str)
        if found:
            await update.message.reply_text(f"❌ İptal edildi olarak işaretlendi: \"{gorev_metni}\"")
        else:
            await update.message.reply_text(f"\"{gorev_metni}\" ile eşleşen bir görev bulamadım.")

    elif islem is ai_service.Islem.NOT_EKLE:
        not_metni = komut.not_metni or ""
        found = notion_service.add_note_to_task(gorev_metni, not_metni, date_str)
        if found:
            await update.message.reply_text(f"📝 Not eklendi: \"{gorev_metni}\" -> {not_metni}")
        else:
            await update.message.reply_text(f"\"{gorev_metni}\" ile eşleşen bir görev bulamadım.")

    elif islem is ai_service.Islem.LISTELE:
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
