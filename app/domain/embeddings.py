"""app/domain/embeddings.py
============================
Embedding sağlayıcı PORT'u.

PDF: OpenAI / Gemini / Voyage / Jina / yerel model birbirinin yerine geçebilmeli.

DİKKAT: `dimension` şemaya (pgvector kolon boyutu) gömülüdür. Sağlayıcı ya da
model değiştirip boyut değişirse, migration + yeniden embedding (re-embed)
gerekir. Bu yüzden boyut config'ten okunur ve sağlayıcı onu doğrular.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    @property
    @abstractmethod
    def dimension(self) -> int:
        """Üretilen vektörün boyutu. Şema ile eşleşmek ZORUNDA."""
        ...

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        ...

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Arka plan embedding worker'ı için toplu üretim (maliyet/hız)."""
        ...
