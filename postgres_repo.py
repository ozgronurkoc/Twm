"""app/infra/db/postgres_repo.py
================================
MemoryRepository'nin Postgres (Supabase) implementasyonu.

PDF: "Katmanları tek tabloda karıştırma." Her katman kendi tablosunda
(mem_identity, mem_preference, mem_episode, mem_knowledge, mem_reflection) ama
ORTAK şemayı paylaşır. Layer 5 (Task) backend'i Notion olduğu için burada
tablosu yoktur.

psycopg (v3) kullanır. Bağlantı bilgisi tamamen env'den (DATABASE_URL) gelir;
bu sayede Nonplo'nun Supabase projesinden bağımsız, AYRI bir proje kullanılır.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.core.enums import MemoryStatus, MemoryType
from app.core.models import Memory
from app.domain.repositories import MemoryRepository

logger = logging.getLogger(__name__)

# type -> tablo adı. Task Notion'da olduğu için burada yok.
_TABLE_BY_TYPE = {
    MemoryType.IDENTITY: "mem_identity",
    MemoryType.PREFERENCE: "mem_preference",
    MemoryType.EPISODE: "mem_episode",
    MemoryType.KNOWLEDGE: "mem_knowledge",
    MemoryType.REFLECTION: "mem_reflection",
}

_COLUMNS = (
    "id, type, title, content, summary, importance, confidence, "
    "created_at, updated_at, last_accessed, access_count, expires_at, "
    "source_conversation, related_memory_ids, tags, status, is_persistent"
)


def _table(mem_type: MemoryType) -> str:
    try:
        return _TABLE_BY_TYPE[mem_type]
    except KeyError:
        raise ValueError(f"{mem_type} için Postgres tablosu yok (Task -> Notion).")


def _row_to_memory(row: dict) -> Memory:
    return Memory(
        id=row["id"],
        type=MemoryType(row["type"]),
        title=row["title"],
        content=row["content"],
        summary=row["summary"],
        importance=row["importance"],
        confidence=row["confidence"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_accessed=row["last_accessed"],
        access_count=row["access_count"],
        expires_at=row["expires_at"],
        source_conversation=row["source_conversation"],
        related_memory_ids=list(row["related_memory_ids"] or []),
        tags=list(row["tags"] or []),
        status=MemoryStatus(row["status"]),
        is_persistent=row["is_persistent"],
        # embedding ilişkisel repo'da taşınmaz; vektör store'un işi.
        embedding=None,
    )


class PostgresMemoryRepository(MemoryRepository):
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _conn(self) -> psycopg.Connection:
        return psycopg.connect(self._dsn, row_factory=dict_row)

    def add(self, memory: Memory) -> Memory:
        table = _table(memory.type)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {table} (
                    id, type, title, content, summary, importance, confidence,
                    created_at, updated_at, last_accessed, access_count,
                    expires_at, source_conversation, related_memory_ids, tags,
                    status, is_persistent
                ) VALUES (
                    %(id)s, %(type)s, %(title)s, %(content)s, %(summary)s,
                    %(importance)s, %(confidence)s, %(created_at)s, %(updated_at)s,
                    %(last_accessed)s, %(access_count)s, %(expires_at)s,
                    %(source_conversation)s, %(related_memory_ids)s, %(tags)s,
                    %(status)s, %(is_persistent)s
                )
                """,
                self._params(memory),
            )
        return memory

    def get(self, memory_id: UUID) -> Optional[Memory]:
        # Hangi katmanda olduğunu bilmediğimiz için tüm tabloları tararız.
        with self._conn() as conn, conn.cursor() as cur:
            for table in _TABLE_BY_TYPE.values():
                cur.execute(
                    f"SELECT {_COLUMNS} FROM {table} WHERE id = %s", (memory_id,)
                )
                row = cur.fetchone()
                if row:
                    return _row_to_memory(row)
        return None

    def update(self, memory: Memory) -> Memory:
        table = _table(memory.type)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {table} SET
                    title=%(title)s, content=%(content)s, summary=%(summary)s,
                    importance=%(importance)s, confidence=%(confidence)s,
                    updated_at=now(), last_accessed=%(last_accessed)s,
                    access_count=%(access_count)s, expires_at=%(expires_at)s,
                    source_conversation=%(source_conversation)s,
                    related_memory_ids=%(related_memory_ids)s, tags=%(tags)s,
                    status=%(status)s, is_persistent=%(is_persistent)s
                WHERE id=%(id)s
                """,
                self._params(memory),
            )
        return memory

    def delete(self, memory_id: UUID, *, hard: bool = False) -> None:
        existing = self.get(memory_id)
        if not existing:
            return
        table = _table(existing.type)
        with self._conn() as conn, conn.cursor() as cur:
            if hard:
                cur.execute(f"DELETE FROM {table} WHERE id = %s", (memory_id,))
            else:
                cur.execute(
                    f"UPDATE {table} SET status = %s, updated_at = now() WHERE id = %s",
                    (MemoryStatus.DELETED.value, memory_id),
                )

    def list_by_type(
        self,
        mem_type: MemoryType,
        *,
        only_active: bool = True,
        persistent_only: bool = False,
        limit: Optional[int] = None,
    ) -> Sequence[Memory]:
        table = _table(mem_type)
        clauses, params = [], []
        if only_active:
            clauses.append("status = %s")
            params.append(MemoryStatus.ACTIVE.value)
        if persistent_only:
            clauses.append("is_persistent = true")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT {_COLUMNS} FROM {table} {where} ORDER BY importance DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [_row_to_memory(r) for r in cur.fetchall()]

    def get_by_ids(self, ids: Sequence[UUID]) -> Sequence[Memory]:
        if not ids:
            return []
        result: list[Memory] = []
        with self._conn() as conn, conn.cursor() as cur:
            for table in _TABLE_BY_TYPE.values():
                cur.execute(
                    f"SELECT {_COLUMNS} FROM {table} WHERE id = ANY(%s)", (list(ids),)
                )
                result.extend(_row_to_memory(r) for r in cur.fetchall())
        return result

    def list_created_between(
        self,
        mem_type: MemoryType,
        start,
        end,
        *,
        only_active: bool = True,
        tag: Optional[str] = None,
    ) -> Sequence[Memory]:
        table = _table(mem_type)
        clauses = ["created_at >= %s", "created_at < %s"]
        params: list = [start, end]
        if only_active:
            clauses.append("status = %s")
            params.append(MemoryStatus.ACTIVE.value)
        if tag:
            clauses.append("%s = ANY(tags)")
            params.append(tag)
        sql = (f"SELECT {_COLUMNS} FROM {table} WHERE {' AND '.join(clauses)} "
               f"ORDER BY created_at ASC")
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [_row_to_memory(r) for r in cur.fetchall()]

    def list_decay_candidates(
        self,
        *,
        max_importance: float,
        max_access_count: int,
        not_accessed_before,
        exclude_types: Sequence[MemoryType] = (),
        limit: Optional[int] = None,
    ) -> Sequence[Memory]:
        excluded = set(exclude_types)
        result: list[Memory] = []
        with self._conn() as conn, conn.cursor() as cur:
            for mem_type, table in _TABLE_BY_TYPE.items():
                if mem_type in excluded:
                    continue
                sql = f"""
                    SELECT {_COLUMNS} FROM {table}
                    WHERE status = %s
                      AND importance <= %s
                      AND access_count <= %s
                      AND COALESCE(last_accessed, created_at) < %s
                      AND is_persistent = false
                    ORDER BY importance ASC
                """
                if limit:
                    sql += f" LIMIT {int(limit)}"
                cur.execute(sql, (
                    MemoryStatus.ACTIVE.value, max_importance,
                    max_access_count, not_accessed_before,
                ))
                result.extend(_row_to_memory(r) for r in cur.fetchall())
        return result

    def list_archived_before(
        self, cutoff, *, exclude_types: Sequence[MemoryType] = ()
    ) -> Sequence[Memory]:
        excluded = set(exclude_types)
        result: list[Memory] = []
        with self._conn() as conn, conn.cursor() as cur:
            for mem_type, table in _TABLE_BY_TYPE.items():
                if mem_type in excluded:
                    continue
                cur.execute(
                    f"SELECT {_COLUMNS} FROM {table} WHERE status = %s AND updated_at < %s",
                    (MemoryStatus.ARCHIVED.value, cutoff),
                )
                result.extend(_row_to_memory(r) for r in cur.fetchall())
        return result

    def health_check(self) -> bool:
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() is not None
        except Exception:
            logger.exception("Postgres health_check başarısız.")
            return False

    @staticmethod
    def _params(m: Memory) -> dict:
        return {
            "id": m.id,
            "type": m.type.value,
            "title": m.title,
            "content": m.content,
            "summary": m.summary,
            "importance": m.importance,
            "confidence": m.confidence,
            "created_at": m.created_at,
            "updated_at": m.updated_at,
            "last_accessed": m.last_accessed,
            "access_count": m.access_count,
            "expires_at": m.expires_at,
            "source_conversation": m.source_conversation,
            "related_memory_ids": [str(x) for x in m.related_memory_ids],
            "tags": m.tags,
            "status": m.status.value,
            "is_persistent": m.is_persistent,
        }
