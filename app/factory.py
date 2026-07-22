"""app/factory.py
=================
DEPENDENCY INJECTION birleştiricisi.

Config'teki sağlayıcı seçimlerine göre somut infra implementasyonlarını kurar ve
MemoryManager'a inject eder. Uygulamanın geri kalanı yalnızca `build_memory_manager()`
çağırır; hangi Postgres/OpenAI implementasyonunun kullanıldığını bilmez.

Bu dosya, somut sınıfların (infra) bilindiği TEK yerdir. core/ katmanı temiz kalır.

DÜZELTME: `_build_llm()` artık `config.REASONING_EFFORT`'u da OpenAILLMProvider'a
geçiriyor (gpt-5.6-luna gibi reasoning modelleri için gerekli).
"""
from __future__ import annotations

import config
from app.core.memory_manager import MemoryManager
from app.domain.embeddings import EmbeddingProvider
from app.domain.llm import LLMProvider
from app.domain.repositories import MemoryRepository
from app.domain.tasks import TaskRepository
from app.domain.vector_store import VectorStore


def _build_repository() -> MemoryRepository:
    if config.DB_PROVIDER == "postgres":
        from app.infra.db.postgres_repo import PostgresMemoryRepository

        if not config.DATABASE_URL:
            raise RuntimeError("DB_PROVIDER=postgres için DATABASE_URL gerekli.")
        return PostgresMemoryRepository(config.DATABASE_URL)

    if config.DB_PROVIDER == "sqlite":
        from app.infra.db.sqlite_repo import SqliteMemoryRepository

        return SqliteMemoryRepository(config.SQLITE_PATH)

    raise ValueError(f"Bilinmeyen DB_PROVIDER: {config.DB_PROVIDER}")


def _build_vector_store() -> VectorStore:
    if config.VECTOR_PROVIDER == "pgvector":
        from app.infra.vector.pgvector_store import PgVectorStore

        if not config.DATABASE_URL:
            raise RuntimeError("VECTOR_PROVIDER=pgvector için DATABASE_URL gerekli.")
        return PgVectorStore(config.DATABASE_URL)

    if config.VECTOR_PROVIDER == "inmemory":
        from app.infra.vector.inmemory_store import InMemoryVectorStore

        return InMemoryVectorStore()

    if config.VECTOR_PROVIDER in ("none", "noop") or config.DB_PROVIDER == "sqlite":
        from app.infra.vector.noop_store import NoopVectorStore

        return NoopVectorStore()

    raise ValueError(f"Bilinmeyen VECTOR_PROVIDER: {config.VECTOR_PROVIDER}")


def _build_embedder() -> EmbeddingProvider:
    if config.EMBEDDING_PROVIDER == "openai":
        from app.infra.embeddings.openai_embeddings import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(
            api_key=config.OPENAI_API_KEY,
            model=config.EMBEDDING_MODEL,
            expected_dim=config.EMBEDDING_DIM,
        )
    raise ValueError(f"Bilinmeyen EMBEDDING_PROVIDER: {config.EMBEDDING_PROVIDER}")


def _build_llm() -> LLMProvider:
    if config.LLM_PROVIDER == "openai":
        from app.infra.llm.openai_llm import OpenAILLMProvider

        return OpenAILLMProvider(
            api_key=config.OPENAI_API_KEY,
            model=config.OPENAI_MODEL,
            timeout=config.OPENAI_TIMEOUT,
            max_retries=config.OPENAI_MAX_RETRIES,
            reasoning_effort=getattr(config, "REASONING_EFFORT", "minimal"),
        )
    raise ValueError(f"Bilinmeyen LLM_PROVIDER: {config.LLM_PROVIDER}")


def _build_task_repository() -> TaskRepository | None:
    # Notion bilgisi yoksa Task katmanı devre dışı kalır (manager guard eder).
    if not (config.NOTION_API_KEY and config.NOTION_PAGE_ID):
        return None
    from app.infra.notion.notion_repo import NotionTaskRepository

    return NotionTaskRepository(
        api_key=config.NOTION_API_KEY,
        page_id=config.NOTION_PAGE_ID,
        database_title=config.DATABASE_TITLE,
    )


def build_memory_manager() -> MemoryManager:
    return MemoryManager(
        repository=_build_repository(),
        vector_store=_build_vector_store(),
        embedder=_build_embedder(),
        llm=_build_llm(),
        task_repository=_build_task_repository(),
    )
