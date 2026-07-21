"""
Notion database'i ile ilgili tüm işlemler burada.
- Database yoksa otomatik oluşturur, varsa şemayı günceller (Öncelik/Kategori)
- Görev ekleme / tamamlama / iptal etme / not ekleme
- Günlük / haftalık / geciken / tüm görevleri listeleme
- Belirsiz eşleşmelerde tahmin etmek yerine adayları döndürür
"""
from __future__ import annotations

from typing import NamedTuple, Optional

from notion_client import Client
import config

notion = Client(auth=config.NOTION_API_KEY)

_database_id_cache = None

# Kategoriye göre sayfa ikonu (emoji, seçim etiketinin baş harfinden alınır)
_DEFAULT_ONCELIK = "🟡 Orta"
_DEFAULT_KATEGORI = "📌 Diğer"

_REQUIRED_PROPERTIES = {
    "Görev": {"title": {}},
    "Durum": {
        "select": {
            "options": [
                {"name": "Yapılacak", "color": "yellow"},
                {"name": "Yapıldı", "color": "green"},
                {"name": "İptal", "color": "red"},
            ]
        }
    },
    "Öncelik": {
        "select": {
            "options": [
                {"name": "🔴 Yüksek", "color": "red"},
                {"name": "🟡 Orta", "color": "yellow"},
                {"name": "🟢 Düşük", "color": "green"},
            ]
        }
    },
    "Kategori": {
        "select": {
            "options": [
                {"name": "💼 İş", "color": "blue"},
                {"name": "🏠 Ev", "color": "orange"},
                {"name": "❤️ Sağlık", "color": "pink"},
                {"name": "👤 Kişisel", "color": "purple"},
                {"name": "📌 Diğer", "color": "gray"},
            ]
        }
    },
    "Tarih": {"date": {}},
    "Not": {"rich_text": {}},
}


class MatchResult(NamedTuple):
    """_find_task_page'in dönüş biçimi: tek bir sonuca mı vardık, yoksa
    birden fazla farklı görev mi eşleşti (adaylar listesiyle)."""

    page: Optional[dict]
    candidates: list[str]  # doluysa: birden fazla farklı görev eşleşti, kullanıcıya sor


def get_or_create_database() -> str:
    """Görevler database'ini bulur, yoksa oluşturur, varsa şemasını tamamlar."""
    global _database_id_cache
    if _database_id_cache:
        return _database_id_cache

    children = notion.blocks.children.list(block_id=config.NOTION_PAGE_ID)
    for block in children.get("results", []):
        if block.get("type") == "child_database":
            title = block.get("child_database", {}).get("title", "")
            if title == config.DATABASE_TITLE:
                _database_id_cache = block["id"]
                _ensure_schema(_database_id_cache)
                return _database_id_cache

    new_db = notion.databases.create(
        parent={"type": "page_id", "page_id": config.NOTION_PAGE_ID},
        title=[{"type": "text", "text": {"content": config.DATABASE_TITLE}}],
        properties=_REQUIRED_PROPERTIES,
    )
    _database_id_cache = new_db["id"]
    return _database_id_cache


def _ensure_schema(db_id: str) -> None:
    """Eski (Öncelik/Kategori öncesi) database'lerde eksik alanları tamamlar."""
    db = notion.databases.retrieve(database_id=db_id)
    existing = set(db.get("properties", {}).keys())
    missing = {k: v for k, v in _REQUIRED_PROPERTIES.items() if k not in existing}
    if missing:
        notion.databases.update(database_id=db_id, properties=missing)


def _category_icon(kategori: str) -> dict:
    emoji = kategori.split(" ", 1)[0] if kategori else "📌"
    return {"type": "emoji", "emoji": emoji}


def add_task(
    task_text: str,
    date_str: str,
    oncelik: str = _DEFAULT_ONCELIK,
    kategori: str = _DEFAULT_KATEGORI,
) -> None:
    db_id = get_or_create_database()
    notion.pages.create(
        parent={"database_id": db_id},
        icon=_category_icon(kategori),
        properties={
            "Görev": {"title": [{"text": {"content": task_text}}]},
            "Durum": {"select": {"name": "Yapılacak"}},
            "Öncelik": {"select": {"name": oncelik}},
            "Kategori": {"select": {"name": kategori}},
            "Tarih": {"date": {"start": date_str}},
        },
    )


