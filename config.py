import os

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
NOTION_API_KEY = os.environ["NOTION_API_KEY"]
NOTION_PAGE_ID = os.environ["NOTION_PAGE_ID"]

# Görevlerin tutulacağı database'in başlığı.
# Bot ilk çalıştığında bu isimde bir database'i NOTION_PAGE_ID sayfasının
# altında arar, yoksa kendisi oluşturur.
DATABASE_TITLE = "Görevler"

# Türkiye saat dilimi - "bugün" / "yarın" hesaplamaları için kullanılır.
TIMEZONE = "Europe/Istanbul"

# Sohbet hafızasının tutulacağı SQLite dosyasının yolu.
MEMORY_DB_PATH = os.environ.get("MEMORY_DB_PATH", "twm_memory.db")
