"""tests/test_phase6.py
=======================
Faz 6 testleri (dış servis GEREKTİRMEZ) — reflection + decay + worker'lar:
  1. Pencere hesabı (günlük/haftalık/aylık/çeyreklik).
  2. Günlük özet: episode'lardan üretilir, kaynaklara graf referansı tutar.
  3. Konsolidasyon zinciri: günlük -> haftalık (haftalık, GÜNLÜK özetlerden beslenir).
  4. Idempotency: aynı pencere ikinci kez çalışınca yeni kayıt AÇILMAZ.
  5. Decay: düşük önem + düşük erişim + eskimiş -> ARŞİV; saklama süresi
     dolunca kalıcı SİLME.
  6. **Koruma**: Identity ve kalıcı bağlam asla arşivlenmez/silinmez.
  7. Worker turu: tüm işler çökmeden çalışır.

Çalıştırma:
    python -m tests.test_phase6
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.decay import DecayConfig
from app.core.enums import (
    MemoryStatus,
    MemoryType,
    ReflectionLevel,
    reflection_tag,
)
from app.core.memory_manager import MemoryManager
from app.core.models import MemoryDraft
from app.core.persistent_context import ContextKind
from app.core.reflection import window_for
from app.domain.llm import LLMProvider
from app.infra.db.sqlite_repo import SqliteMemoryRepository
from app.infra.vector.inmemory_store import InMemoryVectorStore
from app.workers import jobs
from tests.test_phase3 import KeywordEmbedder


class SummarizingLLM(LLMProvider):
    """Özet üretiyormuş gibi davranan sahte LLM: girdiyi sayıp sabit metin döner."""

    def __init__(self):
        self.calls = 0

    def complete(self, *, system, messages, temperature=0.0):
        self.calls += 1
        body = messages[-1]["content"]
        n = body.count("\n- ")
        return f"Bu dönemde {n} önemli gelişme yaşandı ve nonplo üzerinde ilerleme kaydedildi."

    def complete_structured(self, *, system, messages, tool_schema, tool_name, temperature=0.0):
        return {"memories": []}


def _make(db_path, **kw):
    return MemoryManager(
        repository=SqliteMemoryRepository(db_path),
        vector_store=InMemoryVectorStore(),
        embedder=KeywordEmbedder(),
        llm=SummarizingLLM(),
        **kw,
    )


def _test_windows():
    ref = datetime(2026, 7, 22, 15, 30, tzinfo=timezone.utc)  # Çarşamba
    d = window_for(ReflectionLevel.DAILY, ref)
    assert d.label == "2026-07-22" and (d.end - d.start).days == 1

    w = window_for(ReflectionLevel.WEEKLY, ref)
    assert w.start.strftime("%Y-%m-%d") == "2026-07-20"  # Pazartesi
    assert (w.end - w.start).days == 7

    m = window_for(ReflectionLevel.MONTHLY, ref)
    assert m.label == "2026-07" and m.start.day == 1

    q = window_for(ReflectionLevel.QUARTERLY, ref)
    assert q.label == "2026-Ç3" and q.start.month == 7
    print("  ✓ Pencere hesabı (günlük/haftalık/aylık/çeyreklik)")


def _test_daily_reflection(tmp):
    mgr = _make(str(Path(tmp) / "a.db"))
    ids = []
    for text in ["Nonplo ilk reklamı yayınladı.", "Nonplo demo videosu çekildi.",
                 "Nonplo ilk müşteri görüşmesi yapıldı."]:
        ids.append(mgr.create(MemoryDraft(
            type=MemoryType.EPISODE, title="olay", content=text, tags=["nonplo"]
        )).id)

    summary = mgr.run_reflection(ReflectionLevel.DAILY)
    assert summary is not None
    assert summary.type is MemoryType.REFLECTION
    assert reflection_tag(ReflectionLevel.DAILY) in summary.tags
    # kaynaklara graf referansı
    assert set(ids).issubset(set(summary.related_memory_ids))
    print("  ✓ Günlük özet üretildi + kaynaklara graf referansı")

    # 4) idempotency: tekrar çalıştır -> yeni kayıt açılmaz
    again = mgr.run_reflection(ReflectionLevel.DAILY)
    reflections = mgr._repo.list_by_type(MemoryType.REFLECTION, only_active=True)
    assert len(reflections) == 1, f"tek özet beklenirdi, {len(reflections)} var"
    assert again.id == summary.id
    print("  ✓ Idempotency: aynı pencere ikinci kez yeni kayıt açmadı")


def _test_chain_daily_to_weekly(tmp):
    """Haftalık özet, episode'lardan DEĞİL, günlük özetlerden beslenmeli."""
    mgr = _make(str(Path(tmp) / "b.db"))
    # Bu haftaya ait iki günlük özet oluştur (elle, doğru etiketle)
    for label in ["2026-07-20", "2026-07-21"]:
        mgr.create(MemoryDraft(
            type=MemoryType.REFLECTION,
            title=f"Günlük özet — {label}",
            content=f"{label} gününde nonplo çalışmaları sürdü.",
            tags=[reflection_tag(ReflectionLevel.DAILY)],
        ))
    # Aynı haftaya ait bir de episode — haftalık özet bunu KAYNAK ALMAMALI
    mgr.create(MemoryDraft(type=MemoryType.EPISODE, title="olay",
                           content="Alakasız bir olay."))

    weekly = mgr.run_reflection(ReflectionLevel.WEEKLY)
    assert weekly is not None
    assert reflection_tag(ReflectionLevel.WEEKLY) in weekly.tags
    # kaynak sayısı 2 (iki günlük özet), episode dahil değil
    assert len(weekly.related_memory_ids) == 2
    print("  ✓ Zincir: haftalık özet günlük özetlerden beslendi (episode'dan değil)")


