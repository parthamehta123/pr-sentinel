-- Migration 005: Feedback decay support + TigerData DiskANN index
-- The feedback learning loop needs a way to mark old feedback as decayed
-- without violating the append-only audit trail (no deletes).

ALTER TABLE feedback ADD COLUMN IF NOT EXISTS decayed BOOLEAN DEFAULT false;

CREATE INDEX IF NOT EXISTS feedback_decay_idx
    ON feedback (decayed, created_at DESC)
    WHERE decayed = false;

-- When using TigerData cloud with pgvectorscale, create a DiskANN index
-- for efficient nearest-neighbor search at scale. This replaces the default
-- ivfflat index with one that works on SSD for millions of chunks.
-- Uncomment when using TigerData:
-- CREATE INDEX IF NOT EXISTS code_chunks_diskann_idx
--     ON code_chunks USING diskann (embedding);
