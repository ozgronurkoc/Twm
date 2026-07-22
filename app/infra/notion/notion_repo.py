"""app/infra/notion/notion_repo.py
==================================
TaskRepository'nin Notion implementasyonu.

Mevcut projedeki notion_service.py mantığı buraya, TaskRepository arayüzünün
arkasına taşındı. Davranış birebir korunur:
  - "Görevler" database'i yoksa otomatik oluşturur, varsa şemayı tamamlar.
  - Belirsiz eşleşmelerde tahmin etmez, adayları döndürür.
  - Aynı isimli görev birden fazla tarihte varsa, verilen tarihe en yakını /
    yoksa en son gireni seçer.

Client enjekte edilebilir (test için).
"""
from __future__ import annotations

from typing import Optional, Sequence

from notion_client import Client

from app.core.models import Task, TaskMatch
from app.domain.tasks import TaskRepository

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


class NotionTaskRepository(TaskRepository):
    def __init__(
        self,
        *,
        api_key: str,
        page_id: str,
        database_title: str = "Görevler",
        client: Optional[Client] = None,
    ) -> None:
        self._notion = client or Client(auth=api_key)
        self._page_id = page_id
        self._db_title = database_title
        self._db_id_cache: Optional[str] = None

    # ---- database yönetimi ------------------------------------------------
    def _get_or_create_db(self) -> str:
        if self._db_id_cache:
            return self._db_id_cache

        children = self._notion.blocks.children.list(block_id=self._page_id)
        for block in children.get("results", []):
            if block.get("type") == "child_database":
                title = block.get("child_database", {}).get("title", "")
                if title == self._db_title:
                    self._db_id_cache = block["id"]
                    self._ensure_schema(self._db_id_cache)
                    return self._db_id_cache

        new_db = self._notion.databases.create(
            parent={"type": "page_id", "page_id": self._page_id},
            title=[{"type": "text", "text": {"content": self._db_title}}],
            properties=_REQUIRED_PROPERTIES,
        )
        self._db_id_cache = new_db["id"]
        return self._db_id_cache

    def _ensure_schema(self, db_id: str) -> None:
        db = self._notion.databases.retrieve(database_id=db_id)
        existing = set(db.get("properties", {}).keys())
        missing = {k: v for k, v in _REQUIRED_PROPERTIES.items() if k not in existing}
        if missing:
            self._notion.databases.update(database_id=db_id, properties=missing)

    @staticmethod
    def _category_icon(kategori: str) -> dict:
        emoji = kategori.split(" ", 1)[0] if kategori else "📌"
        return {"type": "emoji", "emoji": emoji}

    # ---- yazma ------------------------------------------------------------
    def add(self, text: str, date_str: str, *, oncelik: str = _DEFAULT_ONCELIK,
            kategori: str = _DEFAULT_KATEGORI) -> Task:
        db_id = self._get_or_create_db()
        page = self._notion.pages.create(
            parent={"database_id": db_id},
            icon=self._category_icon(kategori),
            properties={
                "Görev": {"title": [{"text": {"content": text}}]},
                "Durum": {"select": {"name": "Yapılacak"}},
                "Öncelik": {"select": {"name": oncelik}},
                "Kategori": {"select": {"name": kategori}},
                "Tarih": {"date": {"start": date_str}},
            },
        )
        return Task(
            gorev=text, durum="Yapılacak", oncelik=oncelik, kategori=kategori,
            tarih=date_str, notion_page_id=page.get("id"),
        )

    def set_status(self, text: str, status: str, date_str: Optional[str] = None) -> TaskMatch:
        match = self._find(text, date_str)
        if match.task and match.task.notion_page_id:
            self._notion.pages.update(
                page_id=match.task.notion_page_id,
                properties={"Durum": {"select": {"name": status}}},
            )
            match.task.durum = status
        return match

    def add_note(self, text: str, note: str, date_str: Optional[str] = None) -> TaskMatch:
        match = self._find(text, date_str)
        if match.task and match.task.notion_page_id:
            self._notion.pages.update(
                page_id=match.task.notion_page_id,
                properties={"Not": {"rich_text": [{"text": {"content": note}}]}},
            )
        return match

    # ---- arama ------------------------------------------------------------
    def _find(self, text: str, date_str: Optional[str] = None) -> TaskMatch:
        db_id = self._get_or_create_db()
        results = self._notion.databases.query(
            database_id=db_id,
            filter={
                "and": [
                    {"property": "Görev", "title": {"contains": text}},
                    {"property": "Durum", "select": {"equals": "Yapılacak"}},
                ]
            },
        ).get("results", [])

        if not results:
            return TaskMatch(task=None, candidates=[])

        titles = {
            p["properties"]["Görev"]["title"][0]["plain_text"]
            for p in results
            if p["properties"]["Görev"]["title"]
        }

        if date_str:
            same_date = [
                p for p in results
                if p["properties"]["Tarih"]["date"]
                and p["properties"]["Tarih"]["date"]["start"] == date_str
            ]
            if len(same_date) == 1:
                return TaskMatch(task=self._page_to_task(same_date[0]), candidates=[])
            if same_date:
                results = same_date

        if len(titles) == 1:
            return TaskMatch(task=self._page_to_task(results[-1]), candidates=[])

        return TaskMatch(task=None, candidates=sorted(titles))

    # ---- listeleme --------------------------------------------------------
    def list_for_date(self, date_str: str) -> Sequence[Task]:
        db_id = self._get_or_create_db()
        results = self._notion.databases.query(
            database_id=db_id,
            filter={"property": "Tarih", "date": {"equals": date_str}},
        ).get("results", [])
        return [self._page_to_task(p) for p in results]

    def list_overdue(self, before_date_str: str) -> Sequence[Task]:
        db_id = self._get_or_create_db()
        results = self._notion.databases.query(
            database_id=db_id,
            filter={
                "and": [
                    {"property": "Tarih", "date": {"before": before_date_str}},
                    {"property": "Durum", "select": {"equals": "Yapılacak"}},
                ]
            },
        ).get("results", [])
        return [self._page_to_task(p) for p in results]

    def list_range(self, start_date_str: str, end_date_str: str) -> Sequence[Task]:
        db_id = self._get_or_create_db()
        results = self._notion.databases.query(
            database_id=db_id,
            filter={
                "and": [
                    {"property": "Tarih", "date": {"on_or_after": start_date_str}},
                    {"property": "Tarih", "date": {"on_or_before": end_date_str}},
                ]
            },
            sorts=[{"property": "Tarih", "direction": "ascending"}],
        ).get("results", [])
        return [self._page_to_task(p) for p in results]

    def list_all_open(self) -> Sequence[Task]:
        db_id = self._get_or_create_db()
        results = self._notion.databases.query(
            database_id=db_id,
            filter={"property": "Durum", "select": {"equals": "Yapılacak"}},
            sorts=[{"property": "Tarih", "direction": "ascending"}],
        ).get("results", [])
        return [self._page_to_task(p) for p in results]

    # ---- yardımcılar ------------------------------------------------------
    @staticmethod
    def _page_to_task(page: dict) -> Task:
        props = page["properties"]
        title_parts = props["Görev"]["title"]
        title = title_parts[0]["plain_text"] if title_parts else ""
        durum = props["Durum"]["select"]["name"] if props["Durum"]["select"] else "Yapılacak"
        oncelik = props.get("Öncelik", {}).get("select")
        kategori = props.get("Kategori", {}).get("select")
        tarih = props["Tarih"]["date"]["start"] if props["Tarih"]["date"] else None
        return Task(
            gorev=title,
            durum=durum,
            oncelik=oncelik["name"] if oncelik else _DEFAULT_ONCELIK,
            kategori=kategori["name"] if kategori else _DEFAULT_KATEGORI,
            tarih=tarih,
            notion_page_id=page.get("id"),
        )

    def health_check(self) -> bool:
        try:
            self._get_or_create_db()
            return True
        except Exception:
            return False
