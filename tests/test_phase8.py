"""tests/test_phase8.py
=======================
Faz 8 testleri (dış servis GEREKTİRMEZ) — şeffaflık:
  1. JSON dışa aktarma: eksiksiz, sürümlü; embedding YER ALMAZ (taşınabilirlik).
  2. Gidiş-dönüş (round-trip): dışa aktar -> içe aktar -> veri korunur.
  3. **TAŞIMA**: bir "sağlayıcıdan" dışa aktarıp BAŞKA bir depoya/embedder'a
     geri yükleme çalışır (PDF: migrate edilebilir olmalı).
  4. Markdown döküm insan-okunur ve gruplanmış.
  5. İstatistik (inceleme yüzeyi).
  6. Komutlar: /hafiza, /hafiza <arama>, /duzenle, /hafiza_sil, /disaktar.
  7. Düzenleme sonrası embedding tazelenir, güven 1.0 olur.
  8. Yedekleme işi dosya üretir.

Çalıştırma:
    python -m tests.test_phase8
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.core.commands import handle_command
from app.core.enums import MemoryType
from app.core.memory_manager import MemoryManager
from app.core.models import MemoryDraft
from app.core.persistent_context import ContextKind
from app.core.transparency import parse_export, to_markdown
from app.infra.db.sqlite_repo import SqliteMemoryRepository
from app.infra.vector.inmemory_store import InMemoryVectorStore
from app.workers import jobs
from tests.fake_llm import ScriptedLLM
from tests.test_phase3 import KeywordEmbedder


class OtherEmbedder(KeywordEmbedder):
    """Farklı boyutlu 'başka sağlayıcı' — taşıma senaryosu için."""

    @property
    def dimension(self) -> int:
        return 4

    def embed(self, text: str) -> list[float]:
        t = text.lower()
        return [1.0 if w in t else 0.0 for w in ("nonplo", "pgvector", "kahve", "x")]


def _make(db_path, embedder=None):
    return MemoryManager(
        repository=SqliteMemoryRepository(db_path),
        vector_store=InMemoryVectorStore(),
        embedder=embedder or KeywordEmbedder(),
        llm=ScriptedLLM(),
    )


def _seed(mgr):
    mgr.create(MemoryDraft(type=MemoryType.IDENTITY, title="Ad",
                           content="Kullanıcının adı Özgür.", importance=0.95))
    mgr.create(MemoryDraft(type=MemoryType.KNOWLEDGE, title="proje",
                           content="Nonplo KOBİlere satış yapar.",
                           tags=["nonplo", "saas"]))
    mgr.create(MemoryDraft(type=MemoryType.EPISODE, title="olay",
                           content="Nonplo ilk reklamını yayınladı.", tags=["nonplo"]))
    mgr.add_persistent_context("Kısa cevap ver.", kind=ContextKind.RULE)


def _test_export_json(tmp):
    mgr = _make(str(Path(tmp) / "a.db"))
    _seed(mgr)

    raw = mgr.export_json(note="test")
    data = json.loads(raw)

    assert data["version"] == 1 and data["count"] == 4
    assert data["note"] == "test" and "exported_at" in data

    for item in data["memories"]:
        # 17 alanın hepsi var mı (embedding hariç)
        for f in ("id", "type", "title", "content", "importance", "confidence",
                  "created_at", "access_count", "related_memory_ids", "tags",
                  "status", "is_persistent"):
            assert f in item, f"{f} eksik"
        # embedding taşınmaz -> sağlayıcı bağımsızlığı
        assert "embedding" not in item
    print("  ✓ JSON dışa aktarma: eksiksiz, sürümlü, embedding'siz")


def _test_roundtrip(tmp):
    mgr = _make(str(Path(tmp) / "b.db"))
    _seed(mgr)
    raw = mgr.export_json()
    original = {m.id: m.content for m in mgr.all_memories()}

    # Aynı sisteme geri yükleme (yedekten dönüş)
    restored = mgr.import_json(raw)
    assert restored == 4
    after = {m.id: m.content for m in mgr.all_memories()}
    assert original == after, "gidiş-dönüş veriyi bozmamalı"
    print("  ✓ Gidiş-dönüş: veri korundu")


def _test_migration_to_other_provider(tmp):
    """KRİTİK: farklı depo + farklı embedding sağlayıcısına taşıma."""
    src = _make(str(Path(tmp) / "c1.db"))
    _seed(src)
    dump = src.export_json()

    # Yepyeni bir depo + FARKLI boyutlu embedder
    dst = _make(str(Path(tmp) / "c2.db"), embedder=OtherEmbedder())
    count = dst.import_json(dump)

    assert count == 4
    contents = {m.content for m in dst.all_memories()}
    assert "Kullanıcının adı Özgür." in contents
    assert "Nonplo KOBİlere satış yapar." in contents

    # Kalıcı bağlam bayrağı da taşındı
    assert len(dst.list_persistent_context()) == 1

    # Yeni embedder ile semantik arama ÇALIŞIYOR (embedding yeniden üretildi)
    hits = dst.semantic_search("nonplo", types=[MemoryType.KNOWLEDGE], k=5)
    assert hits, "taşıma sonrası semantik arama çalışmalıydı"
    print("  ✓ Taşıma: farklı depo + farklı embedding sağlayıcısına geçiş çalıştı")


def _test_markdown_and_stats(tmp):
    mgr = _make(str(Path(tmp) / "d.db"))
    _seed(mgr)

    md = mgr.export_markdown()
    assert "# Hafıza dökümü" in md
    assert "## Kimlik" in md and "## Bilgiler" in md
    assert "Kullanıcının adı Özgür." in md
    assert "önem" in md and "güven" in md  # metadata insan-okunur
    print("  ✓ Markdown döküm: gruplanmış ve insan-okunur")

    stats = mgr.stats()
    assert stats.by_type["identity"] == 1
    assert stats.by_type["knowledge"] == 2  # bilgi + kalıcı kural
    assert stats.persistent == 1
    rendered = stats.render()
    assert "Toplam: 4 kayıt" in rendered
    print("  ✓ İstatistik (inceleme yüzeyi)")


def _test_commands(tmp):
    mgr = _make(str(Path(tmp) / "e.db"))
    _seed(mgr)

    # /hafiza -> istatistik
    r = handle_command(mgr, "/hafiza", "c1")
    assert r.handled and "Toplam" in r.text

    # /hafiza <arama> -> numaralı liste
    r = handle_command(mgr, "/hafiza nonplo", "c1")
    assert r.handled and "sonuç" in r.text and "1." in r.text

    # /duzenle
    r = handle_command(mgr, "/duzenle 1 Nonplo artık dişçilere satış yapıyor.", "c1")
    assert r.handled and "Güncellendi" in r.text
    assert any("dişçilere" in m.content for m in mgr.all_memories())

    # düzenleme sonrası güven 1.0
    edited = [m for m in mgr.all_memories() if "dişçilere" in m.content][0]
    assert edited.confidence == 1.0
    print("  ✓ Komutlar: /hafiza, arama, /duzenle (güven 1.0)")

    # /hafiza_sil
    before = len(mgr.all_memories())
    handle_command(mgr, "/hafiza nonplo", "c1")
    r = handle_command(mgr, "/hafiza_sil 1", "c1")
    assert r.handled and "Unuttum" in r.text
    assert len(mgr.all_memories()) == before - 1

    # listeleme yapılmadan silme -> uyarı
    r = handle_command(mgr, "/hafiza_sil 1", "yeni_sohbet")
    assert "Önce" in r.text
    print("  ✓ Komutlar: /hafiza_sil + liste yokken korumalı uyarı")

    # /disaktar
    r = handle_command(mgr, "/disaktar", "c1")
    assert r.handled and parse_export(r.text)
    r = handle_command(mgr, "/disaktar md", "c1")
    assert r.handled and "# Hafıza dökümü" in r.text
    print("  ✓ Komutlar: /disaktar (JSON + Markdown)")


def _test_backup_job(tmp):
    mgr = _make(str(Path(tmp) / "f.db"))
    _seed(mgr)
    backup_dir = str(Path(tmp) / "backups")
    result = jobs.job_backup(mgr, directory=backup_dir)
    assert result.ok
    files = list(Path(backup_dir).glob("twm-memory-*.json"))
    assert len(files) == 1
    assert parse_export(files[0].read_text(encoding="utf-8"))
    print("  ✓ Yedekleme işi: geri yüklenebilir dosya üretti")


def run():
    with tempfile.TemporaryDirectory() as d:
        _test_export_json(d)
        _test_roundtrip(d)
        _test_migration_to_other_provider(d)
        _test_markdown_and_stats(d)
        _test_commands(d)
        _test_backup_job(d)
    print("OK — Faz 8 tüm testleri geçti.")


def test_phase8():
    run()


if __name__ == "__main__":
    run()
