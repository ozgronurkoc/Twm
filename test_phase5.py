"""tests/test_phase5.py
=======================
Faz 5 testleri (dış servis GEREKTİRMEZ) — dedup + çakışma çözümü:
  1. Karar mantığı (saf): NONE / SKIP / UPDATE / MERGE / SUPERSEDE.
  2. SKIP: aynı bilgi tekrar gelince yeni kayıt AÇILMAZ, mevcut güçlenir.
  3. SUPERSEDE: tercih değişince eskisi superseded olur, aktif tek kayıt kalır
     ve yeni kayıt eskisine graf referansı tutar.
  4. MERGE: aynı konuda ek bilgi tek kayıtta birleşir.
  5. Çakışan kalıcı hafıza bir arada tutulmaz (aktif set tutarlı).

Çalıştırma:
    python -m tests.test_phase5
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.core.dedup import DedupConfig, DuplicateAction, decide, merge_content
from app.core.enums import MemoryStatus, MemoryType
from app.core.memory_manager import MemoryManager
from app.core.models import Memory, MemoryDraft
from app.core.retrieval import RetrievalConfig
from app.infra.db.sqlite_repo import SqliteMemoryRepository
from app.infra.vector.inmemory_store import InMemoryVectorStore
from tests.fake_llm import ScriptedLLM
from tests.test_phase3 import KeywordEmbedder


def _mem(t: MemoryType, content: str, conf: float = 0.7) -> Memory:
    return Memory(type=t, title="x", content=content, confidence=conf)


def _test_decision_logic():
    cfg = DedupConfig()

    # 1) benzerlik düşük -> çakışma yok
    v = decide(new_type=MemoryType.KNOWLEDGE, new_content="a", new_confidence=0.8,
               existing=_mem(MemoryType.KNOWLEDGE, "b"), similarity=0.30, cfg=cfg)
    assert v.action is DuplicateAction.NONE

    # 2) neredeyse aynı + yeni daha güvenilir -> UPDATE
    v = decide(new_type=MemoryType.KNOWLEDGE, new_content="a", new_confidence=0.95,
               existing=_mem(MemoryType.KNOWLEDGE, "a", conf=0.6), similarity=0.98, cfg=cfg)
    assert v.action is DuplicateAction.UPDATE

    # 2b) neredeyse aynı + güven farkı yok -> SKIP
    v = decide(new_type=MemoryType.KNOWLEDGE, new_content="a", new_confidence=0.7,
               existing=_mem(MemoryType.KNOWLEDGE, "a", conf=0.7), similarity=0.98, cfg=cfg)
    assert v.action is DuplicateAction.SKIP

    # 3) tercih -> SUPERSEDE
    v = decide(new_type=MemoryType.PREFERENCE, new_content="uzun yaz", new_confidence=0.8,
               existing=_mem(MemoryType.PREFERENCE, "kısa yaz"), similarity=0.88, cfg=cfg)
    assert v.action is DuplicateAction.SUPERSEDE

    # 3b) kimlik + yeni güvenilir -> UPDATE
    v = decide(new_type=MemoryType.IDENTITY, new_content="Ankara'da yaşıyor", new_confidence=0.9,
               existing=_mem(MemoryType.IDENTITY, "İstanbul'da yaşıyor", conf=0.7),
               similarity=0.88, cfg=cfg)
    assert v.action is DuplicateAction.UPDATE

    # 3c) bilgi, aynı konu -> MERGE
    v = decide(new_type=MemoryType.KNOWLEDGE, new_content="Nonplo KOBİlere satar.",
               new_confidence=0.7,
               existing=_mem(MemoryType.KNOWLEDGE, "Nonplo bir SaaS platformu."),
               similarity=0.87, cfg=cfg)
    assert v.action is DuplicateAction.MERGE and v.merged_content
    print("  ✓ Karar mantığı: NONE/SKIP/UPDATE/MERGE/SUPERSEDE")

    # merge yardımcısı tekrar yazmıyor
    assert merge_content("A.", "A.") == "A."
    assert "A." in merge_content("A.", "B.") and "B." in merge_content("A.", "B.")
    print("  ✓ merge_content tekrarı önlüyor")


def _make(db_path, **kw):
    return MemoryManager(
        repository=SqliteMemoryRepository(db_path),
        vector_store=InMemoryVectorStore(),
        embedder=KeywordEmbedder(),
        llm=ScriptedLLM(),
        retrieval_config=RetrievalConfig(),
        **kw,
    )


def _test_skip_no_duplicate_record(tmp):
    mgr = _make(str(Path(tmp) / "a.db"))
    d1 = MemoryDraft(type=MemoryType.KNOWLEDGE, title="db",
                     content="pgvector cosine kullanır.", tags=["pgvector", "cosine"],
                     confidence=0.7)
    first = mgr.create_or_resolve(d1)

    # Aynı bilgi tekrar (aynı anahtar kelimeler -> yüksek benzerlik)
    d2 = MemoryDraft(type=MemoryType.KNOWLEDGE, title="db",
                     content="pgvector cosine kullanır.", tags=["pgvector", "cosine"],
                     confidence=0.7)
    second = mgr.create_or_resolve(d2)

    assert second.id == first.id, "yeni kayıt açılmamalıydı (SKIP)"
    active = mgr._repo.list_by_type(MemoryType.KNOWLEDGE, only_active=True)
    assert len(active) == 1, f"tek kayıt beklenirdi, {len(active)} var"
    print("  ✓ SKIP: aynı bilgi tekrarında yeni kayıt açılmadı")


def _test_supersede_preference(tmp):
    mgr = _make(str(Path(tmp) / "b.db"))
    old = mgr.create_or_resolve(MemoryDraft(
        type=MemoryType.PREFERENCE, title="ton",
        content="Kullanıcı kahve hakkında kısa yanıt ister.", tags=["kahve"],
    ))
    new = mgr.create_or_resolve(MemoryDraft(
        type=MemoryType.PREFERENCE, title="ton",
        content="Kullanıcı kahve hakkında uzun yanıt ister.", tags=["kahve"],
        should_overwrite=True,
    ))

    assert new.id != old.id
    active = mgr._repo.list_by_type(MemoryType.PREFERENCE, only_active=True)
    assert len(active) == 1, "çakışan kalıcı tercih bir arada tutulmamalı"
    assert "uzun" in active[0].content

    # eskisi superseded
    stale = mgr.get(old.id)
    assert stale.status is MemoryStatus.SUPERSEDED
    # graf referansı kuruldu
    assert old.id in mgr.get(new.id).related_memory_ids
    print("  ✓ SUPERSEDE: eskisi superseded, aktif tek kayıt, graf referansı var")


def _test_merge_knowledge(tmp):
    mgr = _make(str(Path(tmp) / "c.db"),
                dedup_config=DedupConfig(near_duplicate=0.70, identical=0.99))
    first = mgr.create_or_resolve(MemoryDraft(
        type=MemoryType.KNOWLEDGE, title="proje",
        content="Nonplo bir reklam platformu.", tags=["nonplo", "reklam"],
    ))
    second = mgr.create_or_resolve(MemoryDraft(
        type=MemoryType.KNOWLEDGE, title="proje",
        content="Nonplo meta reklam yönetir.", tags=["nonplo", "reklam", "meta"],
    ))

    assert second.id == first.id, "birleştirme mevcut kayıtta olmalı"
    merged = mgr.get(first.id)
    assert "platformu" in merged.content and "meta" in merged.content.lower()
    assert "meta" in merged.tags  # etiketler birleşti
    active = mgr._repo.list_by_type(MemoryType.KNOWLEDGE, only_active=True)
    assert len(active) == 1
    print("  ✓ MERGE: bilgiler ve etiketler tek kayıtta birleşti")


def run():
    _test_decision_logic()
    with tempfile.TemporaryDirectory() as d:
        _test_skip_no_duplicate_record(d)
        _test_supersede_preference(d)
        _test_merge_knowledge(d)
    print("OK — Faz 5 tüm testleri geçti.")


def test_phase5():
    run()


if __name__ == "__main__":
    run()
