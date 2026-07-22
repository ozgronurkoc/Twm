"""tests/test_phase3.py
=======================
Faz 3 testleri (dış servis GEREKTİRMEZ) — retrieval + injection:
  1. Semantik getirme: sorguya en yakın knowledge hafızası bulunur (gerçek cosine).
  2. Re-rank: metadata (importance) skoru etkiler.
  3. Enjeksiyon: bağlam PDF öncelik sırasıyla kurulur (Identity -> Preference ->
     Aktif görev -> Bilgi -> Olay -> Özet).
  4. Erişim istatistiği: enjekte edilen hafızanın access_count'u artar,
     last_accessed güncellenir.

Deterministik embedding için basit anahtar-kelime tabanlı sahte embedder
kullanılır (gerçek cosine bunun üstünde çalışır).

Çalıştırma:
    python -m tests.test_phase3
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.core.enums import MemoryType
from app.core.executor import InlineExecutor
from app.core.memory_manager import MemoryManager
from app.core.models import MemoryDraft
from app.core.pipeline import MessagePipeline
from app.core.retrieval import RetrievalConfig
from app.domain.embeddings import EmbeddingProvider
from app.infra.db.sqlite_repo import SqliteMemoryRepository
from app.infra.notion.notion_repo import NotionTaskRepository
from app.infra.vector.inmemory_store import InMemoryVectorStore
from tests.fake_llm import ScriptedLLM
from tests.fake_notion import FakeNotionClient

# Küçük bir kavram sözlüğü üstünden deterministik "embedding".
_VOCAB = ["nonplo", "reklam", "meta", "pgvector", "cosine", "kahve", "spor", "istanbul"]


class KeywordEmbedder(EmbeddingProvider):
    @property
    def dimension(self) -> int:
        return len(_VOCAB)

    def embed(self, text: str) -> list[float]:
        t = text.lower()
        vec = [1.0 if w in t else 0.0 for w in _VOCAB]
        # sıfır vektör olmasın
        if not any(vec):
            vec[0] = 0.01
        return vec

    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


def _make(db_path):
    mgr = MemoryManager(
        repository=SqliteMemoryRepository(db_path),
        vector_store=InMemoryVectorStore(),
        embedder=KeywordEmbedder(),
        llm=ScriptedLLM(),
        task_repository=NotionTaskRepository(api_key="x", page_id="p", client=FakeNotionClient()),
        retrieval_config=RetrievalConfig(max_knowledge=3, max_episodes=2),
    )
    return mgr


def _test_semantic_and_rerank(tmp):
    mgr = _make(str(Path(tmp) / "a.db"))
    # iki bilgi: biri pgvector, biri kahve
    mgr.create(MemoryDraft(type=MemoryType.KNOWLEDGE, title="db",
                           content="pgvector cosine <=> operatörü kullanır.",
                           tags=["pgvector", "cosine"]))
    mgr.create(MemoryDraft(type=MemoryType.KNOWLEDGE, title="içecek",
                           content="Kullanıcı sabah kahve içer.", tags=["kahve"]))

    hits = mgr.semantic_search("pgvector nasıl çalışır", types=[MemoryType.KNOWLEDGE], k=5)
    assert hits, "semantik sonuç bekleniyordu"
    assert "pgvector" in hits[0].memory.content, "en alakalı sonuç pgvector olmalı"
    print("  ✓ Semantik getirme en alakalı hafızayı buldu")

    # re-rank: importance farkı skoru etkiliyor mu (aynı benzerlikte)
    from datetime import datetime, timezone
    from app.core.retrieval import rerank
    m_hi = mgr.create(MemoryDraft(type=MemoryType.EPISODE, title="a", content="olay meta", importance=0.9))
    m_lo = mgr.create(MemoryDraft(type=MemoryType.EPISODE, title="b", content="olay meta", importance=0.1))
    ranked = rerank([(m_lo, 0.8), (m_hi, 0.8)], cfg=mgr._rcfg, now=datetime.now(timezone.utc))
    assert ranked[0].memory.id == m_hi.id, "yüksek importance öne gelmeli"
    print("  ✓ Metadata re-rank (importance) sıralamayı etkiliyor")


def _test_injection_order_and_access(tmp):
    mgr = _make(str(Path(tmp) / "b.db"))
    mgr.create(MemoryDraft(type=MemoryType.IDENTITY, title="Şehir",
                           content="Kullanıcı İstanbul'da yaşıyor.", tags=["istanbul"]))
    mgr.create(MemoryDraft(type=MemoryType.PREFERENCE, title="Ton",
                           content="Kullanıcı kısa yanıt ister."))
    know = mgr.create(MemoryDraft(type=MemoryType.KNOWLEDGE, title="proje",
                                  content="Nonplo Meta reklamı yayınladı.",
                                  tags=["nonplo", "meta", "reklam"]))
    mgr.add_task("faturayı öde", "2026-07-22", oncelik="🔴 Yüksek", kategori="🏠 Ev")

    ctx_str = mgr.build_context("nonplo meta reklamı ne durumda", chat_id="1")

    # öncelik sırası: kimlik, tercih, görev, bilgi başlıkları bu sırada
    order = [ctx_str.index(h) for h in
             ["Kullanıcı kimliği", "Tercihler", "Aktif görevler", "İlgili bilgiler"]]
    assert order == sorted(order), "enjeksiyon PDF öncelik sırasında olmalı"
    print("  ✓ Enjeksiyon öncelik sırası doğru")

    # erişim istatistiği: knowledge enjekte edildi -> access_count arttı
    refreshed = mgr.get(know.id)
    assert refreshed.access_count >= 1 and refreshed.last_accessed is not None
    print("  ✓ Erişim istatistiği güncellendi (access_count, last_accessed)")


def _test_pipeline_uses_context(tmp):
    # pipeline build_context çağırıyor ve intent'e bağlam geçiyor mu (akış kırılmıyor mu)
    mgr = _make(str(Path(tmp) / "c.db"))
    mgr.create(MemoryDraft(type=MemoryType.IDENTITY, title="Ad", content="Kullanıcının adı Özgür."))
    pipe = MessagePipeline(mgr, executor=InlineExecutor())
    # ScriptedLLM boş kuyrukla chat döner
    res = pipe.handle("selam", chat_id="1")
    assert res.text  # akış çalıştı
    print("  ✓ Pipeline retrieval->intent akışı çalışıyor")


def run():
    with tempfile.TemporaryDirectory() as d:
        _test_semantic_and_rerank(d)
        _test_injection_order_and_access(d)
        _test_pipeline_uses_context(d)
    print("OK — Faz 3 tüm testleri geçti.")


def test_phase3():
    run()


if __name__ == "__main__":
    run()
