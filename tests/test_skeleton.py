"""tests/test_skeleton.py
=========================
Faz 0 duman testi (smoke test).

Dış servis GEREKTİRMEZ: SQLite repo + no-op vektör store + sahte embedding/LLM
ile Memory Manager'ın create/get/update/delete akışını ve provider-bağımsız
kablolamayı doğrular.

Çalıştırma:
    python -m pytest tests/ -v
    (pytest yoksa)  python tests/test_skeleton.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.core.enums import MemoryType
from app.core.memory_manager import MemoryManager
from app.core.models import MemoryDraft
from app.domain.embeddings import EmbeddingProvider
from app.domain.llm import LLMProvider
from app.infra.db.sqlite_repo import SqliteMemoryRepository
from app.infra.vector.noop_store import NoopVectorStore


class FakeEmbedder(EmbeddingProvider):
    @property
    def dimension(self) -> int:
        return 3

    def embed(self, text: str) -> list[float]:
        return [float(len(text)), 0.0, 1.0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class FakeLLM(LLMProvider):
    def complete(self, *, system, messages, temperature=0.0) -> str:
        return "ok"

    def complete_structured(self, *, system, messages, tool_schema, tool_name, temperature=0.0):
        return {}


def _manager(tmp: str) -> MemoryManager:
    return MemoryManager(
        repository=SqliteMemoryRepository(tmp),
        vector_store=NoopVectorStore(),
        embedder=FakeEmbedder(),
        llm=FakeLLM(),
    )


def run() -> None:
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "t.db")
        mgr = _manager(db)

        # create (Identity)
        m = mgr.create(MemoryDraft(
            type=MemoryType.IDENTITY,
            title="Ad",
            content="Kullanıcının adı Özgür.",
            importance=0.95,
        ))
        assert m.id is not None
        assert m.type is MemoryType.IDENTITY

        # get
        got = mgr.get(m.id)
        assert got is not None and got.title == "Ad"

        # always-on load (Identity + Preference deterministik)
        mgr.create(MemoryDraft(
            type=MemoryType.PREFERENCE,
            title="Ton",
            content="Kısa ve net yaz.",
        ))
        always = mgr.load_always_on()
        types = {x.type for x in always}
        assert MemoryType.IDENTITY in types and MemoryType.PREFERENCE in types

        # update
        got.summary = "isim: Özgür"
        mgr.update(got)
        assert mgr.get(got.id).summary == "isim: Özgür"

        # Identity hard-delete koruması
        try:
            mgr.delete(m.id, hard=True)
            raise AssertionError("Identity hard delete engellenmeliydi.")
        except PermissionError:
            pass

        # soft delete
        mgr.delete(m.id, hard=False)
        assert mgr.get(m.id).status.value == "deleted"

        # Faz 7 itibarıyla PDF'teki tüm Memory Manager metotları implement edildi;
        # artık NotImplementedError bırakan metot yok. Bunun yerine metotların
        # var ve çağrılabilir olduğunu doğrula.
        for name in ("semantic_search", "retrieve_for_context", "build_context",
                     "evaluate_and_store", "detect_duplicates", "resolve_conflict",
                     "run_reflection", "decay_and_archive", "maintain_graph"):
            assert callable(getattr(mgr, name)), f"{name} eksik"

    print("OK — Faz 0 iskeleti tüm duman testlerini geçti.")


def test_skeleton():
    run()


if __name__ == "__main__":
    run()
