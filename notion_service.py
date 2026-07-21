"""
Notion database'i ile ilgili tüm işlemler burada.
- Database yoksa otomatik oluşturur
- Görev ekleme / tamamlama / iptal etme / not ekleme
- Belirli bir güne ait görevleri listeleme
"""

from notion_client import Client
import config

notion = Client(auth=config.NOTION_API_KEY)

_database_id_cache = None


def get_or_create_database() -> str:
    """Görevler database'ini bulur, yoksa oluşturur. ID'sini döner."""
    global _database_id_cache
    if _database_id_cache:
        return _database_id_cache

    # Sayfanın altındaki blokları tara, aynı isimde bir database var mı bak
    children = notion.blocks.children.list(block_id=config.NOTION_PAGE_ID)
    for block in children.get("results", []):
        if block.get("type") == "child_database":
            title = block.get("child_database", {}).get("title", "")
            if title == config.DATABASE_TITLE:
                _database_id_cache = block["id"]
                return _database_id_cache

    # Bulunamadıysa yeni database oluştur
    new_db = notion.databases.create(
        parent={"type": "page_id", "page_id": config.NOTION_PAGE_ID},
        title=[{"type": "text", "text": {"content": config.DATABASE_TITLE}}],
        properties={
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
            "Tarih": {"date": {}},
            "Not": {"rich_text": {}},
        },
    )
    _database_id_cache = new_db["id"]
    return _database_id_cache


def add_task(task_text: str, date_str: str) -> None:
    db_id = get_or_create_database()
    notion.pages.create(
        parent={"database_id": db_id},
        properties={
            "Görev": {"title": [{"text": {"content": task_text}}]},
            "Durum": {"select": {"name": "Yapılacak"}},
            "Tarih": {"date": {"start": date_str}},
        },
    )


def _find_task_page(task_text: str, date_str: str | None = None):
    """Metne en çok benzeyen, tercihen belirtilen tarihe ait görevi bulur."""
    db_id = get_or_create_database()
    query_filter = {
        "property": "Görev",
        "title": {"contains": task_text},
    }
    results = notion.databases.query(database_id=db_id, filter=query_filter).get(
        "results", []
    )
    if not results:
        return None

    if date_str:
        for page in results:
            page_date = page["properties"]["Tarih"]["date"]
            if page_date and page_date["start"] == date_str:
                return page

    # Tarih eşleşmesi yoksa en son oluşturulanı döndür
    return results[-1]


def mark_task_status(task_text: str, status: str, date_str: str | None = None) -> bool:
    """status: 'Yapıldı' veya 'İptal'. Bulunursa True döner."""
    page = _find_task_page(task_text, date_str)
    if not page:
        return False
    notion.pages.update(page_id=page["id"], properties={"Durum": {"select": {"name": status}}})
    return True


def add_note_to_task(task_text: str, note_text: str, date_str: str | None = None) -> bool:
    page = _find_task_page(task_text, date_str)
    if not page:
        return False
    notion.pages.update(
        page_id=page["id"],
        properties={"Not": {"rich_text": [{"text": {"content": note_text}}]}},
    )
    return True


def list_tasks(date_str: str) -> list[dict]:
    db_id = get_or_create_database()
    results = notion.databases.query(
        database_id=db_id,
        filter={"property": "Tarih", "date": {"equals": date_str}},
    ).get("results", [])

    tasks = []
    for page in results:
        props = page["properties"]
        title_parts = props["Görev"]["title"]
        title = title_parts[0]["plain_text"] if title_parts else ""
        status = props["Durum"]["select"]["name"] if props["Durum"]["select"] else "Yapılacak"
        tasks.append({"görev": title, "durum": status})
    return tasks
