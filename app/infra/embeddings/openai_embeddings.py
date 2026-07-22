"""app/infra/embeddings/openai_embeddings.py
=============================================
EmbeddingProvider'ın OpenAI implementasyonu.

Varsayılan model: text-embedding-3-small (1536 boyut) — ucuz/hızlı.
İleride text-embedding-3-large (3072) ya da başka bir sağlayıcıya geçmek
yalnızca bu dosyayı + config'i + bir migration'ı etkiler.
"""
from __future__ import annotations

from openai import OpenAI

from app.domain.embeddings import EmbeddingProvider

# Model -> boyut haritası (şema doğrulaması için).
_MODEL_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "text-embedding-3-small",
        expected_dim: int | None = None,
        client: OpenAI | None = None,
    ) -> None:
        self._model = model
        self._dim = _MODEL_DIMS.get(model, expected_dim or 1536)
        if expected_dim is not None and expected_dim != self._dim:
            raise ValueError(
                f"Şema boyutu ({expected_dim}) ile model boyutu ({self._dim}) "
                f"uyuşmuyor. Migration + re-embed gerekli."
            )
        self._client = client or OpenAI(api_key=api_key)

    @property
    def dimension(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        resp = self._client.embeddings.create(model=self._model, input=text)
        return resp.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self._client.embeddings.create(model=self._model, input=texts)
        # OpenAI sırayı korur ama garanti için index'e göre sıralıyoruz.
        ordered = sorted(resp.data, key=lambda d: d.index)
        return [d.embedding for d in ordered]
