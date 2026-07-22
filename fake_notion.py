"""tests/fake_notion.py
=======================
Notion API'yi taklit eden basit sahte client. Sadece testte kullanılır; gerçek
ağ çağrısı yapmaz. NotionTaskRepository'nin kullandığı yüzeyi (blocks/databases/
pages) minimal biçimde karşılar.
"""
from __future__ import annotations

import uuid


class _Blocks:
    class children:  # noqa: N801
        @staticmethod
        def list(block_id):
            # Hiç database yok -> repo yeni oluşturacak.
            return {"results": []}


class FakeNotionClient:
    def __init__(self):
        self._pages: dict[str, dict] = {}
        self._db_id = "db_" + uuid.uuid4().hex
        self.blocks = _Blocks()
        self.databases = self._Databases(self)
        self.pages = self._Pages(self)

    class _Databases:
        def __init__(self, parent):
            self._p = parent

        def create(self, **kwargs):
            return {"id": self._p._db_id}

        def retrieve(self, database_id):
            return {"properties": {}}

        def update(self, database_id, properties):
            return {"id": database_id}

        def query(self, database_id, filter=None, sorts=None):
            # Filtreyi kabaca uygula: title contains + durum equals.
            pages = list(self._p._pages.values())

            def title_of(p):
                t = p["properties"]["Görev"]["title"]
                return t[0]["plain_text"] if t else ""

            def status_of(p):
                s = p["properties"]["Durum"]["select"]
                return s["name"] if s else "Yapılacak"

            def matches(p):
                if not filter:
                    return True
                conds = filter.get("and", [filter])
                for c in conds:
                    prop = c.get("property")
                    if prop == "Görev" and "title" in c:
                        if c["title"]["contains"] not in title_of(p):
                            return False
                    elif prop == "Durum" and "select" in c:
                        if status_of(p) != c["select"]["equals"]:
                            return False
                return True

            return {"results": [p for p in pages if matches(p)]}

    class _Pages:
        def __init__(self, parent):
            self._p = parent

        def create(self, parent, properties, icon=None):
            pid = "pg_" + uuid.uuid4().hex
            page = {"id": pid, "properties": properties}
            # plain_text alanını doldur (query'de kullanılıyor).
            for t in page["properties"]["Görev"]["title"]:
                t["plain_text"] = t["text"]["content"]
            self._p._pages[pid] = page
            return page

        def update(self, page_id, properties):
            self._p._pages[page_id]["properties"].update(properties)
            return self._p._pages[page_id]