def _find_task_page(task_text: str, date_str: Optional[str] = None) -> MatchResult:
    """Açık (Yapılacak) görevler arasında arar.

    - Tek sonuç varsa: doğrudan onu döner.
    - Aynı isimli görev birden fazla tarihte varsa: verilen tarihe en yakın
      olanı, yoksa en son oluşturulanı döner.
    - Birbirinden FARKLI birden fazla görev eşleşirse: tahmin etmez,
      adayları candidates listesinde döner ki kullanıcıya sorulabilsin.
    """
    db_id = get_or_create_database()
    query_filter = {
        "and": [
            {"property": "Görev", "title": {"contains": task_text}},
            {"property": "Durum", "select": {"equals": "Yapılacak"}},
        ]
    }
    results = notion.databases.query(database_id=db_id, filter=query_filter).get(
        "results", []
    )
    if not results:
        return MatchResult(page=None, candidates=[])

    titles = {
        p["properties"]["Görev"]["title"][0]["plain_text"]
        for p in results
        if p["properties"]["Görev"]["title"]
    }

    if date_str:
        same_date = [
            p
            for p in results
            if p["properties"]["Tarih"]["date"]
            and p["properties"]["Tarih"]["date"]["start"] == date_str
        ]
        if len(same_date) == 1:
            return MatchResult(page=same_date[0], candidates=[])
        if same_date:
            results = same_date

    if len(titles) == 1:
        # Aynı görev, farklı zamanlarda tekrar girilmiş olabilir -> en yeniyi al.
        return MatchResult(page=results[-1], candidates=[])

    # Birden fazla FARKLI görev eşleşti -> tahmin etme, kullanıcıya sor.
    return MatchResult(page=None, candidates=sorted(titles))


def mark_task_status(
    task_text: str, status: str, date_str: Optional[str] = None
) -> MatchResult:
    """status: 'Yapıldı' veya 'İptal'."""
    match = _find_task_page(task_text, date_str)
    if match.page:
        notion.pages.update(
            page_id=match.page["id"], properties={"Durum": {"select": {"name": status}}}
        )
    return match


def add_note_to_task(
    task_text: str, note_text: str, date_str: Optional[str] = None
) -> MatchResult:
    match = _find_task_page(task_text, date_str)
    if match.page:
        notion.pages.update(
            page_id=match.page["id"],
            properties={"Not": {"rich_text": [{"text": {"content": note_text}}]}},
        )
    return match


def _page_to_dict(page: dict) -> dict:
    props = page["properties"]
    title_parts = props["Görev"]["title"]
    title = title_parts[0]["plain_text"] if title_parts else ""
    status = props["Durum"]["select"]["name"] if props["Durum"]["select"] else "Yapılacak"
    oncelik = props.get("Öncelik", {}).get("select")
    kategori = props.get("Kategori", {}).get("select")
    tarih = props["Tarih"]["date"]["start"] if props["Tarih"]["date"] else None
    return {
        "görev": title,
        "durum": status,
        "öncelik": oncelik["name"] if oncelik else _DEFAULT_ONCELIK,
        "kategori": kategori["name"] if kategori else _DEFAULT_KATEGORI,
        "tarih": tarih,
    }


def list_tasks(date_str: str) -> list[dict]:
    """Belirli bir güne ait tüm görevler (durumdan bağımsız)."""
    db_id = get_or_create_database()
    results = notion.databases.query(
        database_id=db_id,
        filter={"property": "Tarih", "date": {"equals": date_str}},
    ).get("results", [])
    return [_page_to_dict(p) for p in results]


def list_overdue_tasks(before_date_str: str) -> list[dict]:
    """`before_date_str`'den önceki, hâlâ 'Yapılacak' durumundaki görevler."""
    db_id = get_or_create_database()
    results = notion.databases.query(
        database_id=db_id,
        filter={
            "and": [
                {"property": "Tarih", "date": {"before": before_date_str}},
                {"property": "Durum", "select": {"equals": "Yapılacak"}},
            ]
        },
    ).get("results", [])
    return [_page_to_dict(p) for p in results]


def list_tasks_range(start_date_str: str, end_date_str: str) -> list[dict]:
    """[start, end] aralığındaki (iki taraf dahil) tüm görevler."""
    db_id = get_or_create_database()
    results = notion.databases.query(
        database_id=db_id,
        filter={
            "and": [
                {"property": "Tarih", "date": {"on_or_after": start_date_str}},
                {"property": "Tarih", "date": {"on_or_before": end_date_str}},
            ]
        },
        sorts=[{"property": "Tarih", "direction": "ascending"}],
    ).get("results", [])
    return [_page_to_dict(p) for p in results]


def list_all_open_tasks() -> list[dict]:
    """Durumdan bağımsız değil, sadece hâlâ açık olan (Yapılacak) tüm görevler."""
    db_id = get_or_create_database()
    results = notion.databases.query(
        database_id=db_id,
        filter={"property": "Durum", "select": {"equals": "Yapılacak"}},
        sorts=[{"property": "Tarih", "direction": "ascending"}],
    ).get("results", [])
    return [_page_to_dict(p) for p in results]
