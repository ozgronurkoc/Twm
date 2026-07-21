"""
memory_service.py
==================
Basit SQLite tabanlı sohbet hafızası.

Her chat_id için son mesajları saklar ki agent, önceki konuşmayı hatırlayarak
cevap verebilsin (JARVIS'in Tony'yi hatırlaması gibi). Bu sayede her mesaj
sıfırdan yorumlanmak yerine, önceki turlar da bağlam olarak modele gönderilir.

NOT: SQLite dosyası deploy ortamının kalıcı olmayan diskinde tutuluyorsa
(örn. Heroku gibi ephemeral filesystem'ler) dyno her yeniden başladığında
hafıza sıfırlanır. Kalıcı hafıza istenirse ileride Postgres/Notion gibi
kalıcı bir depoya taşınması önerilir.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import config

DB_PATH = Path(getattr(config, "MEMORY_DB_PATH", "twm_memory.db"))

# Bir cevap üretirken modele gönderilecek maksimum geçmiş mesaj sayısı.
DEFAULT_HISTORY_LIMIT = 12
# Veritabanında chat başına saklanacak maksimum satır (şişmeyi engellemek için).
MAX_STORED_PER_CHAT = 200


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages (chat_id, id)"
        )


def add_message(chat_id: int | str, role: str, content: str) -> None:
    """Bir mesajı hafızaya ekler ve chat başına satır sayısını sınırlar."""
    if not content:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
            (str(chat_id), role, content),
        )
        # Eski mesajları budayarak veritabanının şişmesini engelle.
        conn.execute(
            """
            DELETE FROM messages
            WHERE chat_id = ? AND id NOT IN (
                SELECT id FROM messages WHERE chat_id = ?
                ORDER BY id DESC LIMIT ?
            )
            """,
            (str(chat_id), str(chat_id), MAX_STORED_PER_CHAT),
        )


def get_recent_messages(chat_id: int | str, limit: int = DEFAULT_HISTORY_LIMIT) -> list[dict]:
    """Son `limit` mesajı, eskiden yeniye doğru sıralı olarak döner.

    Dönen format doğrudan OpenAI chat mesaj formatına uygundur:
    [{"role": "user"/"assistant", "content": "..."}, ...]
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (str(chat_id), limit),
        ).fetchall()
    return [{"role": role, "content": content} for role, content in reversed(rows)]


def clear_history(chat_id: int | str) -> None:
    """Kullanıcı isterse geçmişi sıfırlamak için (ör. /sifirla komutu)."""
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (str(chat_id),))