def _test_decay_lifecycle(tmp):
    mgr = _make(
        str(Path(tmp) / "c.db"),
        decay_config=DecayConfig(max_importance=0.35, max_access_count=1,
                                 stale_after_days=90, retention_days=180),
    )
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    old = now - timedelta(days=200)

    # Arşivlenmeli: düşük önem, hiç erişilmemiş, eski
    weak = mgr.create(MemoryDraft(type=MemoryType.KNOWLEDGE, title="zayıf",
                                  content="Önemsiz eski bilgi.", importance=0.1))
    weak.created_at = old
    weak.last_accessed = None
    mgr._repo.update(weak)

    # Arşivlenmemeli: yüksek önem
    strong = mgr.create(MemoryDraft(type=MemoryType.KNOWLEDGE, title="güçlü",
                                    content="Kritik bilgi.", importance=0.9))
    strong.created_at = old
    mgr._repo.update(strong)

    report = mgr.decay_and_archive(now=now)
    assert weak.id in report.archived, "zayıf kayıt arşivlenmeliydi"
    assert strong.id not in report.archived, "önemli kayıt arşivlenmemeliydi"
    assert mgr.get(weak.id).status is MemoryStatus.ARCHIVED
    print("  ✓ Decay: zayıf kayıt arşivlendi, önemli kayıt korundu")

    # Saklama süresi dolmuş arşiv -> kalıcı silme
    archived = mgr.get(weak.id)
    archived.updated_at = now - timedelta(days=200)
    mgr._repo.update(archived)
    report2 = mgr.decay_and_archive(now=now)
    assert weak.id in report2.deleted, "saklama süresi dolan arşiv silinmeliydi"
    assert mgr.get(weak.id) is None
    print("  ✓ Decay: saklama süresi dolan arşiv kalıcı silindi")


def _test_protections(tmp):
    mgr = _make(
        str(Path(tmp) / "d.db"),
        decay_config=DecayConfig(max_importance=1.0, max_access_count=999,
                                 stale_after_days=0, retention_days=0),
    )
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    old = now - timedelta(days=500)

    ident = mgr.create(MemoryDraft(type=MemoryType.IDENTITY, title="Ad",
                                   content="Kullanıcının adı Özgür.", importance=0.1))
    pref = mgr.create(MemoryDraft(type=MemoryType.PREFERENCE, title="Ton",
                                  content="Kısa yaz.", importance=0.1))
    pinned = mgr.add_persistent_context("Emoji kullanma.", kind=ContextKind.RULE)
    for m in (ident, pref, pinned):
        m.created_at = old
        mgr._repo.update(m)

    report = mgr.decay_and_archive(now=now)
    # Eşikler her şeyi yakalayacak kadar gevşek olmasına rağmen hiçbiri düşmemeli
    for m in (ident, pref, pinned):
        assert m.id not in report.archived, f"{m.type} arşivlenmemeliydi"
        assert mgr.get(m.id).status is MemoryStatus.ACTIVE
    print("  ✓ Koruma: Identity, Preference ve kalıcı bağlam decay edilmedi")


def _test_worker_round(tmp):
    mgr = _make(str(Path(tmp) / "e.db"))
    mgr.create(MemoryDraft(type=MemoryType.EPISODE, title="olay",
                           content="Nonplo bugün yeni bir özellik yayınladı."))
    results = jobs.run_nightly(mgr)
    # Faz 8'de yedekleme işi eklendi -> 6 iş.
    assert len(results) == 6, [r.name for r in results]
    assert all(r.ok for r in results), [r for r in results if not r.ok]
    names = {r.name for r in results}
    assert "reflection_daily" in names and "decay_archive" in names
    print("  ✓ Gecelik worker turu: 6 iş de hatasız çalıştı")


def run():
    _test_windows()
    with tempfile.TemporaryDirectory() as d:
        _test_daily_reflection(d)
        _test_chain_daily_to_weekly(d)
        _test_decay_lifecycle(d)
        _test_protections(d)
        _test_worker_round(d)
    print("OK — Faz 6 tüm testleri geçti.")


def test_phase6():
    run()


if __name__ == "__main__":
    run()
