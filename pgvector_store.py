"""app/infra/vector/pgvector_store.py
======================================
VectorStore'un pgvector implementasyonu.

Her katmanın kendi vektör tablosu vardır (vec_identity, vec_preference,
vec_episode, vec_knowledge, vec_reflection). Böylece PDF'in "katmanları tek
vektör DB'ye karıştırma" kuralı korunur ve arama her zaman type-scoped olur.

Benzerlik: cosine distance (<=>). Skor = 1 - distance (0-1, büyük daha iyi).
"""
from __future__ import annotations

import logging
from typing import Sequence
from uuid import UUID

import psycopg
from pgvector.psycopg import register_vector

from app.core.enums import MemoryType
from app.domain.vector_store import VectorHit, VectorStore

logger = logging.getLogger(__name__)

_VEC_TABLE_BY_TYPE = {
    MemoryType.IDENTITY: "vec_identity",
    MemoryType.PREFERENCE: "vec_preference",
    MemoryType.EPISODE: "vec_episode",
    MemoryType.KNOWLEDGE: "vec_knowledge",
    MemoryType.REFLECTION: "vec_reflection",
}


def _vec_table(mem_type: MemoryType) -> str:
    try:
        return _VEC_TABLE_BY_TYPE[mem_type]
    except KeyError:
        raise ValueError(f"{mem_type} için vektör tablosu yok.")


class PgVectorStore(VectorStore):
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _conn(self) -> psycopg.Connection:
        conn = psycopg.connect(self._dsn)
        register_vector(conn)
        return conn

    def upsert(self, memory_id: UUID, mem_type: MemoryType, embedding: list[float]) -> None:
        table = _vec_table(mem_type)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {table} (memory_id, embedding)
                VALUES (%s, %s)
                ON CONFLICT (memory_id) DO UPDATE SET embedding = EXCLUDED.embedding
                """,
                (memory_id, embedding),
            )

    def delete(self, memory_id: UUID, mem_type: MemoryType) -> None:
        table = _vec_table(mem_type)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {table} WHERE memory_id = %s", (memory_id,))

    def search(
        self,
        embedding: list[float],
        *,
        types: Sequence[MemoryType],
        k: int = 10,
    ) -> list[VectorHit]:
        hits: list[VectorHit] = []
        with self._conn() as conn, conn.cursor() as cur:
            for mem_type in types:
                table = _vec_table(mem_type)
                cur.execute(
                    f"""
                    SELECT memory_id, 1 - (embedding <=> %s) AS score
                    FROM {table}
                    ORDER BY embedding <=> %s
                    LIMIT %s
                    """,
                    (embedding, embedding, k),
                )
                for row in cur.fetchall():
                    hits.append(
                        VectorHit(memory_id=row[0], score=float(row[1]), mem_type=mem_type)
                    )
        # Ham skora göre sırala; metadata re-rank Faz 3'te retrieval katmanında.
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def health_check(self) -> bool:
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() is not None
        except Exception:
            logger.exception("pgvector health_check başarısız.")
            return False
