"""app/core/transparency.py
==========================
Şeffaflık katmanı (OpenClaw ilhamı).

PDF: "Mümkün olduğunca hafızalar, yalnızca gizli veritabanı kayıtları olarak
değil, İNSAN TARAFINDAN OKUNABİLİR biçimde saklanmalı. Mimari, hafızaların
incelenebileceği, dışa aktarılabileceği, yedeklenebileceği, düzenlenebileceği
veya taşınabileceği yapılandırılmış formatları desteklemeli."

Bu modül iki format sunar:
  - JSON     : eksiksiz, makine-okunur; yedekleme/geri yükleme ve TAŞIMA için
               (provider değiştirirken veriyi taşımanın yolu).
  - Markdown : insan-okunur; kullanıcının hafızasını gözden geçirmesi için.

`embedding` alanı dışa aktarılmaz: sağlayıcıya özgüdür ve içerikten yeniden
üretilebilir (import sonrası backfill işi halleder). Bu, taşınabilirliği artırır.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from app.core.enums import (
    MemoryStatus,
    MemoryType,
    reflection_level_of,
)
from app.core.models import Memory
from app.core.persistent_context import kind_of

logger = logging.getLogger(__name__)

EXPORT_VERSION = 1

_TYPE_LABEL = {
    MemoryType.IDENTITY: "Kimlik",
    MemoryType.PREFERENCE: "Tercihler",
    MemoryType.EPISODE: "Olaylar",
    MemoryType.KNOWLEDGE: "Bilgiler",
    MemoryType.REFLECTION: "Özetler",
    MemoryType.TASK: "Görevler",
}


# --------------------------------------------------------------------------- #
# JSON dışa/içe aktarma
# --------------------------------------------------------------------------- #
def memory_to_dict(m: Memory) -> dict[str, Any]:
    """Tek hafızayı taşınabilir sözlüğe çevirir (embedding hariç)."""
    return {
        "id": str(m.id),
        "type": m.type.value,
        "title": m.title,
        "content": m.content,
        "summary": m.summary,
        "importance": m.importance,
        "confidence": m.confidence,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        "last_accessed": m.last_accessed.isoformat() if m.last_accessed else None,
        "access_count": m.access_count,
        "expires_at": m.expires_at.isoformat() if m.expires_at else None,
        "source_conversation": m.source_conversation,
        "related_memory_ids": [str(x) for x in m.related_memory_ids],
        "tags": list(m.tags),
        "status": m.status.value,
        "is_persistent": m.is_persistent,
    }


def memory_from_dict(data: dict[str, Any]) -> Memory:
    """Sözlükten Memory üretir (import / geri yükleme)."""
    def _dt(v: Optional[str]):
        return datetime.fromisoformat(v) if v else None

    return Memory(
        id=data["id"],
        type=MemoryType(data["type"]),
        title=data["title"],
        content=data["content"],
        summary=data.get("summary"),
        importance=data.get("importance", 0.5),
        confidence=data.get("confidence", 0.7),
        created_at=_dt(data.get("created_at")) or datetime.now(timezone.utc),
        updated_at=_dt(data.get("updated_at")) or datetime.now(timezone.utc),
        last_accessed=_dt(data.get("last_accessed")),
        access_count=data.get("access_count", 0),
        expires_at=_dt(data.get("expires_at")),
        source_conversation=data.get("source_conversation"),
        related_memory_ids=data.get("related_memory_ids", []),
        tags=data.get("tags", []),
        status=MemoryStatus(data.get("status", "active")),
        is_persistent=data.get("is_persistent", False),
        embedding=None,  # kasıtlı: içerikten yeniden üretilir
    )


def build_export(memories: Iterable[Memory], *, note: str = "") -> dict[str, Any]:
    items = [memory_to_dict(m) for m in memories]
    return {
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "note": note,
        "count": len(items),
        "memories": items,
    }


def to_json(memories: Iterable[Memory], *, note: str = "", indent: int = 2) -> str:
    return json.dumps(build_export(memories, note=note), ensure_ascii=False, indent=indent)


def parse_export(raw: str) -> list[Memory]:
    data = json.loads(raw)
    version = data.get("version")
    if version != EXPORT_VERSION:
        logger.warning("Beklenmeyen dışa aktarma sürümü: %s", version)
    return [memory_from_dict(d) for d in data.get("memories", [])]


# --------------------------------------------------------------------------- #
# Markdown (insan-okunur)
# --------------------------------------------------------------------------- #
def _describe(m: Memory) -> str:
    """Bir hafızanın metadata satırı — neden var, ne kadar önemli, kaç kez kullanıldı."""
    bits = [f"önem {m.importance:.2f}", f"güven {m.confidence:.2f}"]
    if m.access_count:
        bits.append(f"{m.access_count} kez kullanıldı")
    if m.created_at:
        bits.append(m.created_at.strftime("%Y-%m-%d"))
    if m.is_persistent:
        k = kind_of(m.tags)
        bits.append(f"kalıcı{f'/{k.value}' if k else ''}")
    lvl = reflection_level_of(m.tags)
    if lvl:
        bits.append(lvl.value)
    if m.status is not MemoryStatus.ACTIVE:
        bits.append(m.status.value)
    return ", ".join(bits)


def to_markdown(memories: Iterable[Memory], *, title: str = "Hafıza dökümü") -> str:
    items = list(memories)
    lines = [f"# {title}", "", f"Toplam {len(items)} kayıt.", ""]

    for mem_type in MemoryType:
        group = [m for m in items if m.type is mem_type]
        if not group:
            continue
        lines.append(f"## {_TYPE_LABEL.get(mem_type, mem_type.value)} ({len(group)})")
        lines.append("")
        for m in sorted(group, key=lambda x: x.importance, reverse=True):
            lines.append(f"### {m.title}")
            lines.append(m.content)
            lines.append("")
            lines.append(f"_{_describe(m)}_")
            if m.tags:
                lines.append(f"Etiketler: {', '.join(m.tags)}")
            if m.related_memory_ids:
                lines.append(f"Bağlantılı kayıt sayısı: {len(m.related_memory_ids)}")
            lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# İstatistik (inceleme yüzeyi)
# --------------------------------------------------------------------------- #
@dataclass
class MemoryStats:
    by_type: dict[str, int] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)
    persistent: int = 0
    total_edges: int = 0
    avg_importance: float = 0.0
    most_used: Optional[Memory] = None

    def render(self) -> str:
        lines = ["📊 Hafıza durumu", ""]
        total = sum(self.by_type.values())
        lines.append(f"Toplam: {total} kayıt")
        for k, v in sorted(self.by_type.items(), key=lambda x: -x[1]):
            label = _TYPE_LABEL.get(MemoryType(k), k)
            lines.append(f"  • {label}: {v}")
        if self.persistent:
            lines.append(f"  • Kalıcı bağlam: {self.persistent}")
        arch = self.by_status.get("archived", 0)
        sup = self.by_status.get("superseded", 0)
        if arch or sup:
            lines.append(f"Arşivlenmiş: {arch}, geçersiz kılınmış: {sup}")
        lines.append(f"Ortalama önem: {self.avg_importance:.2f}")
        lines.append(f"Graf bağlantısı: {self.total_edges}")
        if self.most_used:
            lines.append(
                f"En çok kullanılan: \"{self.most_used.title}\" "
                f"({self.most_used.access_count} kez)"
            )
        return "\n".join(lines)


def compute_stats(memories: Iterable[Memory]) -> MemoryStats:
    items = list(memories)
    stats = MemoryStats()
    if not items:
        return stats

    for m in items:
        stats.by_type[m.type.value] = stats.by_type.get(m.type.value, 0) + 1
        stats.by_status[m.status.value] = stats.by_status.get(m.status.value, 0) + 1
        stats.total_edges += len(m.related_memory_ids or [])
        if m.is_persistent:
            stats.persistent += 1

    stats.avg_importance = sum(m.importance for m in items) / len(items)
    used = [m for m in items if m.access_count > 0]
    stats.most_used = max(used, key=lambda m: m.access_count) if used else None
    return stats
