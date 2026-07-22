"""tests/test_phase1.py
=======================
Faz 1 testleri (dış servis GEREKTİRMEZ):
  1. Beş hafıza katmanının (Identity/Preference/Episode/Knowledge/Reflection)
     SQLite repo üzerinden yazılıp okunması.
  2. Layer 5 (Task) katmanının, sahte Notion client'ıyla Memory Manager
     üzerinden ekleme/tamamlama/iptal/listeleme akışı.

Çalıştırma:
    python -m tests.test_phase1
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.core.enums import MemoryType
from app.core.memory_manager import MemoryManager
from app.core.models import MemoryDraft
from app.domain.embeddings import EmbeddingProvider
from app.domain.llm import LLMProvider
from app.infra.db.sqlite_repo import SqliteMemoryRepository
from app.infra.notion.notion_repo import NotionTaskRepository
from app.infra.vector.noop_store import NoopVectorStore
from tests.fake_notion import FakeNotionClient


class FakeEmbedder(EmbeddingProvider):
    @property
    def dimension(self) -> int:
        return 3

    def embed(self, text: str) -> list[float]:
        return [float(len(text)), 1.0, 0.0]

    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


class FakeLLM(LLMProvider):
    def complete(self, *, system, messages, temperature=0.0):
        return "ok"

    def complete_structured(self, *, system, messages, tool_schema, tool_name, temperature=0.0):
        return {}


def _manager(db_path: str) -> MemoryManager:
    task_repo = NotionTaskRepository(
        api_key="x", page_id="page", client=FakeNotionClient()
    )
    return MemoryManager(
        repository=SqliteMemoryRepository(db_path),
        vector_store=NoopVectorStore(),
        embedder=FakeEmbedder(),
        llm=FakeLLM(),
        task_repository=task_repo,
    )


def _test_all_layers(mgr: MemoryManager) -> None:
    samples = {
        MemoryType.IDENTITY: ("Ad", "Kullanıcının adı Özgür."),
        MemoryType.PREFERENCE: ("Ton", "Kısa ve net yaz."),
        MemoryType.EPISODE: ("Olay", "Bugün Nonplo ilk reklamı yayınladı."),
        MemoryType.KNOWLEDGE: ("Not", "pgvector cosine <=> operatörü kullanır."),
        MemoryType.REFLECTION: ("Haftalık", "Bu hafta GTM'e odaklanıldı."),
    }
    created_ids = {}
    for mtype, (title, content) in samples.items():
        m = mgr.create(MemoryDraft(type=mtype, title=title, content=content))
        assert m.type is mtype
        created_ids[mtype] = m.id

    # her katman okunabiliyor mu
    for mtype, mid in created_ids.items():
        got = mgr.get(mid)
        assert got is not None and got.type is mtype

    # list_by_type katman bazında çalışıyor mu
    assert len(mgr._repo.list_by_type(MemoryType.KNOWLEDGE)) == 1
    print("  ✓ 5 hafıza katmanı yaz/oku çalışıyor")


def _test_task_layer(mgr: MemoryManager) -> None:
    # ekle
    t = mgr.add_task("faturayı öde", "2026-07-22", oncelik="🔴 Yüksek", kategori="🏠 Ev")
    assert t.durum == "Yapılacak" and t.notion_page_id

    # listele (bugüne ait)
    todays = mgr.list_tasks_for_date("2026-07-22")
    assert any(x.gorev == "faturayı öde" for x in todays)

    # açık görevler
    assert len(mgr.list_open_tasks()) == 1

    # tamamla
    match = mgr.complete_task("faturayı öde", "2026-07-22")
    assert match.task is not None and match.task.durum == "Yapıldı"

    # tamamlandıktan sonra açık görev kalmadı
    assert len(mgr.list_open_tasks()) == 0

    # ikinci görev + iptal
    mgr.add_task("spora git", "2026-07-22", oncelik="🟡 Orta", kategori="❤️ Sağlık")
    cancel = mgr.cancel_task("spora git", "2026-07-22")
    assert cancel.task is not None and cancel.task.durum == "İptal"
    print("  ✓ Task katmanı (Notion) Memory Manager üzerinden çalışıyor")


def run() -> None:
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "t.db")
        mgr = _manager(db)
        _test_all_layers(mgr)
        _test_task_layer(mgr)
    print("OK — Faz 1 tüm testleri geçti.")


def test_phase1():
    run()


if __name__ == "__main__":
    run()
