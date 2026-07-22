"""app/core/reflection.py
=========================
Reflection Engine — hafıza konsolidasyonu.

PDF: "Asistan eski episodic hafızaları periyodik olarak gözden geçirmeli.
Yüzlerce konuşmayı sonsuza dek saklamak yerine sıkıştırmalı:
    Günlük hafızalar -> Haftalık özet -> Aylık özet -> Çeyreklik özet
    -> Uzun vadeli anlayış
Bu süreç ANLAMI korurken token kullanımını azaltmalı; insan hafıza
konsolidasyonuna benzemeli."

Tasarım:
  - DAILY   : o günün EPISODE kayıtlarından üretilir.
  - WEEKLY  : o haftanın DAILY özetlerinden üretilir.
  - MONTHLY : o ayın WEEKLY özetlerinden.
  - QUARTERLY: o çeyreğin MONTHLY özetlerinden.
Her özet REFLECTION katmanına yazılır, seviyesi etiketle işaretlenir ve
`related_memory_ids` ile kaynaklarına bağlanır (graf zemini + izlenebilirlik).

Aynı pencere için ikinci kez çalıştırılırsa yeni kayıt açılmaz; mevcut özet
güncellenir (idempotent).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.enums import (
    MemoryType,
    ReflectionLevel,
    reflection_level_of,
    reflection_tag,
)
from app.core.models import Memory
from app.domain.llm import LLMProvider

logger = logging.getLogger(__name__)


SYSTEM_PROMPT_REFLECTION = """\
# ROL
Sen bir hafıza konsolidasyon motorusun. Görevin, verilen hafıza kayıtlarını
ANLAMI KORUYARAK tek bir üst-seviye özete sıkıştırmak.

# İLKELER
- Ham kayıtları tek tek listeleme; ÖRÜNTÜ ve ANLAM çıkar.
- Tekrar edenleri birleştir, önemsiz ayrıntıyı at.
- Üçüncü tekil şahıs, sade Türkçe, geçmiş zaman kullan.
- Kişi/proje/hedef isimleri korunmalı; tarih detayı gerekmiyorsa atılabilir.
- Çıktı 2-5 cümle olmalı. Madde işareti kullanma, akıcı paragraf yaz.
- Hiçbir şey uydurma; yalnızca verilen kayıtlardan yararlan.

# ÇIKTI
Yalnızca özet metnini döndür. Başlık, ön söz, tırnak ekleme.
"""


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    label: str


def window_for(level: ReflectionLevel, ref: datetime) -> Window:
    """Verilen ana ait pencereyi hesaplar (yerel değil, UTC bazlı)."""
    ref = ref.astimezone(timezone.utc)
    day = ref.replace(hour=0, minute=0, second=0, microsecond=0)

    if level is ReflectionLevel.DAILY:
        return Window(day, day + timedelta(days=1), day.strftime("%Y-%m-%d"))

    if level is ReflectionLevel.WEEKLY:
        start = day - timedelta(days=day.weekday())
        return Window(start, start + timedelta(days=7),
                      f"{start.strftime('%Y-%m-%d')} haftası")

    if level is ReflectionLevel.MONTHLY:
        start = day.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
        return Window(start, end, start.strftime("%Y-%m"))

    # QUARTERLY
    q = (day.month - 1) // 3
    start = day.replace(month=q * 3 + 1, day=1)
    end_month = start.month + 3
    end = (start.replace(year=start.year + 1, month=end_month - 12)
           if end_month > 12 else start.replace(month=end_month))
    return Window(start, end, f"{start.year}-Ç{q + 1}")


def summarize(llm: LLMProvider, sources: list[Memory], level: ReflectionLevel,
              label: str) -> Optional[str]:
    """Kaynak kayıtlardan özet metni üretir."""
    if not sources:
        return None

    bullet = "\n".join(f"- {m.summary or m.content}" for m in sources)
    kind = "günlük olay" if level is ReflectionLevel.DAILY else "alt-dönem özeti"
    user = (
        f"Aşağıda {label} dönemine ait {len(sources)} adet {kind} var. "
        f"Bunları tek bir {level.value} özetine sıkıştır.\n\n{bullet}"
    )
    try:
        text = llm.complete(
            system=SYSTEM_PROMPT_REFLECTION,
            messages=[{"role": "user", "content": user}],
            temperature=0.2,
        )
    except Exception:
        logger.exception("Reflection özeti üretilemedi (%s / %s)", level.value, label)
        return None

    text = " ".join((text or "").split())
    return text or None


def find_existing(reflections: list[Memory], level: ReflectionLevel,
                  label: str) -> Optional[Memory]:
    """Aynı seviye+pencere için daha önce üretilmiş özeti bulur (idempotency)."""
    tag = reflection_tag(level)
    for m in reflections:
        if tag in m.tags and m.title.endswith(label):
            return m
    return None


def title_for(level: ReflectionLevel, label: str) -> str:
    names = {
        ReflectionLevel.DAILY: "Günlük özet",
        ReflectionLevel.WEEKLY: "Haftalık özet",
        ReflectionLevel.MONTHLY: "Aylık özet",
        ReflectionLevel.QUARTERLY: "Çeyreklik özet",
    }
    return f"{names[level]} — {label}"
