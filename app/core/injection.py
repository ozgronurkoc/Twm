"""app/core/injection.py
=========================
Bağlam enjeksiyonu — PDF'teki öncelik sırası.

    1. Identity
    2. Preferences
    3. Active Tasks
    4. Relevant Knowledge  (kalıcı + semantik)
    5. Relevant Episodes
    6. Reflections

Yalnızca MİNİMUM gerekli hafıza eklenir. Sonuç, LLM system prompt'una eklenecek
düz metin bloğudur (insan-okur -> OpenClaw şeffaflık).
"""
from __future__ import annotations

from app.core.models import Memory, Task
from app.core.persistent_context import kind_of
from app.core.retrieval import RankedMemory, RetrievedContext


def _mem_line(m: Memory) -> str:
    return f"- {m.summary or m.content}"


def _ranked_line(r: RankedMemory) -> str:
    return f"- {r.memory.summary or r.memory.content}"


def _task_line(t: Task) -> str:
    tarih = f" [{t.tarih}]" if t.tarih else ""
    return f"- {t.oncelik} {t.gorev} ({t.kategori}){tarih}"


def build_context_string(ctx: RetrievedContext) -> str:
    blocks: list[str] = []

    if ctx.identity:
        blocks.append("## Kullanıcı kimliği\n" + "\n".join(_mem_line(m) for m in ctx.identity))

    if ctx.preferences:
        blocks.append("## Tercihler\n" + "\n".join(_mem_line(m) for m in ctx.preferences))

    # Kalıcı bağlam: deneyim hafızalarından AYRI, kendi bloğunda ve türe göre
    # gruplanmış. PDF: kalıcı bilgi ile deneyim ayırt edilmeli.
    if ctx.persistent:
        by_kind: dict[str, list[Memory]] = {}
        for m in ctx.persistent:
            k = kind_of(m.tags)
            by_kind.setdefault(k.value if k else "kural", []).append(m)

        lines: list[str] = []
        for label, items in by_kind.items():
            lines.append(f"### {label.capitalize()}")
            lines += [_mem_line(m) for m in items]
        blocks.append("## Kalıcı bağlam (her zaman geçerli)\n" + "\n".join(lines))

    if ctx.active_tasks:
        blocks.append("## Aktif görevler\n" + "\n".join(_task_line(t) for t in ctx.active_tasks))

    if ctx.knowledge:
        blocks.append("## İlgili bilgiler\n" + "\n".join(_ranked_line(r) for r in ctx.knowledge))

    if ctx.episodes:
        blocks.append("## İlgili olaylar\n" + "\n".join(_ranked_line(r) for r in ctx.episodes))

    if ctx.reflections:
        blocks.append("## Geçmiş özetler\n" + "\n".join(_ranked_line(r) for r in ctx.reflections))

    # Graf gezinmesiyle gelenler: doğrudan alakalı değil ama bağlantılı.
    # Ayrı blokta tutulur ki modelin öncelik algısı bozulmasın.
    if ctx.graph_related:
        lines = [f"- {n.memory.summary or n.memory.content}" for n in ctx.graph_related]
        blocks.append("## Bağlantılı bilgiler\n" + "\n".join(lines))

    return "\n\n".join(blocks)
