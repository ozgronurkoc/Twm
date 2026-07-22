"""tests/test_phase9.py
=======================
Faz 9 testleri (dış servis GEREKTİRMEZ) — sertleştirme:
  1. Hız sınırı: dakikalık mesaj limiti, kayan pencere.
  2. Çıkarım bütçesi: limit dolunca YANIT DEVAM eder, sadece çıkarım atlanır.
  3. Bağlam kırpma: token bütçesi aşılınca sondan kesilir (öncelikli bloklar korunur).
  4. Mesaj kırpma: aşırı uzun girdi.
  5. Telegram yanıt bölme: 4096 sınırı, satır sınırları korunarak.
  6. **UÇTAN UCA**: mesaj -> hafıza -> sonraki mesajda hatırlama.

Çalıştırma:
    python -m tests.test_phase9
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.bot.handlers import _split
from app.core.enums import MemoryType
from app.core.executor import InlineExecutor
from app.core.guardrails import GuardrailConfig, Guardrails, RateLimiter
from app.core.memory_manager import MemoryManager
from app.core.models import MemoryDraft
from app.core.pipeline import MessagePipeline
from app.infra.db.sqlite_repo import SqliteMemoryRepository
from app.infra.notion.notion_repo import NotionTaskRepository
from app.infra.vector.inmemory_store import InMemoryVectorStore
from tests.fake_llm import ScriptedLLM
from tests.fake_notion import FakeNotionClient
from tests.test_phase3 import KeywordEmbedder


def _test_rate_limiter():
    rl = RateLimiter(limit=3, window_seconds=60)
    now = 1000.0
    assert all(rl.allow("u1", now=now + i) for i in range(3))
    assert not rl.allow("u1", now=now + 3), "4. istek reddedilmeliydi"
    # farklı kullanıcı etkilenmez
    assert rl.allow("u2", now=now + 3)
    # pencere kayınca yeniden serbest
    assert rl.allow("u1", now=now + 61)
    print("  ✓ Hız sınırı: kayan pencere, kullanıcı bazında izole")


def _test_truncation():
    g = Guardrails(GuardrailConfig(max_context_chars=120, max_message_chars=20))

    assert g.truncate_message("a" * 50) == "a" * 20
    assert g.truncate_message("kısa") == "kısa"

    ctx = (
        "## Kullanıcı kimliği\n- Adı Özgür.\n\n"
        "## Tercihler\n- Kısa yaz.\n\n"
        "## Bağlantılı bilgiler\n" + "- uzun bilgi satırı\n" * 20
    )
    cut = g.truncate_context(ctx)
    assert len(cut) <= 120
    # en öncelikli blok korunmalı
    assert "Kullanıcı kimliği" in cut
    # en az öncelikli blok feda edilmeli
    assert "Bağlantılı bilgiler" not in cut
    print("  ✓ Kırpma: mesaj + bağlam (öncelikli bloklar korundu)")


def _test_split():
    assert _split("kısa") == ["kısa"]

    text = "\n".join(f"satır {i}" for i in range(500))
    parts = _split(text, limit=200)
    assert all(len(p) <= 200 for p in parts)
    assert "".join(p.replace("\n", "") for p in parts) == text.replace("\n", "")

    # tek satır sınırı aşarsa sert bölünür
    parts = _split("x" * 500, limit=100)
    assert len(parts) == 5 and all(len(p) == 100 for p in parts)
    print("  ✓ Telegram yanıt bölme (satır sınırları + sert bölme)")


def _make(db_path, *, intents=None, evals=None, guardrails=None):
    mgr = MemoryManager(
        repository=SqliteMemoryRepository(db_path),
        vector_store=InMemoryVectorStore(),
        embedder=KeywordEmbedder(),
        llm=ScriptedLLM(intents=intents, evals=evals),
        task_repository=NotionTaskRepository(api_key="x", page_id="p",
                                             client=FakeNotionClient()),
    )
    pipe = MessagePipeline(mgr, executor=InlineExecutor(),
                           guardrails=guardrails or Guardrails())
    return mgr, pipe


def _test_message_rate_limit(tmp):
    guard = Guardrails(GuardrailConfig(max_messages_per_minute=2))
    _, pipe = _make(str(Path(tmp) / "a.db"), guardrails=guard)

    r1 = pipe.handle("selam", chat_id="c1")
    r2 = pipe.handle("selam", chat_id="c1")
    r3 = pipe.handle("selam", chat_id="c1")

    assert "hızlı" in r3.text.lower(), "3. mesaj sınırlanmalıydı"
    assert r3.extraction_scheduled is False
    # başka sohbet etkilenmez
    assert "hızlı" not in pipe.handle("selam", chat_id="c2").text.lower()
    print("  ✓ Mesaj hız sınırı devrede (sohbet bazında)")


def _test_extraction_budget(tmp):
    """Çıkarım bütçesi dolunca yanıt DEVAM etmeli, sadece çıkarım atlanmalı."""
    guard = Guardrails(GuardrailConfig(max_messages_per_minute=100,
                                       max_extractions_per_hour=1))
    mgr, pipe = _make(
        str(Path(tmp) / "b.db"),
        intents=[{"action": "chat", "yanit": "olur"},
                 {"action": "chat", "yanit": "tabii"}],
        evals=[{"memories": [{"type": "knowledge", "title": "n",
                              "content": "Kullanıcı nonplo üzerinde çalışıyor."}]},
               {"memories": [{"type": "knowledge", "title": "k",
                              "content": "İkinci bilgi."}]}],
        guardrails=guard,
    )

    r1 = pipe.handle("nonplo üzerinde çalışıyorum", chat_id="c1")
    assert r1.extraction_scheduled is True

    r2 = pipe.handle("bir şey daha", chat_id="c1")
    assert r2.text, "bütçe dolsa da yanıt üretilmeli"
    assert r2.extraction_scheduled is False, "çıkarım atlanmalıydı"

    # yalnızca ilk mesajdan hafıza oluştu
    assert len(mgr._repo.list_by_type(MemoryType.KNOWLEDGE)) == 1
    print("  ✓ Çıkarım bütçesi: yanıt sürdü, çıkarım atlandı")


def _test_end_to_end_memory(tmp):
    """UÇTAN UCA: bir mesajda öğrenilen bilgi, sonraki mesajın bağlamında olmalı."""
    mgr, pipe = _make(
        str(Path(tmp) / "c.db"),
        intents=[{"action": "chat", "yanit": "Memnun oldum!"},
                 {"action": "chat", "yanit": "Tabii."}],
        evals=[{"memories": [{"type": "identity", "title": "Proje",
                              "content": "Kullanıcı nonplo adlı bir girişim yürütüyor.",
                              "importance": 0.9}]},
               {"memories": []}],
    )

    pipe.handle("ben nonplo diye bir girişim yürütüyorum", chat_id="c1")

    # İkinci mesajda bağlam kurulurken bu bilgi enjekte edilmeli
    ctx = mgr.build_context("nonplo hakkında ne biliyorsun", chat_id="c1")
    assert "nonplo adlı bir girişim" in ctx, "öğrenilen bilgi hatırlanmalıydı"
    assert "## Kullanıcı kimliği" in ctx
    print("  ✓ Uçtan uca: mesajda öğrenilen bilgi sonraki bağlamda hatırlandı")


def _test_commands_still_work(tmp):
    mgr, pipe = _make(str(Path(tmp) / "d.db"))
    r = pipe.handle("/kural Kısa cevap ver", chat_id="c1")
    assert "eklendi" in r.text
    r = pipe.handle("/hafiza", chat_id="c1")
    assert "Toplam" in r.text
    print("  ✓ Komut katmanı guardrail'lerle birlikte çalışıyor")


def run():
    _test_rate_limiter()
    _test_truncation()
    _test_split()
    with tempfile.TemporaryDirectory() as d:
        _test_message_rate_limit(d)
        _test_extraction_budget(d)
        _test_end_to_end_memory(d)
        _test_commands_still_work(d)
    print("OK — Faz 9 tüm testleri geçti.")


def test_phase9():
    run()


if __name__ == "__main__":
    run()
