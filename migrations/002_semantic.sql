-- 002_semantic.sql — the semantic shape: the repository as retrievable memory.
--
-- The vector(1536) width below is load-bearing. It must equal EMBEDDING_DIM in
-- the environment; `pr-sentinel doctor` compares them and refuses to start on a
-- mismatch, because pgvector neither pads nor truncates — it raises at INSERT
-- time, long after the embedding bill has been paid.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS code_chunks (
    id            BIGSERIAL PRIMARY KEY,
    repo_id       BIGINT      NOT NULL REFERENCES repositories (id) ON DELETE CASCADE,
    commit_sha    TEXT        NOT NULL,
    file_path     TEXT        NOT NULL,
    language      TEXT,
    symbol        TEXT,
    start_line    INTEGER     NOT NULL,
    end_line      INTEGER     NOT NULL,
    content       TEXT        NOT NULL,
    content_hash  TEXT        NOT NULL,
    embedding     VECTOR(1536),
    fts           TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (repo_id, commit_sha, file_path, start_line, content_hash)
);

CREATE INDEX IF NOT EXISTS code_chunks_repo_idx ON code_chunks (repo_id, commit_sha);
CREATE INDEX IF NOT EXISTS code_chunks_path_idx ON code_chunks (repo_id, file_path);
CREATE INDEX IF NOT EXISTS code_chunks_fts_idx  ON code_chunks USING GIN (fts);

-- Prefer pgvectorscale's StreamingDiskANN: it keeps the graph on SSD, so recall
-- does not fall off a cliff when the index stops fitting in RAM. Fall back to
-- HNSW on a stock pgvector install.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vectorscale CASCADE;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pgvectorscale unavailable (%); falling back to HNSW', SQLERRM;
END
$$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vectorscale') THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS code_chunks_diskann_idx
                   ON code_chunks USING diskann (embedding vector_cosine_ops)';
    ELSE
        EXECUTE 'CREATE INDEX IF NOT EXISTS code_chunks_hnsw_idx
                   ON code_chunks USING hnsw (embedding vector_cosine_ops)
                   WITH (m = 16, ef_construction = 64)';
    END IF;
END
$$;

-- Procedural memory: the conventions a reviewer would already know. Small,
-- structured, hand-curated, and injected verbatim — never retrieved fuzzily.
CREATE TABLE IF NOT EXISTS conventions (
    id          BIGSERIAL PRIMARY KEY,
    repo_id     BIGINT      REFERENCES repositories (id) ON DELETE CASCADE,
    scope       TEXT        NOT NULL DEFAULT 'global',
    rule        TEXT        NOT NULL,
    source      TEXT,
    active      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conventions_repo_idx ON conventions (repo_id) WHERE active;

