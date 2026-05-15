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

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS embedding vector(1536);

-- GIN index on tags for fast array containment / overlap queries
CREATE INDEX IF NOT EXISTS idx_products_tags_gin
    ON products USING gin(tags);

-- GIN index on metadata using jsonb_path_ops (smaller and faster for @> lookups)
CREATE INDEX IF NOT EXISTS idx_products_metadata_gin
    ON products USING gin(metadata jsonb_path_ops);

-- IVFFlat cosine index for approximate nearest-neighbor embedding search.
-- lists=100 is a reasonable default for catalogs up to ~10k products.
-- For larger catalogs bump lists to sqrt(row_count).
CREATE INDEX IF NOT EXISTS idx_products_embedding_cosine
    ON products USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
