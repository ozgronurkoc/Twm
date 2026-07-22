"""config.py
============
Merkezi ayarlar ve SAĞLAYICI SEÇİMİ.

Tüm provider seçimleri (DB, vektör, embedding, LLM) buradan env ile yapılır;
kod tabanının hiçbir yerinde somut sağlayıcı sabit değildir. Sağlayıcı
değiştirmek = yalnızca bu dosyadaki env değerlerini değiştirmek.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


# --- Mevcut Twm ayarları (korunuyor) ---------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_PAGE_ID = os.environ.get("NOTION_PAGE_ID", "")
DATABASE_TITLE = "Görevler"
TIMEZONE = "Europe/Istanbul"


# --- Sağlayıcı seçimi ------------------------------------------------------
# DB_PROVIDER:        postgres | sqlite
# VECTOR_PROVIDER:    pgvector           (postgres ile)
# EMBEDDING_PROVIDER: openai
# LLM_PROVIDER:       openai
DB_PROVIDER = os.environ.get("DB_PROVIDER", "postgres")
VECTOR_PROVIDER = os.environ.get("VECTOR_PROVIDER", "pgvector")
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "openai")
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openai")


# --- Veritabanı ------------------------------------------------------------
# AYRI bir Supabase projesinin connection string'i (Nonplo'dan bağımsız!).
# Örn: postgresql://postgres:<pwd>@db.<ref>.supabase.co:5432/postgres
DATABASE_URL = os.environ.get("DATABASE_URL", "")
SQLITE_PATH = os.environ.get("SQLITE_PATH", "twm_dev.db")


# --- Embedding -------------------------------------------------------------
# DİKKAT: boyut değişirse pgvector şeması migration + re-embed ister.
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1536"))


# --- LLM -------------------------------------------------------------------
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT = float(os.environ.get("OPENAI_TIMEOUT", "30"))
OPENAI_MAX_RETRIES = int(os.environ.get("OPENAI_MAX_RETRIES", "3"))
