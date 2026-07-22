"""app/core/models.py
=====================
Hafıza domain modeli.

PDF'teki "Memory Schema" bölümündeki 17 zorunlu alanın tamamını içerir:
    id, type, title, content, summary, importance, confidence,
    createdAt, updatedAt, lastAccessed, accessCount, expiresAt,
    embedding, sourceConversation, relatedMemoryIds, tags, status

Not: `embedding` alanı domain modelinde taşınır ama iş mantığı onun boyutunu
ya da sağlayıcısını bilmez; bu bir soyutlamadır (provider-bağımsızlık).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from app.core.enums import MemoryStatus, MemoryType


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryDraft(BaseModel):
    """Henüz kalıcılaştırılmamış hafıza taslağı.

    Pipeline'ın (evaluation + classification) çıktısıdır. Memory Manager bunu
    alır, embedding üretir, dedup/çakışma kontrolünden geçirir ve `Memory`
    olarak kalıcılaştırır. Ham kullanıcı mesajı ASLA doğrudan buraya yazılmaz;
    yalnızca çıkarılmış, yapılandırılmış bilgi girer.
    """

    type: MemoryType
    title: str
    content: str
    summary: Optional[str] = None
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    source_conversation: Optional[str] = None
    related_memory_ids: list[UUID] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    # Claude Projects ilhamı: her zaman yüklenen kalıcı bağlam kaydı mı?
    is_persistent: bool = False
    # Değerlendiricinin açık ezme sinyali (dedup kararında kullanılır).
    should_overwrite: bool = False

    @field_validator("title", "content", "summary", mode="before")
    @classmethod
    def _strip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = " ".join(str(v).split())
        return v or None


class Memory(BaseModel):
    """Kalıcılaştırılmış, tam metadatalı hafıza kaydı."""

    id: UUID = Field(default_factory=uuid4)
    type: MemoryType
    title: str
    content: str
    summary: Optional[str] = None

    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)

    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    last_accessed: Optional[datetime] = None
    access_count: int = 0
    expires_at: Optional[datetime] = None

    # Domain tarafında embedding'i float listesi olarak taşırız; somut vektör
    # tipi/boyutu infra katmanının (pgvector vb.) sorunudur.
    embedding: Optional[list[float]] = None

    source_conversation: Optional[str] = None
    related_memory_ids: list[UUID] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    status: MemoryStatus = MemoryStatus.ACTIVE

    is_persistent: bool = False

    @classmethod
    def from_draft(cls, draft: MemoryDraft, embedding: Optional[list[float]] = None) -> "Memory":
        return cls(
            type=draft.type,
            title=draft.title,
            content=draft.content,
            summary=draft.summary,
            importance=draft.importance,
            confidence=draft.confidence,
            source_conversation=draft.source_conversation,
            related_memory_ids=list(draft.related_memory_ids),
            tags=list(draft.tags),
            embedding=embedding,
            is_persistent=draft.is_persistent,
        )

    def touch_accessed(self) -> None:
        """Bağlama enjekte edilince çağrılır: erişim sinyallerini günceller."""
        self.last_accessed = _now()
        self.access_count += 1


# --------------------------------------------------------------------------- #
# Layer 5 — Task (operasyonel hafıza).
# Diğer katmanlardan farklı: backend'i Notion, kendi durum/tarih alanları var.
# Bu yüzden ayrı bir domain modeli olarak temsil edilir.
# --------------------------------------------------------------------------- #
class Task(BaseModel):
    """Notion'daki bir görev satırının domain karşılığı."""

    gorev: str
    durum: str = "Yapılacak"          # Yapılacak | Yapıldı | İptal
    oncelik: str = "🟡 Orta"
    kategori: str = "📌 Diğer"
    tarih: Optional[str] = None       # ISO tarih (YYYY-MM-DD)
    notion_page_id: Optional[str] = None


class TaskMatch(BaseModel):
    """Bir görev arama sonucunun biçimi.

    - `task` doluysa: tek ve net eşleşme bulundu.
    - `candidates` doluysa: birden fazla FARKLI görev eşleşti, kullanıcıya sor
      (tahmin etme).
    """

    task: Optional[Task] = None
    candidates: list[str] = Field(default_factory=list)
