"""tests/test_phase2.py
=======================
Faz 2 testleri (dış servis GEREKTİRMEZ) — mesaj işleme hattı:
  1. Görev ekleme: "yarın faturayı öde" -> task_add -> Notion'da görev, yanıt doğal.
  2. Kimlik çıkarımı: sohbet mesajından identity hafızaları üretimi (ham mesaj
     saklanmaz, üçüncü-tekil cümleye dönüşür).
  3. Tercih ezme: yeni tercih eskisini supersede eder (aktif tercih tek kalır).
  4. Sohbet: selam -> hafıza üretilmez.

InlineExecutor kullanılır (değerlendirme senkron çalışır ki assert edebilelim).

Çalıştırma:
    python -m tests.test_phase2
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.core.enums import MemoryStatus, MemoryType
from app.core.executor import InlineExecutor
from app.core.memory_manager import MemoryManager
from app.core.pipeline import MessagePipeline
from app.infra.db.sqlite_repo import SqliteMemoryRepository
from app.infra.notion.notion_repo import NotionTaskRepository
from app.infra.vector.noop_store import NoopVectorStore
from tests.fake_llm import ScriptedLLM
from tests.fake_notion import FakeNotionClient
from tests.test_phase1 import FakeEmbedder


def _make(db_path, *, intents=None, evals=None):
    llm = ScriptedLLM(intents=intents, evals=evals)
    mgr = MemoryManager(
        repository=SqliteMemoryRepository(db_path),
        vector_store=NoopVectorStore(),
        embedder=FakeEmbedder(),
        llm=llm,
        task_repository=NotionTaskRepository(api_key="x", page_id="p", client=FakeNotionClient()),
    )
    pipe = MessagePipeline(mgr, executor=InlineExecutor())
    return mgr, pipe


def _test_task_add(tmp):
    mgr, pipe = _make(
        str(Path(tmp) / "a.db"),
        intents=[{
            "action": "task_add", "gorev_metni": "fatura ödenecek",
            "tarih": "yarın", "oncelik": "🔴 Yüksek", "kategori": "🏠 Ev",
            "yanit": "Halledildi, yarına faturayı ekledim!",
        }],
        evals=[{"memories": []}],  # saf görev -> hafıza yok
    )
    res = pipe.handle("yarın faturayı ödemem lazım", chat_id="42")
    assert "ekledim" in res.text.lower()
    assert len(mgr.list_open_tasks()) == 1
    # ham mesaj hafızaya YAZILMADI
    assert len(mgr._repo.list_by_type(MemoryType.KNOWLEDGE)) == 0
    print("  ✓ Görev ekleme + ham mesaj saklanmadı")


def _test_identity_extraction(tmp):
    mgr, pipe = _make(
        str(Path(tmp) / "b.db"),
        intents=[{"action": "chat", "yanit": "Memnun oldum Özgür!"}],
        evals=[{
            "is_important": True, "useful_future": True,
            "memories": [
                {"type": "identity", "title": "Ad", "content": "Kullanıcının adı Özgür.",
                 "summary": "ad: Özgür", "importance": 0.95, "confidence": 0.9},
                {"type": "identity", "title": "Şehir", "content": "Kullanıcı İstanbul'da yaşıyor.",
                 "importance": 0.85, "confidence": 0.85},
            ],
        }],
    )
    res = pipe.handle("selam, ben Özgür, İstanbul'da yaşıyorum", chat_id="42")
    idents = mgr._repo.list_by_type(MemoryType.IDENTITY)
    assert len(idents) == 2
    # üçüncü-tekil, kalıcı cümle (ham mesaj değil)
    assert any("Kullanıcının adı Özgür" in m.content for m in idents)
    assert "selam" not in " ".join(m.content for m in idents).lower()
    print("  ✓ Kimlik çıkarımı (ham mesaj -> yapılandırılmış bilgi)")


def _test_preference_overwrite(tmp):
    dbp = str(Path(tmp) / "c.db")
    mgr, pipe = _make(
        dbp,
        intents=[{"action": "chat", "yanit": "tamam"}, {"action": "chat", "yanit": "tamam"}],
        evals=[
            {"memories": [{"type": "preference", "title": "yanıt uzunluğu",
                           "content": "Kullanıcı kısa yanıt ister.", "should_overwrite": False}]},
            {"memories": [{"type": "preference", "title": "yanıt uzunluğu",
                           "content": "Kullanıcı uzun ve detaylı yanıt ister.", "should_overwrite": True}]},
        ],
    )
    pipe.handle("kısa yaz lütfen", chat_id="42")
    pipe.handle("aslında uzun ve detaylı yaz", chat_id="42")

    active = mgr._repo.list_by_type(MemoryType.PREFERENCE, only_active=True)
    assert len(active) == 1, f"tek aktif tercih beklenirdi, {len(active)} bulundu"
    assert "uzun" in active[0].content
    print("  ✓ Tercih ezme (yeni tercih eskiyi supersede etti)")


def _test_chat_no_memory(tmp):
    mgr, pipe = _make(
        str(Path(tmp) / "d.db"),
        intents=[{"action": "chat", "yanit": "İyiyim, sen nasılsın?"}],
        evals=[{"is_temporary": True, "memories": []}],
    )
    pipe.handle("nasılsın?", chat_id="42")
    total = sum(len(mgr._repo.list_by_type(t, only_active=False))
               for t in [MemoryType.IDENTITY, MemoryType.PREFERENCE,
                         MemoryType.EPISODE, MemoryType.KNOWLEDGE])
    assert total == 0
    print("  ✓ Sohbet -> hafıza üretilmedi")


def run():
    with tempfile.TemporaryDirectory() as d:
        _test_task_add(d)
        _test_identity_extraction(d)
        _test_preference_overwrite(d)
        _test_chat_no_memory(d)
    print("OK — Faz 2 tüm testleri geçti.")


def test_phase2():
    run()


if __name__ == "__main__":
    run()
