"""app/core/decay.py
=====================
Unutma (Forgetting) Sistemi.

PDF: "Her hafızanın bir yaşam döngüsü vardır. Sinyaller: importance,
accessCount, lastAccessed, confidence. Bir hafızanın önemi düşük, erişim sayısı
düşük ve uzun süredir erişilmemişse önce ARŞİVLENMELİ. Yalnızca yapılandırılabilir
bir saklama süresinden sonra kalıcı olarak SİLİNMELİ. Identity hafızaları
neredeyse hiç silinmemelidir."

Tasarım hedefi (PDF "Design Goal"): hafıza zamanla küçülsün, zenginleşsin ve
akıllansın — sonsuza dek büyümesin.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.enums import MemoryType


@dataclass(frozen=True)
class DecayConfig:
    """Unutma eşikleri. Hepsi yapılandırılabilir (PDF şartı)."""

    # ARŞİVLEME eşikleri — üçü BİRDEN sağlanmalı.
    max_importance: float = 0.35      # bu değerin altındaki önemsizler
    max_access_count: int = 1         # neredeyse hiç kullanılmamış
    stale_after_days: int = 90        # bu süredir dokunulmamış

    # KALICI SİLME: arşivde bu kadar gün bekledikten sonra.
    retention_days: int = 180

    # Asla decay edilmeyen katmanlar.
    # PDF: Identity neredeyse hiç silinmez. Preference ve kalıcı bağlam da
    # kullanıcı davranışını yöneten kalıcı bilgidir; erişim sayısı düşük diye
    # atılmamalı. (Kalıcı bağlam ayrıca repo seviyesinde de dışlanır.)
    protected_types: tuple[MemoryType, ...] = (
        MemoryType.IDENTITY,
        MemoryType.PREFERENCE,
    )

    # Tek turda işlenecek azami kayıt (uzun worker turlarını önler).
    batch_limit: int = 200


@dataclass
class DecayReport:
    archived: list = field(default_factory=list)
    deleted: list = field(default_factory=list)

    @property
    def summary(self) -> str:
        return f"arşivlenen={len(self.archived)}, silinen={len(self.deleted)}"
