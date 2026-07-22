"""app/core/memory_manager.py
==============================
>>> MERKEZİ SİNİR SİSTEMİ <<<

PDF: "Sistemdeki hiçbir bileşen hafıza veritabanından doğrudan okuyamaz ya da
yazamaz. Her modül Memory Manager üzerinden iletişim kurar." Bu sınıf, tüm
hafıza yaşam döngüsünün TEK giriş noktasıdır.

Bağımlılıklar constructor'a INJECT edilir (dependency inversion): sınıf yalnızca
domain arayüzlerini (MemoryRepository, VectorStore, EmbeddingProvider,
LLMProvider) bilir; somut Postgres/OpenAI implementasyonlarını bilmez.

KAPSAM (Faz 7 itibarıyla TAMAMLANDI)
------------------------------------
Bu sınıf artık PDF'teki tüm hafıza yaşam döngüsünü kapsıyor:
create/update/delete, embedding, semantik getirme + metadata re-rank, öncelikli
bağlam enjeksiyonu, değerlendirme + sınıflandırma, dedup/çakışma çözümü,
kalıcı bağlam, reflection konsolidasyonu, forgetting/decay ve knowledge graph.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence
from uuid import UUID

from app.core.enums import (
    ALWAYS_LOADED_TYPES,
    NON_VECTOR_TYPES,
    MemoryStatus,
    MemoryType,
    ReflectionLevel,
    reflection_tag,
)
from app.core.decay import DecayConfig, DecayReport
from app.core.graph import GraphConfig, GraphNeighbor, link, should_link, traverse
from app.core.reflection import (
    find_existing,
    summarize,
    title_for,
    window_for,
)
from app.core.dedup import (
    DedupConfig,
    DuplicateAction,
    DuplicateVerdict,
    decide,
    merge_content,
)
from app.core.dedup import _differs as _differs_text
from app.core.evaluation import EvaluationResult, evaluate
from app.core.injection import build_context_string
from app.core.intent import IntentResult, detect_intent
from app.core.models import Memory, MemoryDraft, Task, TaskMatch
from app.core.persistent_context import (
    DEFAULT_CONFIDENCE,
    DEFAULT_IMPORTANCE,
    ContextKind,
    kind_of,
    kind_tag,
)
from app.core.retrieval import RankedMemory, RetrievalConfig, RetrievedContext, rerank
from app.core.transparency import (
    MemoryStats,
    compute_stats,
    parse_export,
    to_json,
    to_markdown,
)
from app.domain.embeddings import EmbeddingProvider
from app.domain.llm import LLMProvider
from app.domain.repositories import MemoryRepository
from app.domain.tasks import TaskRepository
from app.domain.vector_store import VectorStore

logger = logging.getLogger(__name__)


class MemoryManager:
    def __init__(
        self,
        *,
        repository: MemoryRepository,
        vector_store: VectorStore,
        embedder: EmbeddingProvider,
        llm: LLMProvider,
        task_repository: Optional[TaskRepository] = None,
        retrieval_config: Optional[RetrievalConfig] = None,
        dedup_config: Optional[DedupConfig] = None,
        decay_config: Optional[DecayConfig] = None,
        graph_config: Optional[GraphConfig] = None,
    ) -> None:
        self._repo = repository
        self._vectors = vector_store
        self._embedder = embedder
        self._llm = llm
        self._tasks = task_repository
        self._rcfg = retrieval_config or RetrievalConfig()
        self._dcfg = dedup_config or DedupConfig()
        self._decay_cfg = decay_config or DecayConfig()
        self._gcfg = graph_config or GraphConfig()

    def _require_tasks(self) -> TaskRepository:
        if self._tasks is None:
            raise RuntimeError("Task katmanı (Notion) yapılandırılmamış.")
        return self._tasks

    # ------------------------------------------------------------------ #
    # YARATMA / GÜNCELLEME / SİLME  (Faz 0 — çalışıyor)
    # ------------------------------------------------------------------ #
    def create(self, draft: MemoryDraft) -> Memory:
        """Taslaktan kalıcı hafıza üretir: embedding + kayıt + vektör index.

        NOT: Bu metot ham mesajı değil, zaten değerlendirilmiş/sınıflandırılmış
        bir taslağı alır. (Evaluation/classification pipeline'ın işi — Faz 2.)
        """
        embedding: Optional[list[float]] = None
        if draft.type not in NON_VECTOR_TYPES:
            embedding = self._embedder.embed(draft.summary or draft.content)

        memory = Memory.from_draft(draft, embedding=embedding)
        stored = self._repo.add(memory)

        if embedding is not None:
            self._vectors.upsert(stored.id, stored.type, embedding)

        # Graf: yeni hafızayı ilişkili olanlara bağla (kademeli graf oluşumu).
        # Hata graf dışındaki akışı bozmamalı.
        try:
            self.link_new_memory(stored)
        except Exception:
            logger.exception("Graf bağlama başarısız id=%s", stored.id)

        logger.info("Hafıza oluşturuldu id=%s type=%s", stored.id, stored.type.value)
        return stored

    def get(self, memory_id: UUID) -> Optional[Memory]:
        return self._repo.get(memory_id)

    def update(self, memory: Memory) -> Memory:
        updated = self._repo.update(memory)
        if updated.embedding is not None and updated.type not in NON_VECTOR_TYPES:
            self._vectors.upsert(updated.id, updated.type, updated.embedding)
        return updated

    def delete(self, memory_id: UUID, *, hard: bool = False) -> None:
        existing = self._repo.get(memory_id)
        # PDF: Identity neredeyse hiç silinmez -> hard delete koruması.
        if existing and existing.type is MemoryType.IDENTITY and hard:
            raise PermissionError("Identity hafızaları kalıcı olarak silinemez.")
        if existing and existing.type not in NON_VECTOR_TYPES:
            self._vectors.delete(memory_id, existing.type)
        self._repo.delete(memory_id, hard=hard)

    # ------------------------------------------------------------------ #
    # EMBEDDING  (Faz 0 — çalışıyor)
    # ------------------------------------------------------------------ #
    def embed(self, text: str) -> list[float]:
        return self._embedder.embed(text)

    # ------------------------------------------------------------------ #
    # HER ZAMAN YÜKLENEN BAĞLAM  (Faz 0 — çalışıyor)
    # PDF: Identity + Preference deterministik yüklenir (semantik değil).
    # ------------------------------------------------------------------ #
    def load_always_on(self) -> list[Memory]:
        items: list[Memory] = []
        for t in ALWAYS_LOADED_TYPES:
            items.extend(self._repo.list_by_type(t, only_active=True))
        return items

    # ------------------------------------------------------------------ #
    # DEDUP + ÇAKIŞMA ÇÖZÜMÜ  (Faz 5 — çalışıyor)
    # ------------------------------------------------------------------ #
    def detect_duplicates(self, candidate: MemoryDraft) -> DuplicateVerdict:
        """Taslağa en yakın mevcut hafızayı bulup karar üretir (yazma yapmaz)."""
        if candidate.type in NON_VECTOR_TYPES:
            return DuplicateVerdict(DuplicateAction.NONE)

        text = candidate.summary or candidate.content
        embedding = self._embedder.embed(text)
        hits = self._vectors.search(
            embedding, types=[candidate.type], k=self._rcfg.k_per_type
        )

        best: Optional[Memory] = None
        best_score = 0.0

        if hits:
            by_id = {m.id: m for m in self._repo.get_by_ids([h.memory_id for h in hits])}
            for h in hits:
                m = by_id.get(h.memory_id)
                if m and m.status is MemoryStatus.ACTIVE and h.score > best_score:
                    best, best_score = m, h.score

        if best is None:
            # YEDEK: vektör araması sonuç vermediyse (vektör store devre dışı,
            # embedding henüz üretilmemiş vb.) aynı katmanda BAŞLIK eşleşmesine
            # düş. Özellikle tercih ezme, semantik aramaya bağımlı kalmamalı.
            best = self._find_by_title(candidate.type, candidate.title)
            if best is not None:
                best_score = 1.0 if not _differs_text(best.content, candidate.content) else 0.85

        return decide(
            new_type=candidate.type,
            new_content=candidate.content,
            new_confidence=candidate.confidence,
            existing=best,
            similarity=best_score,
            should_overwrite=candidate.should_overwrite,
            cfg=self._dcfg,
        )

    def _find_by_title(self, mem_type: MemoryType, title: str) -> Optional[Memory]:
        norm = " ".join((title or "").lower().split())
        if not norm:
            return None
        for m in self._repo.list_by_type(mem_type, only_active=True):
            if " ".join(m.title.lower().split()) == norm:
                return m
        return None

    def resolve_conflict(self, verdict: DuplicateVerdict, draft: MemoryDraft) -> Optional[Memory]:
        """Kararı uygular. Dönen değer: kalıcı olan hafıza (SKIP'te mevcut olan)."""
        action, existing = verdict.action, verdict.existing

        if action is DuplicateAction.NONE or existing is None:
            return self.create(draft)

        if action is DuplicateAction.SKIP:
            # Zaten biliniyor: yeni kayıt açma, mevcudu güçlendir.
            existing.confidence = max(existing.confidence, draft.confidence)
            existing.importance = max(existing.importance, draft.importance)
            existing.touch_accessed()
            logger.info("Dedup SKIP id=%s (%s)", existing.id, verdict.reason)
            return self.update(existing)

        if action is DuplicateAction.UPDATE:
            existing.content = draft.content
            existing.summary = draft.summary or existing.summary
            existing.confidence = draft.confidence
            existing.importance = max(existing.importance, draft.importance)
            existing.tags = sorted(set(existing.tags) | set(draft.tags))
            existing.embedding = self._embedder.embed(existing.summary or existing.content)
            logger.info("Dedup UPDATE id=%s (%s)", existing.id, verdict.reason)
            return self.update(existing)

        if action is DuplicateAction.MERGE:
            existing.content = verdict.merged_content or merge_content(
                existing.content, draft.content
            )
            existing.summary = existing.summary or draft.summary
            existing.confidence = max(existing.confidence, draft.confidence)
            existing.importance = max(existing.importance, draft.importance)
            existing.tags = sorted(set(existing.tags) | set(draft.tags))
            existing.embedding = self._embedder.embed(existing.summary or existing.content)
            logger.info("Dedup MERGE id=%s (%s)", existing.id, verdict.reason)
            return self.update(existing)

        if action is DuplicateAction.SUPERSEDE:
            # PDF: çakışan kalıcı hafızalar bir arada tutulmaz.
            existing.status = MemoryStatus.SUPERSEDED
            self._repo.update(existing)
            self._vectors.delete(existing.id, existing.type)
            created = self.create(draft)
            # Graf ilişkisi: yeni kayıt eskisine referans versin (Faz 7 zemini).
            created.related_memory_ids = list(
                set(created.related_memory_ids) | {existing.id}
            )
            logger.info(
                "Dedup SUPERSEDE eski=%s yeni=%s (%s)",
                existing.id, created.id, verdict.reason,
            )
            return self.update(created)

        return self.create(draft)

    def create_or_resolve(self, draft: MemoryDraft) -> Optional[Memory]:
        """Dedup kontrolünden geçirerek hafıza yazar.

        Pipeline (evaluate_and_store) bunu kullanır; ham `create` ise dedup'sız
        doğrudan yazım içindir (ör. reflection üretimi, kalıcı bağlam).
        """
        verdict = self.detect_duplicates(draft)
        return self.resolve_conflict(verdict, draft)

    # ------------------------------------------------------------------ #
    # KALICI BAĞLAM (Faz 4 — çalışıyor)
    # PDF: kalıcı bilgi ile deneyim ayrıdır; bunlar alakadan bağımsız yüklenir.
    # ------------------------------------------------------------------ #
    def add_persistent_context(
        self,
        content: str,
        *,
        kind: ContextKind = ContextKind.RULE,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> Memory:
        all_tags = list(tags or [])
        if kind_tag(kind) not in all_tags:
            all_tags.append(kind_tag(kind))
        draft = MemoryDraft(
            type=MemoryType.KNOWLEDGE,
            title=title or f"{kind.value.capitalize()}",
            content=content,
            summary=summary,
            importance=DEFAULT_IMPORTANCE,
            confidence=DEFAULT_CONFIDENCE,
            tags=all_tags,
            is_persistent=True,
        )
        return self.create(draft)

    def list_persistent_context(
        self, kind: Optional[ContextKind] = None
    ) -> list[Memory]:
        items = list(self._repo.list_by_type(
            MemoryType.KNOWLEDGE, only_active=True, persistent_only=True
        ))
        if kind is not None:
            items = [m for m in items if kind_of(m.tags) is kind]
        return items

    def unpin_persistent_context(self, memory_id: UUID) -> Optional[Memory]:
        """Kalıcı bağlamdan çıkarır ama hafızayı SİLMEZ — sıradan bilgiye döner."""
        m = self._repo.get(memory_id)
        if not m:
            return None
        m.is_persistent = False
        return self.update(m)

    def pin_persistent_context(self, memory_id: UUID) -> Optional[Memory]:
        """Var olan bir hafızayı kalıcı bağlama yükseltir."""
        m = self._repo.get(memory_id)
        if not m:
            return None
        m.is_persistent = True
        return self.update(m)

    def remove_persistent_context(self, memory_id: UUID) -> None:
        self.delete(memory_id, hard=False)

    # ------------------------------------------------------------------ #
    # RETRIEVAL + INJECTION  (Faz 3 — çalışıyor)
    # ------------------------------------------------------------------ #
    def _semantic(
        self, embedding: list[float], mem_type: MemoryType, k: int
    ) -> list[RankedMemory]:
        """Bir katmanda vektör araması + metadata re-rank + alaka eşiği."""
        hits = self._vectors.search(embedding, types=[mem_type], k=k)
        if not hits:
            return []
        by_id = {m.id: m for m in self._repo.get_by_ids([h.memory_id for h in hits])}
        candidates: list[tuple[Memory, float]] = []
        for h in hits:
            m = by_id.get(h.memory_id)
            # Eşiğin altındaki ham benzerlikler daha en baştan elenir.
            if m and m.status is MemoryStatus.ACTIVE and h.score >= self._rcfg.min_similarity:
                candidates.append((m, h.score))
        ranked = rerank(candidates, cfg=self._rcfg)
        # Re-rank sonrası nihai skor eşiği: kota dolmasa bile zayıf olan girmez.
        return [r for r in ranked if r.final_score >= self._rcfg.min_final_score]

    def semantic_search(
        self, query: str, *, types: Sequence[MemoryType], k: int = 10
    ) -> list[RankedMemory]:
        embedding = self._embedder.embed(query)
        out: list[RankedMemory] = []
        for t in types:
            out.extend(self._semantic(embedding, t, k))
        out.sort(key=lambda r: r.final_score, reverse=True)
        return out[:k]

    def retrieve_for_context(self, message: str, chat_id: str) -> RetrievedContext:
        embedding = self._embedder.embed(message)
        cfg = self._rcfg

        # 1-2) Her zaman yüklenen: Identity + Preferences (semantik değil)
        identity = list(self._repo.list_by_type(MemoryType.IDENTITY, only_active=True))
        preferences = list(self._repo.list_by_type(MemoryType.PREFERENCE, only_active=True))

        # Kalıcı (persistent) knowledge da her zaman yüklenir (Claude Projects ilhamı)
        persistent = list(self._repo.list_by_type(
            MemoryType.KNOWLEDGE, only_active=True, persistent_only=True
        ))
        persistent_ids = {m.id for m in persistent}

        # 4-6) Semantik: Knowledge / Episode / Reflection
        knowledge = [
            r for r in self._semantic(embedding, MemoryType.KNOWLEDGE, cfg.k_per_type)
            if r.memory.id not in persistent_ids
        ][: cfg.max_knowledge]
        episodes = self._semantic(embedding, MemoryType.EPISODE, cfg.k_per_type)[: cfg.max_episodes]
        reflections = self._semantic(embedding, MemoryType.REFLECTION, cfg.k_per_type)[: cfg.max_reflections]

        # 3) Aktif görevler (Notion) — varsa
        active_tasks = self.active_tasks_for_context() if self._tasks else []

        # 7) GRAF GENİŞLETME: semantik sonuçları tohum alıp ilişkili hafızalara
        # 1-2 hop uzan. Zaten bağlamda olanları ve kalıcıları hariç tut.
        already = (
            {m.id for m in identity} | {m.id for m in preferences}
            | persistent_ids
            | {r.memory.id for r in knowledge + episodes + reflections}
        )
        seeds = [(r.memory, r.final_score) for r in knowledge + episodes + reflections]
        graph_related = [
            n for n in self.expand_by_graph(seeds) if n.memory.id not in already
        ]

        return RetrievedContext(
            identity=identity,
            preferences=preferences,
            persistent=persistent,
            active_tasks=active_tasks,
            knowledge=knowledge,
            episodes=episodes,
            reflections=reflections,
            graph_related=graph_related,
        )

    def build_context(self, message: str, chat_id: str) -> str:
        ctx = self.retrieve_for_context(message, chat_id)
        # Erişim istatistiklerini güncelle (PDF: enjekte edilince last_accessed +
        # access_count). Tekilleştirilmiş liste üzerinden.
        for m in ctx.injected_memories():
            m.touch_accessed()
            try:
                self._repo.update(m)
            except Exception:  # istatistik güncellemesi yanıtı bloklamamalı
                logger.exception("Erişim istatistiği güncellenemedi id=%s", m.id)
        return build_context_string(ctx)

    # ------------------------------------------------------------------ #
    # PIPELINE — Intent + Evaluation (Faz 2 — çalışıyor)
    # ------------------------------------------------------------------ #
    def interpret_intent(
        self, message: str, history: Optional[list[dict]] = None,
        context: Optional[str] = None,
    ) -> IntentResult:
        """Pipeline adım 1: mesajı yapısal niyete çevir (görev/sohbet)."""
        return detect_intent(self._llm, message, history, context)

    def evaluate_and_store(
        self, message: str, chat_id: str, *, known_context: Optional[str] = None
    ) -> list[Memory]:
        """Pipeline adım 2-6: değerlendir -> sınıflandır -> depola -> embed -> index.

        Ham mesaj ASLA yazılmaz; yalnızca değerlendirmeden çıkan yapılandırılmış
        hafızalar kalıcılaştırılır. Sohbet/geçici/zaten bilinen -> boş liste.
        """
        if known_context is None:
            known_context = self._known_context_snapshot()

        result: EvaluationResult = evaluate(self._llm, message, known_context)
        stored: list[Memory] = []
        for em in result.memories:
            draft = MemoryDraft(
                type=em.type,
                title=em.title,
                content=em.content,
                summary=em.summary,
                importance=em.importance,
                confidence=em.confidence,
                tags=em.tags,
                is_persistent=em.is_persistent,
                source_conversation=str(chat_id),
                should_overwrite=em.should_overwrite,
            )
            # PDF: her yazım dedup/çakışma çözümünden geçer.
            resolved = self.create_or_resolve(draft)
            if resolved is not None:
                stored.append(resolved)

        logger.info(
            "Değerlendirme: mesajdan %d hafıza üretildi (task=%s).",
            len(stored), result.should_be_task,
        )
        return stored

    def _known_context_snapshot(self, limit_per_type: int = 20) -> str:
        """already_known / should_overwrite muhakemesi için mevcut kimlik ve
        tercihlerin kısa bir özetini üretir."""
        lines: list[str] = []
        for t in ALWAYS_LOADED_TYPES:
            for m in self._repo.list_by_type(t, only_active=True, limit=limit_per_type):
                lines.append(f"- ({t.value}) {m.title}: {m.summary or m.content}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # LAYER 5 — TASK  (Faz 1 — çalışıyor, backend Notion)
    # PDF: Notion Integration da Memory Manager üzerinden geçer.
    # ------------------------------------------------------------------ #
    def add_task(self, text: str, date_str: str, *, oncelik: str, kategori: str) -> Task:
        return self._require_tasks().add(text, date_str, oncelik=oncelik, kategori=kategori)

    def complete_task(self, text: str, date_str: Optional[str] = None) -> TaskMatch:
        return self._require_tasks().set_status(text, "Yapıldı", date_str)

    def cancel_task(self, text: str, date_str: Optional[str] = None) -> TaskMatch:
        return self._require_tasks().set_status(text, "İptal", date_str)

    def add_task_note(self, text: str, note: str, date_str: Optional[str] = None) -> TaskMatch:
        return self._require_tasks().add_note(text, note, date_str)

    def list_tasks_for_date(self, date_str: str) -> list[Task]:
        return list(self._require_tasks().list_for_date(date_str))

    def list_overdue_tasks(self, before_date_str: str) -> list[Task]:
        return list(self._require_tasks().list_overdue(before_date_str))

    def list_tasks_range(self, start_date_str: str, end_date_str: str) -> list[Task]:
        return list(self._require_tasks().list_range(start_date_str, end_date_str))

    def list_open_tasks(self) -> list[Task]:
        return list(self._require_tasks().list_all_open())

    def active_tasks_for_context(self) -> list[Task]:
        """Bağlam enjeksiyonunda kullanılacak aktif görevler (Faz 3'te çağrılır)."""
        return self.list_open_tasks()

    # ------------------------------------------------------------------ #
    # SAĞLIK
    # ------------------------------------------------------------------ #
    def health_check(self) -> dict[str, bool]:
        health = {
            "repository": self._repo.health_check(),
            "vector_store": self._vectors.health_check(),
        }
        if self._tasks is not None:
            health["tasks"] = self._tasks.health_check()
        return health

    # ------------------------------------------------------------------ #
    # REFLECTION ENGINE  (Faz 6 — çalışıyor)
    # PDF: günlük -> haftalık -> aylık -> çeyreklik konsolidasyon.
    # ------------------------------------------------------------------ #
    def run_reflection(
        self, level: ReflectionLevel | str, *, ref: Optional[datetime] = None
    ) -> Optional[Memory]:
        """Bir seviye için özet üretir/günceller. Kaynak yoksa None döner."""
        level = ReflectionLevel(level) if isinstance(level, str) else level
        ref = ref or datetime.now(timezone.utc)
        win = window_for(level, ref)

        # Kaynaklar: DAILY -> episode'lar; diğerleri -> bir alt seviye özetleri.
        src_level = level.source_level
        if src_level is None:
            sources = list(self._repo.list_created_between(
                MemoryType.EPISODE, win.start, win.end, only_active=True
            ))
        else:
            sources = list(self._repo.list_created_between(
                MemoryType.REFLECTION, win.start, win.end,
                only_active=True, tag=reflection_tag(src_level),
            ))

        if not sources:
            logger.info("Reflection %s (%s): kaynak yok.", level.value, win.label)
            return None

        text = summarize(self._llm, sources, level, win.label)
        if not text:
            return None

        tag = reflection_tag(level)
        title = title_for(level, win.label)
        source_ids = [m.id for m in sources]

        # Idempotency: aynı pencere için özet varsa güncelle, yenisini açma.
        existing_all = list(self._repo.list_created_between(
            MemoryType.REFLECTION, win.start, win.end, only_active=True, tag=tag
        ))
        existing = find_existing(existing_all, level, win.label)

        if existing is not None:
            existing.content = text
            existing.summary = text
            existing.related_memory_ids = sorted(
                set(existing.related_memory_ids) | set(source_ids)
            )
            existing.embedding = self._embedder.embed(text)
            logger.info("Reflection %s (%s) güncellendi.", level.value, win.label)
            return self.update(existing)

        draft = MemoryDraft(
            type=MemoryType.REFLECTION,
            title=title,
            content=text,
            summary=text,
            importance=0.6,
            confidence=0.8,
            tags=[tag],
            related_memory_ids=source_ids,
        )
        created = self.create(draft)  # reflection dedup'a girmez
        logger.info(
            "Reflection %s (%s) üretildi: %d kaynak -> 1 özet.",
            level.value, win.label, len(sources),
        )
        return created

    def run_all_reflections(self, *, ref: Optional[datetime] = None) -> dict[str, bool]:
        """Zinciri sırayla çalıştırır (günlük -> çeyreklik)."""
        out: dict[str, bool] = {}
        for lvl in (ReflectionLevel.DAILY, ReflectionLevel.WEEKLY,
                    ReflectionLevel.MONTHLY, ReflectionLevel.QUARTERLY):
            out[lvl.value] = self.run_reflection(lvl, ref=ref) is not None
        return out

    # ------------------------------------------------------------------ #
    # FORGETTING / DECAY  (Faz 6 — çalışıyor)
    # PDF: önce arşivle, saklama süresi dolunca sil. Identity korunur.
    # ------------------------------------------------------------------ #
    def decay_and_archive(self, *, now: Optional[datetime] = None) -> DecayReport:
        cfg = self._decay_cfg
        now = now or datetime.now(timezone.utc)
        report = DecayReport()

        # 1) ARŞİVLEME: düşük önem + düşük erişim + uzun süredir dokunulmamış
        stale_cutoff = now - timedelta(days=cfg.stale_after_days)
        candidates = self._repo.list_decay_candidates(
            max_importance=cfg.max_importance,
            max_access_count=cfg.max_access_count,
            not_accessed_before=stale_cutoff,
            exclude_types=cfg.protected_types,
            limit=cfg.batch_limit,
        )
        for m in candidates:
            if m.is_persistent or m.type in cfg.protected_types:
                continue  # ikinci güvenlik kontrolü
            m.status = MemoryStatus.ARCHIVED
            m.updated_at = now
            self._repo.update(m)
            # Arşivlenen kayıt semantik aramadan çıkar (bağlamı kirletmesin).
            if m.type not in NON_VECTOR_TYPES:
                self._vectors.delete(m.id, m.type)
            report.archived.append(m.id)

        # 2) KALICI SİLME: arşivde saklama süresini doldurmuşlar
        retention_cutoff = now - timedelta(days=cfg.retention_days)
        expired = self._repo.list_archived_before(
            retention_cutoff, exclude_types=cfg.protected_types
        )
        for m in expired:
            if m.type is MemoryType.IDENTITY:
                continue  # PDF: Identity kalıcı olarak silinmez
            self._repo.delete(m.id, hard=True)
            report.deleted.append(m.id)

        logger.info("Decay turu tamamlandı: %s", report.summary)
        return report

    # ------------------------------------------------------------------ #
    # ŞEFFAFLIK  (Faz 8 — çalışıyor)
    # PDF: hafızalar incelenebilir, dışa aktarılabilir, yedeklenebilir,
    # düzenlenebilir ve taşınabilir olmalı.
    # ------------------------------------------------------------------ #
    def all_memories(
        self,
        *,
        types: Optional[Sequence[MemoryType]] = None,
        include_inactive: bool = False,
        limit_per_type: Optional[int] = None,
    ) -> list[Memory]:
        """Tüm hafızaları (Task hariç — o Notion'da) tek listede döner."""
        wanted = types or [
            MemoryType.IDENTITY, MemoryType.PREFERENCE, MemoryType.EPISODE,
            MemoryType.KNOWLEDGE, MemoryType.REFLECTION,
        ]
        out: list[Memory] = []
        for t in wanted:
            if t in NON_VECTOR_TYPES:
                continue
            out.extend(self._repo.list_by_type(
                t, only_active=not include_inactive, limit=limit_per_type
            ))
        return out

    def stats(self, *, include_inactive: bool = True) -> MemoryStats:
        return compute_stats(self.all_memories(include_inactive=include_inactive))

    def export_json(self, *, include_inactive: bool = False, note: str = "") -> str:
        return to_json(
            self.all_memories(include_inactive=include_inactive), note=note
        )

    def export_markdown(self, *, include_inactive: bool = False) -> str:
        return to_markdown(self.all_memories(include_inactive=include_inactive))

    def import_json(self, raw: str, *, reindex: bool = True) -> int:
        """Dışa aktarılmış veriyi geri yükler (yedekten dönüş / taşıma).

        Var olan id'ler güncellenir, olmayanlar eklenir. Embedding'ler dışa
        aktarılmadığı için içerikten yeniden üretilir — bu sayede FARKLI bir
        embedding sağlayıcısına taşımak da mümkündür.
        """
        restored = 0
        for m in parse_export(raw):
            existing = self._repo.get(m.id)
            if existing is None:
                self._repo.add(m)
            else:
                self._repo.update(m)
            if reindex and m.type not in NON_VECTOR_TYPES:
                text = m.summary or m.content
                if text:
                    try:
                        self._vectors.upsert(m.id, m.type, self._embedder.embed(text))
                    except Exception:
                        logger.exception("İçe aktarma sırasında indeksleme başarısız id=%s", m.id)
            restored += 1
        logger.info("İçe aktarma tamamlandı: %d kayıt.", restored)
        return restored

    def edit_memory(
        self,
        memory_id: UUID,
        *,
        content: Optional[str] = None,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        importance: Optional[float] = None,
        tags: Optional[list[str]] = None,
    ) -> Optional[Memory]:
        """Kullanıcının bir hafızayı elle düzeltmesi (PDF: düzenlenebilir olmalı)."""
        m = self._repo.get(memory_id)
        if m is None:
            return None
        if title is not None:
            m.title = title
        if content is not None:
            m.content = content
        if summary is not None:
            m.summary = summary
        if importance is not None:
            m.importance = max(0.0, min(1.0, importance))
        if tags is not None:
            m.tags = list(tags)
        # İçerik değiştiyse embedding tazelenmeli.
        if content is not None or summary is not None:
            try:
                m.embedding = self._embedder.embed(m.summary or m.content)
            except Exception:
                logger.exception("Düzenleme sonrası embedding üretilemedi id=%s", memory_id)
        # Kullanıcı elle düzelttiyse güven tamdır.
        m.confidence = 1.0
        return self.update(m)

    # ------------------------------------------------------------------ #
    # KNOWLEDGE GRAPH  (Faz 7 — çalışıyor)
    # PDF: hafızalar birbirine referans versin; getirme graf gezinmesinden de
    # yararlansın.
    # ------------------------------------------------------------------ #
    def link_memories(self, a_id: UUID, b_id: UUID) -> bool:
        """İki hafıza arasında elle çift yönlü kenar kurar."""
        a, b = self._repo.get(a_id), self._repo.get(b_id)
        if a is None or b is None:
            return False
        if link(a, b, cfg=self._gcfg):
            self._repo.update(a)
            self._repo.update(b)
            return True
        return False

    def link_new_memory(self, memory: Memory) -> int:
        """Yeni yazılan bir hafızayı grafa bağlar (etiket + semantik yakınlık).

        Yazım anında çağrılır; böylece graf kademeli olarak kendiliğinden oluşur.
        Dönen değer: kurulan kenar sayısı.
        """
        if memory.type in NON_VECTOR_TYPES:
            return 0

        cfg = self._gcfg
        text = memory.summary or memory.content
        candidates: dict[UUID, tuple[Memory, Optional[float]]] = {}

        # 1) Semantik komşular (dedup eşiğinin ALTINDA kalanlar: ilişkili ama aynı değil)
        try:
            embedding = self._embedder.embed(text)
            hits = self._vectors.search(
                embedding, types=[memory.type], k=self._rcfg.k_per_type
            )
            fetched = {m.id: m for m in self._repo.get_by_ids([h.memory_id for h in hits])}
            for h in hits:
                m = fetched.get(h.memory_id)
                if m and m.id != memory.id and m.status is MemoryStatus.ACTIVE:
                    candidates[m.id] = (m, h.score)
        except Exception:
            logger.exception("Graf: semantik komşu araması başarısız.")

        # 2) Etiket örtüşmesi — katmanlar arası da olabilir (proje -> teknoloji)
        if memory.tags:
            for mem_type in (MemoryType.KNOWLEDGE, MemoryType.EPISODE,
                             MemoryType.IDENTITY):
                for m in self._repo.list_by_type(mem_type, only_active=True, limit=100):
                    if m.id != memory.id and m.id not in candidates:
                        candidates[m.id] = (m, None)

        edges = 0
        for other, sim in candidates.values():
            if should_link(memory, other, similarity=sim, cfg=cfg):
                if link(memory, other, cfg=cfg):
                    self._repo.update(other)
                    edges += 1

        if edges:
            self._repo.update(memory)
            logger.info("Graf: %s için %d kenar kuruldu.", memory.id, edges)
        return edges

    def maintain_graph(self) -> int:
        """Tüm aktif hafızalar üzerinde eksik kenarları tamamlar (worker işi)."""
        total = 0
        for mem_type in (MemoryType.KNOWLEDGE, MemoryType.EPISODE,
                         MemoryType.IDENTITY, MemoryType.REFLECTION):
            for m in self._repo.list_by_type(mem_type, only_active=True, limit=200):
                total += self.link_new_memory(m)
        logger.info("Graf bakımı tamamlandı: %d yeni kenar.", total)
        return total

    def expand_by_graph(
        self, seeds: list[tuple[Memory, float]]
    ) -> list[GraphNeighbor]:
        """Semantik tohumlardan graf üzerinden genişler."""
        if not seeds:
            return []

        def fetch(ids: list[UUID]) -> list[Memory]:
            return [
                m for m in self._repo.get_by_ids(ids)
                if m.status is MemoryStatus.ACTIVE
            ]

        return traverse(seeds, fetch, cfg=self._gcfg)
