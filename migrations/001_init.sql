-- ============================================================================
-- Twm Uzun Vadeli Hafıza — Faz 0 şeması (Supabase / Postgres + pgvector)
-- ----------------------------------------------------------------------------
-- PDF kuralları:
--   * Her katman BAĞIMSIZ tablo (tek vektör DB'ye karıştırma yok).
--   * Her hafıza TAM OLARAK bir kategoriye ait (type NOT NULL + enum).
--   * 17 alanlı ortak şema.
--   * Layer 5 (Task) burada YOK -> backend'i Notion.
--
-- NOT: vektör boyutu 1536 = text-embedding-3-small. Embedding modeli/boyutu
-- değişirse bu tabloların vector(1536) tanımı ve tüm embedding'ler yenilenmeli.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid()

-- Enum tipleri -------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE memory_type AS ENUM
        ('identity','preference','episode','knowledge','task','reflection');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE memory_status AS ENUM
        ('active','archived','superseded','deleted');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Ortak metadata tablosu şablonu (her katman için tekrarlanır) --------------
-- Not: Kısıt olarak type sabitlenir; böylece yanlış katmana yazım engellenir.

-- Layer 1 — Identity --------------------------------------------------------
CREATE TABLE IF NOT EXISTS mem_identity (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type                memory_type NOT NULL DEFAULT 'identity' CHECK (type = 'identity'),
    title               text NOT NULL,
    content             text NOT NULL,
    summary             text,
    importance          real NOT NULL DEFAULT 0.9 CHECK (importance >= 0 AND importance <= 1),
    confidence          real NOT NULL DEFAULT 0.7 CHECK (confidence >= 0 AND confidence <= 1),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    last_accessed       timestamptz,
    access_count        integer NOT NULL DEFAULT 0,
    expires_at          timestamptz,
    source_conversation text,
    related_memory_ids  uuid[] NOT NULL DEFAULT '{}',
    tags                text[] NOT NULL DEFAULT '{}',
    status              memory_status NOT NULL DEFAULT 'active',
    is_persistent       boolean NOT NULL DEFAULT true
);

-- Layer 2 — Preference ------------------------------------------------------
CREATE TABLE IF NOT EXISTS mem_preference (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type                memory_type NOT NULL DEFAULT 'preference' CHECK (type = 'preference'),
    title               text NOT NULL,
    content             text NOT NULL,
    summary             text,
    importance          real NOT NULL DEFAULT 0.7 CHECK (importance >= 0 AND importance <= 1),
    confidence          real NOT NULL DEFAULT 0.7 CHECK (confidence >= 0 AND confidence <= 1),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    last_accessed       timestamptz,
    access_count        integer NOT NULL DEFAULT 0,
    expires_at          timestamptz,
    source_conversation text,
    related_memory_ids  uuid[] NOT NULL DEFAULT '{}',
    tags                text[] NOT NULL DEFAULT '{}',
    status              memory_status NOT NULL DEFAULT 'active',
    is_persistent       boolean NOT NULL DEFAULT true
);

-- Layer 3 — Episode ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS mem_episode (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type                memory_type NOT NULL DEFAULT 'episode' CHECK (type = 'episode'),
    title               text NOT NULL,
    content             text NOT NULL,
    summary             text,
    importance          real NOT NULL DEFAULT 0.5 CHECK (importance >= 0 AND importance <= 1),
    confidence          real NOT NULL DEFAULT 0.7 CHECK (confidence >= 0 AND confidence <= 1),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    last_accessed       timestamptz,
    access_count        integer NOT NULL DEFAULT 0,
    expires_at          timestamptz,
    source_conversation text,
    related_memory_ids  uuid[] NOT NULL DEFAULT '{}',
    tags                text[] NOT NULL DEFAULT '{}',
    status              memory_status NOT NULL DEFAULT 'active',
    is_persistent       boolean NOT NULL DEFAULT false
);

-- Layer 4 — Knowledge -------------------------------------------------------
CREATE TABLE IF NOT EXISTS mem_knowledge (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type                memory_type NOT NULL DEFAULT 'knowledge' CHECK (type = 'knowledge'),
    title               text NOT NULL,
    content             text NOT NULL,
    summary             text,
    importance          real NOT NULL DEFAULT 0.5 CHECK (importance >= 0 AND importance <= 1),
    confidence          real NOT NULL DEFAULT 0.7 CHECK (confidence >= 0 AND confidence <= 1),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    last_accessed       timestamptz,
    access_count        integer NOT NULL DEFAULT 0,
    expires_at          timestamptz,
    source_conversation text,
    related_memory_ids  uuid[] NOT NULL DEFAULT '{}',
    tags                text[] NOT NULL DEFAULT '{}',
    status              memory_status NOT NULL DEFAULT 'active',
    is_persistent       boolean NOT NULL DEFAULT false
);

-- Layer 6 — Reflection ------------------------------------------------------
CREATE TABLE IF NOT EXISTS mem_reflection (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type                memory_type NOT NULL DEFAULT 'reflection' CHECK (type = 'reflection'),
    title               text NOT NULL,
    content             text NOT NULL,
    summary             text,
    importance          real NOT NULL DEFAULT 0.6 CHECK (importance >= 0 AND importance <= 1),
    confidence          real NOT NULL DEFAULT 0.7 CHECK (confidence >= 0 AND confidence <= 1),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    last_accessed       timestamptz,
    access_count        integer NOT NULL DEFAULT 0,
    expires_at          timestamptz,
    source_conversation text,
    related_memory_ids  uuid[] NOT NULL DEFAULT '{}',
    tags                text[] NOT NULL DEFAULT '{}',
    status              memory_status NOT NULL DEFAULT 'active',
    is_persistent       boolean NOT NULL DEFAULT false
);

-- ----------------------------------------------------------------------------
-- Vektör tabloları (katman başına ayrı) — pgvector
-- Her satır ilgili mem_* tablosundaki bir kayda 1-1 bağlıdır.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vec_identity (
    memory_id uuid PRIMARY KEY REFERENCES mem_identity(id) ON DELETE CASCADE,
    embedding vector(1536) NOT NULL
);
CREATE TABLE IF NOT EXISTS vec_preference (
    memory_id uuid PRIMARY KEY REFERENCES mem_preference(id) ON DELETE CASCADE,
    embedding vector(1536) NOT NULL
);
CREATE TABLE IF NOT EXISTS vec_episode (
    memory_id uuid PRIMARY KEY REFERENCES mem_episode(id) ON DELETE CASCADE,
    embedding vector(1536) NOT NULL
);
CREATE TABLE IF NOT EXISTS vec_knowledge (
    memory_id uuid PRIMARY KEY REFERENCES mem_knowledge(id) ON DELETE CASCADE,
    embedding vector(1536) NOT NULL
);
CREATE TABLE IF NOT EXISTS vec_reflection (
    memory_id uuid PRIMARY KEY REFERENCES mem_reflection(id) ON DELETE CASCADE,
    embedding vector(1536) NOT NULL
);

-- Yaklaşık en yakın komşu index'leri (cosine). Veri arttıkça lists ayarlanır.
CREATE INDEX IF NOT EXISTS idx_vec_identity   ON vec_identity   USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_vec_preference ON vec_preference USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_vec_episode    ON vec_episode    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_vec_knowledge  ON vec_knowledge  USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_vec_reflection ON vec_reflection USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Sık kullanılan metadata filtreleri için yardımcı index'ler.
CREATE INDEX IF NOT EXISTS idx_ident_status  ON mem_identity   (status);
CREATE INDEX IF NOT EXISTS idx_pref_status   ON mem_preference (status);
CREATE INDEX IF NOT EXISTS idx_epi_status    ON mem_episode    (status, importance DESC);
CREATE INDEX IF NOT EXISTS idx_know_status   ON mem_knowledge  (status, is_persistent);
CREATE INDEX IF NOT EXISTS idx_refl_status   ON mem_reflection (status);
