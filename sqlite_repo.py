"""app/infra/db/sqlite_repo.py
==============================
MemoryRepository'nin SQLite implementasyonu — yalnızca YEREL GELİŞTİRME için.

Production'da kullanılmaz (ephemeral disk + vektör araması yok). Amaç: Postgres
kurmadan mimariyi/akışı lokalde denemek. Provider-bağımsızlık sayesinde tek bir
env değişkeniyle (DB_PROVIDER=sqlite) devreye girer.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional, Sequence
from uuid import UUID

from app.core.enums import MemoryStatus, MemoryType
from app.core.models import Memory
from app.domain.repositories import MemoryRepository

_FIELDS = (
    "id", "type", "title", "content", "summary", "importance", "confidence",
    "created_at", "updated_at", "last_accessed", "access_count", "expires_at",
    "source_conversation", "related_memory_ids", "tags", "status", "is_persistent",
)


class SqliteMemoryRepository(MemoryRepository):
    def __init__(self, path: str = "twm_dev.db") -> None:
        self._path = Path(path)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT,
                    importance REAL NOT NULL DEFAULT 0.5,
                    confidence REAL NOT NULL DEFAULT 0.7,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_accessed TEXT,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    expires_at TEXT,
                    source_conversation TEXT,
                    related_memory_ids TEXT NOT NULL DEFAULT '[]',
                    tags TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'active',
                    is_persistent INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def add(self, memory: Memory) -> Memory:
        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO memories ({','.join(_FIELDS)}) "
                f"VALUES ({','.join('?' for _ in _FIELDS)})",
                self._row(memory),
            )
        return memory

    def get(self, memory_id: UUID) -> Optional[Memory]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ?", (str(memory_id),)
            ).fetchone()
        return self._to_memory(row) if row else None

    def update(self, memory: Memory) -> Memory:
        with self._conn() as conn:
            assignments = ",".join(f"{f}=?" for f in _FIELDS if f != "id")
            values = [v for f, v in zip(_FIELDS, self._row(memory)) if f != "id"]
            conn.execute(
                f"UPDATE memories SET {assignments} WHERE id=?",
                (*values, str(memory.id)),
            )
        return memory

    def delete(self, memory_id: UUID, *, hard: bool = False) -> None:
        with self._conn() as conn:
            if hard:
                conn.execute("DELETE FROM memories WHERE id = ?", (str(memory_id),))
            else:
                conn.execute(
                    "UPDATE memories SET status = ? WHERE id = ?",
                    (MemoryStatus.DELETED.value, str(memory_id)),
                )

    def list_by_type(
        self,
        mem_type: MemoryType,
        *,
        only_active: bool = True,
        persistent_only: bool = False,
        limit: Optional[int] = None,
    ) -> Sequence[Memory]:
        clauses = ["type = ?"]
        params: list = [mem_type.value]
        if only_active:
            clauses.append("status = ?")
            params.append(MemoryStatus.ACTIVE.value)
        if persistent_only:
            clauses.append("is_persistent = 1")
        sql = (
            f"SELECT * FROM memories WHERE {' AND '.join(clauses)} "
            f"ORDER BY importance DESC"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._to_memory(r) for r in rows]

    def get_by_ids(self, ids: Sequence[UUID]) -> Sequence[Memory]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM memories WHERE id IN ({placeholders})",
                [str(i) for i in ids],
            ).fetchall()
        return [self._to_memory(r) for r in rows]

    def list_created_between(
        self,
        mem_type: MemoryType,
        start,
        end,
        *,
        only_active: bool = True,
        tag: Optional[str] = None,
    ) -> Sequence[Memory]:
        clauses = ["type = ?", "created_at >= ?", "created_at < ?"]
        params: list = [mem_type.value, start.isoformat(), end.isoformat()]
        if only_active:
            clauses.append("status = ?")
            params.append(MemoryStatus.ACTIVE.value)
        sql = (f"SELECT * FROM memories WHERE {' AND '.join(clauses)} "
               f"ORDER BY created_at ASC")
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        items = [self._to_memory(r) for r in rows]
        if tag:
            items = [m for m in items if tag in m.tags]
        return items

    def list_decay_candidates(
        self,
        *,
        max_importance: float,
        max_access_count: int,
        not_accessed_before,
        exclude_types: Sequence[MemoryType] = (),
        limit: Optional[int] = None,
    ) -> Sequence[Memory]:
        clauses = [
            "status = ?",
            "importance <= ?",
            "access_count <= ?",
            "COALESCE(last_accessed, created_at) < ?",
        ]
        params: list = [
            MemoryStatus.ACTIVE.value, max_importance, max_access_count,
            not_accessed_before.isoformat(),
        ]
        # Kalıcı bağlam asla decay edilmez.
        clauses.append("is_persistent = 0")
        for t in exclude_types:
            clauses.append("type != ?")
            params.append(t.value)
        sql = f"SELECT * FROM memories WHERE {' AND '.join(clauses)} ORDER BY importance ASC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._to_memory(r) for r in rows]

    def list_archived_before(
        self, cutoff, *, exclude_types: Sequence[MemoryType] = ()
    ) -> Sequence[Memory]:
        clauses = ["status = ?", "updated_at < ?"]
        params: list = [MemoryStatus.ARCHIVED.value, cutoff.isoformat()]
        for t in exclude_types:
            clauses.append("type != ?")
            params.append(t.value)
        sql = f"SELECT * FROM memories WHERE {' AND '.join(clauses)}"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._to_memory(r) for r in rows]

    def health_check(self) -> bool:
        try:
            with self._conn() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    @staticmethod
    def _row(m: Memory) -> tuple:
        return (
            str(m.id), m.type.value, m.title, m.content, m.summary,
            m.importance, m.confidence, m.created_at.isoformat(),
            m.updated_at.isoformat(),
            m.last_accessed.isoformat() if m.last_accessed else None,
            m.access_count,
            m.expires_at.isoformat() if m.expires_at else None,
            m.source_conversation,
            json.dumps([str(x) for x in m.related_memory_ids]),
            json.dumps(m.tags), m.status.value, int(m.is_persistent),
        )

    @staticmethod
    def _to_memory(row: sqlite3.Row) -> Memory:
        from datetime import datetime

        def _dt(v):
            return datetime.fromisoformat(v) if v else None

        return Memory(
            id=UUID(row["id"]),
            type=MemoryType(row["type"]),
            title=row["title"],
            content=row["content"],
            summary=row["summary"],
            importance=row["importance"],
            confidence=row["confidence"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
            last_accessed=_dt(row["last_accessed"]),
            access_count=row["access_count"],
            expires_at=_dt(row["expires_at"]),
            source_conversation=row["source_conversation"],
            related_memory_ids=[UUID(x) for x in json.loads(row["related_memory_ids"])],
            tags=json.loads(row["tags"]),
            status=MemoryStatus(row["status"]),
            is_persistent=bool(row["is_persistent"]),
            embedding=None,
        )
