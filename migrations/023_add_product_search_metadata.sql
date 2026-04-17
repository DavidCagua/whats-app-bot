-- Migration 023: Robust product search — tags, metadata, embeddings
--
-- Adds the columns and indexes needed for the hybrid lexical + tag +
-- semantic-vector product search. Replaces the old ILIKE-only paths.
--
-- Columns:
--   tags TEXT[]       — curated search tags (hot path, GIN indexed)
--   metadata JSONB    — extensible per-product attributes (allergens,
--                       dietary flags, nutrition, images, prep time,
--                       per-business custom fields, etc.)
--   embedding vector  — semantic vector (text-embedding-3-small, 1536-dim)
--
-- Requires the pgvector extension (Supabase has it built in).
-- In CI (vanilla Postgres) the vector column and index are skipped.

CREATE EXTENSION IF NOT EXISTS unaccent;

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

-- GIN index on tags for fast array containment / overlap queries
CREATE INDEX IF NOT EXISTS idx_products_tags_gin
    ON products USING gin(tags);

-- GIN index on metadata using jsonb_path_ops (smaller and faster for @> lookups)
CREATE INDEX IF NOT EXISTS idx_products_metadata_gin
    ON products USING gin(metadata jsonb_path_ops);

-- pgvector: embedding column + IVFFlat index (skipped gracefully if
-- the extension is not available, e.g. plain Postgres in CI)
DO $$
BEGIN
  CREATE EXTENSION IF NOT EXISTS vector;
  ALTER TABLE products ADD COLUMN IF NOT EXISTS embedding vector(1536);
  CREATE INDEX IF NOT EXISTS idx_products_embedding_cosine
      ON products USING ivfflat (embedding vector_cosine_ops)
      WITH (lists = 100);
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'pgvector not available — skipping embedding column and index';
END
$$;
