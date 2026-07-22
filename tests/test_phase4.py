"""tests/test_phase4.py
=======================
Faz 4 testleri (dış servis GEREKTİRMEZ) — kalıcı bağlam:
  1. Komutlarla ekleme (/kural, /proje) ve listeleme (/kalici).
  2. **KRİTİK**: kalıcı bağlam, sorguyla semantik olarak ALAKASIZ olsa bile
     her zaman bağlama girer (sıradan bilgi ise girmez) — kalıcı bilgi ile
     deneyimin ayrıldığının kanıtı.
  3. Enjeksiyonda ayrı blok + türe göre gruplama.
  4. /kalici_sil ile kaldırma; unpin ile hafızayı silmeden bağlamdan çıkarma.

Çalıştırma:
    python -m tests.test_phase4
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.core.commands import handle_command
from app.core.enums import MemoryType
from app.core.executor import InlineExecutor
from app.core.memory_manager import MemoryManager
from app.core.models import MemoryDraft
from app.core.persistent_context import ContextKind
from app.core.pipeline import MessagePipeline
from app.core.retrieval import RetrievalConfig
from app.infra.db.sqlite_repo import SqliteMemoryRepository
from app.infra.notion.notion_repo import NotionTaskRepository
from app.infra.vector.inmemory_store import InMemoryVectorStore
from tests.fake_llm import ScriptedLLM
from tests.fake_notion import FakeNotionClient
from tests.test_phase3 import KeywordEmbedder


def _make(db_path):
    return MemoryManager(
        repository=SqliteMemoryRepository(db_path),
        vector_store=InMemoryVectorStore(),
        embedder=KeywordEmbedder(),
        llm=ScriptedLLM(),
        task_repository=NotionTaskRepository(api_key="x", page_id="p", client=FakeNotionClient()),
        retrieval_config=RetrievalConfig(max_knowledge=3),
    )


def _test_commands(tmp):
    mgr = _make(str(Path(tmp) / "a.db"))

    r = handle_command(mgr, "/kural Bana her zaman kısa ve madde madde cevap ver")
    assert r.handled and "eklendi" in r.text

    r = handle_command(mgr, "/proje Nonplo: WhatsApp AI satış ajanı, hedef KOBİler")
    assert r.handled

    r = handle_command(mgr, "/kalici")
    assert r.handled and "Kalıcı bağlam" in r.text
    assert "madde madde" in r.text and "Nonplo" in r.text

    # tür filtresi
    assert len(mgr.list_persistent_context(ContextKind.RULE)) == 1
    assert len(mgr.list_persistent_context(ContextKind.PROJECT)) == 1
    assert len(mgr.list_persistent_context()) == 2
    print("  ✓ Komutlarla ekleme + listeleme + tür filtresi")


def _test_always_loaded_regardless_of_relevance(tmp):
    """En kritik test: kalıcı bağlam ALAKASIZ sorguda bile yüklenir,
    sıradan bilgi ise yüklenmez."""
    mgr = _make(str(Path(tmp) / "b.db"))

    # Kalıcı kural — sorguyla hiçbir kelime ortaklığı yok
    mgr.add_persistent_context(
        "Kullanıcıya her zaman kısa cevap ver.", kind=ContextKind.RULE
    )
    # Sıradan bilgi — o da sorguyla alakasız
    mgr.create(MemoryDraft(
        type=MemoryType.KNOWLEDGE, title="içecek",
        content="Kullanıcı sabah kahve içer.", tags=["kahve"],
    ))

    # Tamamen alakasız bir sorgu (pgvector hakkında)
    ctx = mgr.build_context("pgvector cosine nasıl çalışır", chat_id="1")

    assert "kısa cevap ver" in ctx, "kalıcı bağlam her zaman yüklenmeliydi"
    assert "kahve" not in ctx, "alakasız sıradan bilgi yüklenmemeliydi"
    print("  ✓ Kalıcı bağlam alakadan bağımsız yüklendi, sıradan bilgi yüklenmedi")


def _test_injection_block_and_grouping(tmp):
    mgr = _make(str(Path(tmp) / "c.db"))
    mgr.add_persistent_context("Emoji kullanma.", kind=ContextKind.RULE)
    mgr.add_persistent_context("Nonplo KOBİlere satış yapar.", kind=ContextKind.PROJECT)

    ctx = mgr.build_context("merhaba", chat_id="1")
    assert "## Kalıcı bağlam (her zaman geçerli)" in ctx
    assert "### Kural" in ctx and "### Proje" in ctx
    # kalıcı blok, sıradan "İlgili bilgiler" bloğundan ayrı olmalı
    assert "Emoji kullanma" in ctx.split("## Kalıcı bağlam")[1]
    print("  ✓ Enjeksiyonda ayrı blok + türe göre gruplama")


def _test_remove_and_unpin(tmp):
    mgr = _make(str(Path(tmp) / "d.db"))
    mgr.add_persistent_context("Silinecek kural.", kind=ContextKind.RULE)
    keep = mgr.add_persistent_context("Kalacak bilgi.", kind=ContextKind.DOC)

    r = handle_command(mgr, "/kalici_sil 1")
    assert r.handled and "Kaldırıldı" in r.text
    assert len(mgr.list_persistent_context()) == 1

    # unpin: hafıza SİLİNMEZ, sadece kalıcı bağlamdan çıkar
    mgr.unpin_persistent_context(keep.id)
    assert len(mgr.list_persistent_context()) == 0
    still_there = mgr.get(keep.id)
    assert still_there is not None and still_there.status.value == "active"
    print("  ✓ Silme + unpin (hafıza korunarak bağlamdan çıkarma)")


def _test_pipeline_command_shortcircuit(tmp):
    mgr = _make(str(Path(tmp) / "e.db"))
    pipe = MessagePipeline(mgr, executor=InlineExecutor())
    res = pipe.handle("/kural Türkçe konuş", chat_id="1")
    assert "eklendi" in res.text
    # komut LLM'e gitmedi, hafıza çıkarımı tetiklenmedi
    assert res.extraction_scheduled is False
    assert len(mgr.list_persistent_context()) == 1
    print("  ✓ Pipeline komutu LLM'e göndermeden işledi")


def run():
    with tempfile.TemporaryDirectory() as d:
        _test_commands(d)
        _test_always_loaded_regardless_of_relevance(d)
        _test_injection_block_and_grouping(d)
        _test_remove_and_unpin(d)
        _test_pipeline_command_shortcircuit(d)
    print("OK — Faz 4 tüm testleri geçti.")


def test_phase4():
    run()


if __name__ == "__main__":
    run()
