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

_DURUM_EMOJI = {"Yapılacak": "🔲", "Yapıldı": "✅", "İptal": "❌"}


def _date_str(gun: str) -> str:
    today = datetime.now(TZ).date()
    if gun == "yarın":
        return (today + timedelta(days=1)).isoformat()
    return today.isoformat()


def _today() -> datetime.date:
    return datetime.now(TZ).date()


def _format_task_line(t: dict) -> str:
    return f"{_DURUM_EMOJI.get(t['durum'], '🔲')} {t['öncelik']} {t['görev']} ({t['kategori']})"


async def _reply(update: Update, chat_id, text: str) -> None:
    """Kullanıcıya cevap gönderir ve hafızaya asistan turu olarak kaydeder."""
    await update.message.reply_text(text)
    memory_service.add_message(chat_id, "assistant", text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Merhaba! Görevlerini bana yazman yeterli.\n\n"
        "Örnekler:\n"
        "- \"bugün faturayı ödemem lazım\"\n"
        "- \"acil, yarın doktora gitmem lazım\"\n"
        "- \"fatura ödeme işi iptal oldu\"\n"
        "- \"faturayı ödedim\"\n"
        "- \"bu hafta neler var\" ya da \"geciken işlerim var mı\"\n"
        "- /liste yazarak bugünkü listeni görebilirsin\n"
        "- /sifirla yazarak hafızamı temizleyebilirsin"
    )


async def sifirla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory_service.clear_history(update.effective_chat.id)
    await update.message.reply_text("Tamamdır, önceki konuşmaları unuttum. Baştan başlıyoruz!")


def _render_liste(tasks: list[dict], overdue: list[dict] | None = None, baslik: str = "📋 Liste") -> str:
    lines = [f"{baslik}\n"]
    if overdue:
        lines.append("⏳ Gecikmiş:")
        for t in overdue:
            lines.append(_format_task_line(t))
        lines.append("")
    if tasks:
        for t in tasks:
            lines.append(_format_task_line(t))
    elif not overdue:
        lines.append("Bu kapsamda henüz bir görev yok.")
    return "\n".join(lines)


async def liste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date_str = _date_str("bugün")
    tasks = notion_service.list_tasks(date_str)
    overdue = notion_service.list_overdue_tasks(date_str)
    await update.message.reply_text(_render_liste(tasks, overdue, "📋 Bugünün listesi"))


async def _handle_listele(update: Update, context: ContextTypes.DEFAULT_TYPE, kapsam: ai_service.Kapsam):
    today = _today()

    if kapsam is ai_service.Kapsam.YARIN:
        date_str = _date_str("yarın")
        tasks = notion_service.list_tasks(date_str)
        text = _render_liste(tasks, None, "📋 Yarının listesi")

    elif kapsam is ai_service.Kapsam.HAFTA:
        start = today.isoformat()
        end = (today + timedelta(days=6)).isoformat()
        tasks = notion_service.list_tasks_range(start, end)
        text = _render_liste(tasks, None, "🗓️ Bu haftanın listesi")

    elif kapsam is ai_service.Kapsam.TUMU:
        tasks = notion_service.list_all_open_tasks()
        text = _render_liste(tasks, None, "📚 Tüm açık görevler")

    elif kapsam is ai_service.Kapsam.GECIKMIS:
        overdue = notion_service.list_overdue_tasks(today.isoformat())
        if not overdue:
            text = "⏳ Gecikmiş bir görevin yok, harikasın! 🎉"
        else:
            text = _render_liste([], overdue, "⏳ Geciken görevler")

    else:  # BUGUN (varsayılan)
        date_str = today.isoformat()
        tasks = notion_service.list_tasks(date_str)
        overdue = notion_service.list_overdue_tasks(date_str)
        text = _render_liste(tasks, overdue, "📋 Bugünün listesi")

    await update.message.reply_text(text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.effective_chat.id

    if not user_text or not user_text.strip():
        await update.message.reply_text("Boş bir mesaj gönderdin, tekrar dener misin?")
        return

    memory_service.add_message(chat_id, "user", user_text)
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
        notion_service.add_task(
            gorev_metni, date_str, oncelik=komut.oncelik.value, kategori=komut.kategori.value
        )
        await _reply(update, chat_id, yanit)

    elif islem is ai_service.Islem.TAMAMLA:
        match = notion_service.mark_task_status(gorev_metni, "Yapıldı", date_str)
        await _reply(update, chat_id, _sonuc_mesaji(match, yanit, gorev_metni))

    elif islem is ai_service.Islem.IPTAL:
        match = notion_service.mark_task_status(gorev_metni, "İptal", date_str)
        await _reply(update, chat_id, _sonuc_mesaji(match, yanit, gorev_metni))

    elif islem is ai_service.Islem.NOT_EKLE:
        not_metni = komut.not_metni or ""
        match = notion_service.add_note_to_task(gorev_metni, not_metni, date_str)
        await _reply(update, chat_id, _sonuc_mesaji(match, yanit, gorev_metni))

    elif islem is ai_service.Islem.LISTELE:
        await _reply(update, chat_id, yanit)
        await _handle_listele(update, context, komut.kapsam)

    else:
        await _reply(update, chat_id, yanit)


def _sonuc_mesaji(match: notion_service.MatchResult, basarili_yanit: str, gorev_metni: str) -> str:
    """Eşleştirme sonucuna göre kullanıcıya gösterilecek metni üretir."""
    if match.page:
        return basarili_yanit
    if match.candidates:
        secenekler = "\n".join(f"- {c}" for c in match.candidates)
        return (
            f"\"{gorev_metni}\" ile birden fazla görev eşleşti, hangisini "
            f"kastettiğini netleştirir misin?\n{secenekler}"
        )
    return f"\"{gorev_metni}\" ile eşleşen bir görev bulamadım."


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
