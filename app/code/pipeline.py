"""app/core/pipeline.py
=======================
Mesaj işleme hattı orkestratörü (PDF'teki tam sıra).

    User Message
      -> Intent Detection            (senkron; yanıt için)
      -> Görev işlemi (varsa)        (Notion, Memory Manager üzerinden)
      -> Response Generation         (kullanıcıya döner)
      -> [arka plan] Memory Evaluation -> Classification -> Storage
                                       -> Embedding -> Vector Index

Kritik: Değerlendirme yanıt yolundan AYRI (executor'a submit) — böylece her
mesajda çalışsa da yanıt gecikmesini artırmaz. Ham mesaj asla saklanmaz.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytz
from pydantic import BaseModel

from app.core.commands import handle_command
from app.core.executor import BackgroundExecutor, InlineExecutor
from app.core.guardrails import Guardrails
from app.core.intent import Action, IntentResult
from app.core.memory_manager import MemoryManager
from app.core.models import Task, TaskMatch

logger = logging.getLogger(__name__)

_DURUM_EMOJI = {"Yapılacak": "🔲", "Yapıldı": "✅", "İptal": "❌"}


@dataclass
class PipelineResult:
    text: str                 # kullanıcıya gösterilecek yanıt
    intent: IntentResult
    extraction_scheduled: bool


class MessagePipeline:
    def __init__(
        self,
        manager: MemoryManager,
        *,
        executor: BackgroundExecutor | None = None,
        timezone: str = "Europe/Istanbul",
        guardrails: Guardrails | None = None,
    ) -> None:
        self._m = manager
        self._executor = executor or InlineExecutor()
        self._tz = pytz.timezone(timezone)
        self._guard = guardrails or Guardrails()

    # ---- tarih yardımcıları ----------------------------------------------
    def _today(self) -> date:
        return datetime.now(self._tz).date()

    def _date_str(self, tarih: str) -> str:
        if tarih == "yarın":
            return (self._today() + timedelta(days=1)).isoformat()
        return self._today().isoformat()

    # ---- ana giriş --------------------------------------------------------
    def handle(
        self, message: str, chat_id: str, history: list[dict] | None = None
    ) -> PipelineResult:
        if not message or not message.strip():
            return PipelineResult("Boş mesaj gönderdin, tekrar dener misin?",
                                  IntentResult(action=Action.CHAT, yanit=""), False)

        # Hız sınırı: spam ve maliyet koruması.
        if not self._guard.allow_message(chat_id):
            return PipelineResult(
                "Biraz hızlı gidiyoruz 🙂 Bir dakika sonra tekrar dener misin?",
                IntentResult(action=Action.CHAT, yanit=""), False,
            )

        message = self._guard.truncate_message(message)

        # 0) Komut mu? (kalıcı bağlam yönetimi) — LLM'e gitmeden işlenir.
        cmd = handle_command(self._m, message, chat_id)
        if cmd.handled:
            return PipelineResult(
                text=cmd.text,
                intent=IntentResult(action=Action.CHAT, yanit=cmd.text),
                extraction_scheduled=False,
            )

        # 1) Retrieval + Injection: ilgili hafızaları öncelik sırasıyla getir
        context = self._guard.truncate_context(self._m.build_context(message, chat_id))

        # 2) Intent Detection (hatırlanan bağlamla)
        intent = self._m.interpret_intent(message, history, context=context)

        # 3) Görev işlemi + yanıt üretimi
        text = self._execute(intent)

        # 4) Hafıza çıkarımını ARKA PLANA at (yanıtı bloklamaz).
        # Bütçe dolduysa yanıt yine döner, yalnızca çıkarım atlanır.
        scheduled = self._guard.allow_extraction(chat_id)
        if scheduled:
            self._executor.submit(
                lambda: self._m.evaluate_and_store(message, chat_id)
            )

        return PipelineResult(text=text, intent=intent, extraction_scheduled=scheduled)

    # ---- intent yürütme ---------------------------------------------------
    def _execute(self, intent: IntentResult) -> str:
        a = intent.action
        yanit = intent.yanit or "Tamamdır."

        if a is Action.ADD:
            self._m.add_task(
                intent.gorev_metni, self._date_str(intent.tarih),
                oncelik=intent.oncelik, kategori=intent.kategori,
            )
            return yanit

        if a is Action.COMPLETE:
            match = self._m.complete_task(intent.gorev_metni, self._date_str(intent.tarih))
            return self._match_msg(match, yanit, intent.gorev_metni)

        if a is Action.CANCEL:
            match = self._m.cancel_task(intent.gorev_metni, self._date_str(intent.tarih))
            return self._match_msg(match, yanit, intent.gorev_metni)

        if a is Action.NOTE:
            match = self._m.add_task_note(
                intent.gorev_metni, intent.not_metni or "", self._date_str(intent.tarih)
            )
            return self._match_msg(match, yanit, intent.gorev_metni)

        if a is Action.LIST:
            return yanit + "\n\n" + self._render_list(intent.kapsam)

        return yanit  # CHAT

    # ---- görev listeleme render ------------------------------------------
    def _render_list(self, kapsam: str) -> str:
        today = self._today()
        if kapsam == "yarın":
            tasks = self._m.list_tasks_for_date((today + timedelta(days=1)).isoformat())
            return self._fmt(tasks, [], "📋 Yarının listesi")
        if kapsam == "hafta":
            tasks = self._m.list_tasks_range(
                today.isoformat(), (today + timedelta(days=6)).isoformat()
            )
            return self._fmt(tasks, [], "🗓️ Bu haftanın listesi")
        if kapsam == "tümü":
            return self._fmt(self._m.list_open_tasks(), [], "📚 Tüm açık görevler")
        if kapsam == "gecikmiş":
            overdue = self._m.list_overdue_tasks(today.isoformat())
            if not overdue:
                return "⏳ Gecikmiş görevin yok! 🎉"
            return self._fmt([], overdue, "⏳ Geciken görevler")
        # bugün
        tasks = self._m.list_tasks_for_date(today.isoformat())
        overdue = self._m.list_overdue_tasks(today.isoformat())
        return self._fmt(tasks, overdue, "📋 Bugünün listesi")

    @staticmethod
    def _line(t: Task) -> str:
        return f"{_DURUM_EMOJI.get(t.durum, '🔲')} {t.oncelik} {t.gorev} ({t.kategori})"

    def _fmt(self, tasks, overdue, baslik: str) -> str:
        lines = [baslik]
        if overdue:
            lines.append("\n⏳ Gecikmiş:")
            lines += [self._line(t) for t in overdue]
        if tasks:
            lines.append("")
            lines += [self._line(t) for t in tasks]
        elif not overdue:
            lines.append("Bu kapsamda henüz görev yok.")
        return "\n".join(lines)

    @staticmethod
    def _match_msg(match: TaskMatch, ok: str, gorev: str) -> str:
        if match.task:
            return ok
        if match.candidates:
            secenekler = "\n".join(f"- {c}" for c in match.candidates)
            return (f"\"{gorev}\" ile birden fazla görev eşleşti, hangisi?\n{secenekler}")
        return f"\"{gorev}\" ile eşleşen görev bulamadım."
