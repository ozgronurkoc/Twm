"""tests/test_phase7.py
=======================
Faz 7 testleri (dış servis GEREKTİRMEZ) — knowledge graph:
  1. Kenar kurma kuralları: etiket örtüşmesi / semantik aralık / aynı-şey hariç.
  2. Kenarlar ÇİFT YÖNLÜ kurulur.
  3. Otomatik bağlama: yeni hafıza yazılınca graf kendiliğinden oluşur.
  4. Gezinme: 1-2 hop, skor sönümlemesi, tohumlar sonuçta yer almaz.
  5. **KRİTİK**: semantik aramanın BULAMADIĞI ama zincirle bağlı olan hafıza
     bağlama girer (PDF: "getirme graf gezinmesinden de yararlanmalı").
  6. Worker: graf bakımı işi çalışır.

Çalıştırma:
    python -m tests.test_phase7
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.core.enums import MemoryType
from app.core.graph import GraphConfig, link, should_link, traverse
from app.core.memory_manager import MemoryManager
from app.core.models import Memory, MemoryDraft
from app.core.retrieval import RetrievalConfig
from app.infra.db.sqlite_repo import SqliteMemoryRepository
from app.infra.vector.inmemory_store import InMemoryVectorStore
from app.workers import jobs
from tests.fake_llm import ScriptedLLM
from tests.test_phase3 import KeywordEmbedder


def _m(title, content, tags=None) -> Memory:
    return Memory(type=MemoryType.KNOWLEDGE, title=title, content=content,
                  tags=tags or [])


def _test_link_rules():
    cfg = GraphConfig()

    # etiket örtüşmesi >= 2 -> bağla
    a = _m("a", "Nonplo projesi", ["nonplo", "saas"])
    b = _m("b", "Nonplo mimarisi", ["nonplo", "saas", "python"])
    assert should_link(a, b, similarity=None, cfg=cfg)

    # tek ortak etiket -> bağlama
    c = _m("c", "Kahve", ["nonplo"])
    assert not should_link(a, c, similarity=None, cfg=cfg)

    # semantik aralık içinde -> bağla
    assert should_link(a, c, similarity=0.70, cfg=cfg)

    # çok yüksek benzerlik = aynı şey -> graf değil DEDUP'ın işi
    assert not should_link(a, c, similarity=0.95, cfg=cfg)

    # çok düşük benzerlik -> bağlama
    assert not should_link(a, c, similarity=0.20, cfg=cfg)

    # işaret etiketleri (yansima:, kalici:) sayılmamalı
    d = _m("d", "x", ["yansima:daily", "kalici:kural"])
    e = _m("e", "y", ["yansima:daily", "kalici:kural"])
    assert not should_link(d, e, similarity=None, cfg=cfg)
    print("  ✓ Kenar kurma kuralları (etiket / semantik aralık / dedup ayrımı)")


def _test_bidirectional():
    cfg = GraphConfig()
    a, b = _m("a", "A"), _m("b", "B")
    assert link(a, b, cfg=cfg)
    assert b.id in a.related_memory_ids and a.id in b.related_memory_ids
    # tekrar çağrı değişiklik üretmez
    assert not link(a, b, cfg=cfg)
    print("  ✓ Kenarlar çift yönlü ve idempotent")


def _make(db_path, **kw):
    return MemoryManager(
        repository=SqliteMemoryRepository(db_path),
        vector_store=InMemoryVectorStore(),
        embedder=KeywordEmbedder(),
        llm=ScriptedLLM(),
        retrieval_config=RetrievalConfig(),
        **kw,
    )


def _test_auto_linking(tmp):
    mgr = _make(str(Path(tmp) / "a.db"))
    first = mgr.create(MemoryDraft(
        type=MemoryType.KNOWLEDGE, title="proje",
        content="Nonplo bir reklam platformu.", tags=["nonplo", "reklam"]))
    second = mgr.create(MemoryDraft(
        type=MemoryType.KNOWLEDGE, title="ekip",
        content="Nonplo ekibi reklam kampanyası yürütüyor.", tags=["nonplo", "reklam"]))

    a, b = mgr.get(first.id), mgr.get(second.id)
    assert b.id in a.related_memory_ids, "yazım anında kenar kurulmalıydı"
    assert a.id in b.related_memory_ids
    print("  ✓ Otomatik bağlama: yeni hafıza grafa kendiliğinden eklendi")


def _test_traversal():
    cfg = GraphConfig(max_hops=2, hop_decay=0.5, max_expanded=10)
    # zincir: seed -> n1 -> n2
    seed, n1, n2 = _m("s", "S"), _m("n1", "N1"), _m("n2", "N2")
    link(seed, n1, cfg=cfg)
    link(n1, n2, cfg=cfg)
    store = {m.id: m for m in (seed, n1, n2)}

    def fetch(ids):
        return [store[i] for i in ids if i in store]

    out = traverse([(seed, 1.0)], fetch, cfg=cfg)
    ids = [n.memory.id for n in out]

    assert seed.id not in ids, "tohum sonuçta yer almamalı"
    assert n1.id in ids and n2.id in ids, "1 ve 2 hop uzaktakiler bulunmalı"

    by_id = {n.memory.id: n for n in out}
    assert by_id[n1.id].hops == 1 and by_id[n2.id].hops == 2
    # sönümleme: uzak olan daha düşük skorlu
    assert by_id[n1.id].score > by_id[n2.id].score
    assert abs(by_id[n1.id].score - 0.5) < 1e-9
    assert abs(by_id[n2.id].score - 0.25) < 1e-9
    print("  ✓ Gezinme: 1-2 hop, skor sönümlemesi, tohum hariç")


def _test_retrieval_finds_unreachable_by_semantics(tmp):
    """KRİTİK: semantik aramanın bulamadığı ama zincirle bağlı hafıza,
    graf sayesinde bağlama girmeli."""
    mgr = _make(str(Path(tmp) / "b.db"))

    # Sorguyla eşleşecek olan (embedder 'pgvector' kelimesini görüyor)
    hit = mgr.create(MemoryDraft(
        type=MemoryType.KNOWLEDGE, title="db",
        content="pgvector cosine araması yapar.", tags=["pgvector", "altyapi"]))

    # Sorguyla HİÇ kelime ortaklığı yok -> semantik olarak bulunamaz.
    # Ama ortak etiketlerle hit'e bağlı.
    linked = mgr.create(MemoryDraft(
        type=MemoryType.KNOWLEDGE, title="karar",
        content="Veritabanı barındırma kararı Supabase yönünde alındı.",
        tags=["pgvector", "altyapi"]))

    # bağlantı kuruldu mu
    assert linked.id in mgr.get(hit.id).related_memory_ids

    ctx = mgr.build_context("pgvector nasıl çalışır", chat_id="1")

    assert "pgvector cosine" in ctx, "doğrudan alakalı kayıt bağlamda olmalı"
    assert "Supabase" in ctx, "graf üzerinden bağlantılı kayıt bağlama girmeliydi"
    assert "## Bağlantılı bilgiler" in ctx, "graf sonuçları ayrı blokta olmalı"
    print("  ✓ Semantik olarak bulunamayan ilişkili hafıza graf ile bulundu")


def _test_graph_job(tmp):
    mgr = _make(str(Path(tmp) / "c.db"))
    mgr.create(MemoryDraft(type=MemoryType.KNOWLEDGE, title="a",
                           content="Nonplo reklam.", tags=["nonplo", "reklam"]))
    mgr.create(MemoryDraft(type=MemoryType.KNOWLEDGE, title="b",
                           content="Nonplo meta reklam.", tags=["nonplo", "reklam"]))
    result = jobs.job_graph_maintenance(mgr)
    assert result.ok, result.detail
    print("  ✓ Worker: graf bakımı işi çalıştı")


def run():
    _test_link_rules()
    _test_bidirectional()
    _test_traversal()
    with tempfile.TemporaryDirectory() as d:
        _test_auto_linking(d)
        _test_retrieval_finds_unreachable_by_semantics(d)
        _test_graph_job(d)
    print("OK — Faz 7 tüm testleri geçti.")


def test_phase7():
    run()


if __name__ == "__main__":
    run()
