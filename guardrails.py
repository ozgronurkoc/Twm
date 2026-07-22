"""app/core/guardrails.py
========================
Maliyet ve gecikme koruyucuları.

Hafıza sistemi her mesajda LLM + embedding çağrısı yapar. Production'da bu
üç riski doğurur: (1) maliyet patlaması, (2) yanıt gecikmesi, (3) sağlayıcı
rate limit hataları. Bu modül üçüne karşı da basit, bağımlılıksız korumalar sunar.

Not: Kasıtlı olarak süreç-içi (in-process) tutuldu. Çok replikalı bir kuruluma
geçilirse aynı arayüzler Redis tabanlı bir sayaçla değiştirilebilir.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GuardrailConfig:
    # Kullanıcı başına dakikadaki azami mesaj (spam / maliyet koruması).
    max_messages_per_minute: int = 20
    # Kullanıcı başına saatteki azami hafıza çıkarımı (LLM çağrısı).
    max_extractions_per_hour: int = 120
    # Bağlama enjekte edilecek azami karakter (token bütçesi).
    max_context_chars: int = 6000
    # Tek mesajda işlenecek azami karakter (aşırı uzun girdi koruması).
    max_message_chars: int = 4000


class RateLimiter:
    """Kayan pencere sayacı. Thread-safe."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        with self._lock:
            q = self._events[key]
            cutoff = now - self._window
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self._limit:
                return False
            q.append(now)
            return True

    def remaining(self, key: str, *, now: Optional[float] = None) -> int:
        now = now if now is not None else time.time()
        with self._lock:
            q = self._events[key]
            cutoff = now - self._window
            while q and q[0] < cutoff:
                q.popleft()
            return max(0, self._limit - len(q))


@dataclass
class Guardrails:
    cfg: GuardrailConfig = field(default_factory=GuardrailConfig)
    _messages: RateLimiter = field(init=False)
    _extractions: RateLimiter = field(init=False)

    def __post_init__(self) -> None:
        self._messages = RateLimiter(self.cfg.max_messages_per_minute, 60)
        self._extractions = RateLimiter(self.cfg.max_extractions_per_hour, 3600)

    def allow_message(self, chat_id: str) -> bool:
        ok = self._messages.allow(str(chat_id))
        if not ok:
            logger.warning("Mesaj limiti aşıldı chat=%s", chat_id)
        return ok

    def allow_extraction(self, chat_id: str) -> bool:
        """Hafıza çıkarımı (ek LLM çağrısı) yapılabilir mi?

        Limit dolduğunda yanıt akışı DEVAM eder; yalnızca çıkarım atlanır.
        Böylece kullanıcı deneyimi bozulmaz, maliyet sınırlanır.
        """
        ok = self._extractions.allow(str(chat_id))
        if not ok:
            logger.info("Çıkarım limiti doldu, bu mesaj için atlanıyor chat=%s", chat_id)
        return ok

    def truncate_message(self, text: str) -> str:
        limit = self.cfg.max_message_chars
        if len(text) <= limit:
            return text
        logger.info("Mesaj %d karaktere kırpıldı.", limit)
        return text[:limit]

    def truncate_context(self, context: str) -> str:
        """Bağlamı token bütçesine sığdırır.

        Kırpma SONDAN yapılır: enjeksiyon öncelik sırasına göre kurulduğu için
        (kimlik -> tercih -> görev -> bilgi -> olay -> özet -> bağlantılı),
        sondan kesmek en az önemli bloğu feda eder.
        """
        limit = self.cfg.max_context_chars
        if len(context) <= limit:
            return context
        cut = context[:limit]
        # Yarım blok bırakmak (başlık var, içeriği kesik) prompt'ta kafa
        # karıştırır. Bu yüzden HER ZAMAN son tam blok sınırına geri sarılır.
        # Öncelik sırası sayesinde baştaki bloklar zaten en önemlileridir.
        last_block = cut.rfind("\n\n## ")
        if last_block > 0:
            cut = cut[:last_block]
        logger.info("Bağlam %d karaktere kırpıldı.", len(cut))
        return cut
