"""app/core/commands.py
=======================
Kalıcı bağlam yönetim komutları.

PDF (OpenClaw ilhamı): hafızalar gizli veritabanı kayıtları olarak değil,
incelenebilir, düzenlenebilir, dışa aktarılabilir biçimde var olmalı. Bu modül
kullanıcının kalıcı bağlamını sohbet üzerinden yönetmesini sağlar.

Komutlar:
    /kural <metin>      -> kişisel kural / kalıcı talimat ekle
    /proje <metin>      -> uzun vadeli proje bilgisi ekle
    /dokuman <metin>    -> dokümantasyon / bilgi tabanı girdisi ekle
    /prompt <metin>     -> prompt koleksiyonu girdisi ekle
    /kalici             -> tüm kalıcı bağlamı listele (numaralı)
    /kalici_sil <no>    -> listedeki numaralı girdiyi kaldır

Şeffaflık komutları (Faz 8):
    /hafiza             -> hafıza istatistikleri (ne biliyorum?)
    /hafiza <arama>     -> hafızada ara, numaralı listele
    /hafiza_sil <no>    -> son listedeki kaydı sil
    /duzenle <no> <yeni metin> -> son listedeki kaydın içeriğini düzelt
    /disaktar           -> JSON yedek (taşınabilir)
    /disaktar md        -> insan-okunur Markdown döküm

Bot çatısından bağımsızdır (Telegram'a bağlı değil) — saf fonksiyonlar.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.memory_manager import MemoryManager
from app.core.persistent_context import ContextKind, kind_of

_KIND_BY_COMMAND = {
    "kural": ContextKind.RULE,
    "proje": ContextKind.PROJECT,
    "dokuman": ContextKind.DOC,
    "prompt": ContextKind.PROMPT,
}

_KIND_EMOJI = {
    ContextKind.RULE: "📏",
    ContextKind.PROJECT: "🚀",
    ContextKind.DOC: "📚",
    ContextKind.PROMPT: "💬",
}


@dataclass
class CommandResult:
    handled: bool
    text: str = ""


def handle_command(
    manager: MemoryManager, raw: str, chat_id: str = "default"
) -> CommandResult:
    """Mesaj bir komutsa işler; değilse handled=False döner.

    chat_id, numaralı listeleme durumunu (arama sonucu -> sil/düzelt) sohbet
    bazında ayırmak için kullanılır.
    """
    text = (raw or "").strip()
    if not text.startswith("/"):
        return CommandResult(handled=False)

    parts = text[1:].split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    # --- kalıcı bağlam ekleme ---
    if cmd in _KIND_BY_COMMAND:
        kind = _KIND_BY_COMMAND[cmd]
        if not arg:
            return CommandResult(True, f"Ne eklemek istersin? Örnek: /{cmd} <metin>")
        m = manager.add_persistent_context(arg, kind=kind)
        emoji = _KIND_EMOJI[kind]
        return CommandResult(True, f"{emoji} Kalıcı bağlama eklendi: {m.content}")

    # --- listeleme ---
    if cmd == "kalici":
        items = manager.list_persistent_context()
        if not items:
            return CommandResult(
                True,
                "Henüz kalıcı bağlam yok.\n"
                "Ekle: /kural, /proje, /dokuman, /prompt",
            )
        lines = ["🧷 Kalıcı bağlam (her sohbette geçerli):"]
        for i, m in enumerate(items, start=1):
            k = kind_of(m.tags) or ContextKind.RULE
            lines.append(f"{i}. {_KIND_EMOJI[k]} {m.content}")
        lines.append("\nKaldırmak için: /kalici_sil <numara>")
        return CommandResult(True, "\n".join(lines))

    # --- silme ---
    if cmd == "kalici_sil":
        items = manager.list_persistent_context()
        if not arg.isdigit():
            return CommandResult(True, "Numara ver: /kalici_sil 2")
        idx = int(arg)
        if not (1 <= idx <= len(items)):
            return CommandResult(True, f"1 ile {len(items)} arasında bir numara ver.")
        target = items[idx - 1]
        manager.remove_persistent_context(target.id)
        return CommandResult(True, f"🗑️ Kaldırıldı: {target.content}")

    # --- şeffaflık: inceleme / arama ---
    if cmd == "hafiza":
        if not arg:
            return CommandResult(True, manager.stats().render() +
                                 "\n\nAramak için: /hafiza <kelime>")
        matches = _search(manager, arg)
        if not matches:
            return CommandResult(True, f"\"{arg}\" ile eşleşen hafıza bulamadım.")
        _remember_listing(chat_id, matches)
        lines = [f"🔎 \"{arg}\" için {len(matches)} sonuç:"]
        for i, m in enumerate(matches, start=1):
            lines.append(f"{i}. [{m.type.value}] {m.content}")
        lines.append("\nSil: /hafiza_sil <no>   Düzelt: /duzenle <no> <yeni metin>")
        return CommandResult(True, "\n".join(lines))

    if cmd == "hafiza_sil":
        if not arg.isdigit():
            return CommandResult(True, "Numara ver: /hafiza_sil 2")
        listing = _last_listing(chat_id)
        if not listing:
            return CommandResult(True, "Önce /hafiza <kelime> ile listele.")
        idx = int(arg)
        if not (1 <= idx <= len(listing)):
            return CommandResult(True, f"1 ile {len(listing)} arasında bir numara ver.")
        target = listing[idx - 1]
        try:
            manager.delete(target.id, hard=False)
        except PermissionError as exc:
            return CommandResult(True, f"Silinemedi: {exc}")
        return CommandResult(True, f"🗑️ Unuttum: {target.content}")

    if cmd == "duzenle":
        bits = arg.split(maxsplit=1)
        if len(bits) < 2 or not bits[0].isdigit():
            return CommandResult(True, "Kullanım: /duzenle <no> <yeni metin>")
        listing = _last_listing(chat_id)
        if not listing:
            return CommandResult(True, "Önce /hafiza <kelime> ile listele.")
        idx, new_text = int(bits[0]), bits[1].strip()
        if not (1 <= idx <= len(listing)):
            return CommandResult(True, f"1 ile {len(listing)} arasında bir numara ver.")
        updated = manager.edit_memory(listing[idx - 1].id, content=new_text)
        if updated is None:
            return CommandResult(True, "Kayıt bulunamadı.")
        return CommandResult(True, f"✏️ Güncellendi: {updated.content}")

    # --- şeffaflık: dışa aktarma ---
    if cmd == "disaktar":
        if arg.lower().startswith("md"):
            return CommandResult(True, manager.export_markdown())
        return CommandResult(True, manager.export_json(note="Twm hafıza yedeği"))

    return CommandResult(handled=False)


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
def _search(manager: MemoryManager, query: str, limit: int = 10):
    """Basit metin araması (semantik değil): kullanıcı ne yazdıysa onu bulsun.

    İnceleme amaçlı olduğu için birebir eşleşme daha öngörülebilir; semantik
    arama zaten bağlam getirmede kullanılıyor.
    """
    q = query.lower().strip()
    out = []
    for m in manager.all_memories(include_inactive=False):
        haystack = f"{m.title} {m.content} {' '.join(m.tags)}".lower()
        if q in haystack:
            out.append(m)
        if len(out) >= limit:
            break
    return out


# Son listeleme, numara ile silme/düzenleme için sohbet bazında tutulur.
# Süreç belleğinde; kalıcılık gerekmez (yalnızca bir sonraki komuta kadar yaşar).
_LISTINGS: dict[str, list] = {}
_MAX_LISTINGS = 50


def _remember_listing(chat_id: str, items: list) -> None:
    if len(_LISTINGS) >= _MAX_LISTINGS:
        _LISTINGS.clear()
    _LISTINGS[str(chat_id)] = list(items)


def _last_listing(chat_id: str) -> list:
    return _LISTINGS.get(str(chat_id), [])
