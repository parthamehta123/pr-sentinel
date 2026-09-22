-- 001_core.sql — the relational shape: what we reviewed and what we concluded.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS repositories (
    id               BIGSERIAL PRIMARY KEY,
    github_repo_id   BIGINT      NOT NULL UNIQUE,
    full_name        TEXT        NOT NULL,
    default_branch   TEXT        NOT NULL DEFAULT 'main',
    indexed_sha      TEXT,
    indexed_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS repositories_full_name_idx ON repositories (full_name);

CREATE TABLE IF NOT EXISTS pull_requests (
    id          BIGSERIAL PRIMARY KEY,
    repo_id     BIGINT      NOT NULL REFERENCES repositories (id) ON DELETE CASCADE,
    number      INTEGER     NOT NULL,
    head_sha    TEXT        NOT NULL,
    base_sha    TEXT        NOT NULL,
    title       TEXT        NOT NULL DEFAULT '',
    author      TEXT        NOT NULL DEFAULT '',
    is_private  BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (repo_id, number, head_sha)
);

-- Durable idempotency ledger. Redis is the fast path; this is the one that
-- survives a Redis flush. INVARIANT-2.
CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id     TEXT PRIMARY KEY,
    event           TEXT        NOT NULL,
    action          TEXT,
    repo_full_name  TEXT,
    pr_number       INTEGER,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    status          TEXT        NOT NULL DEFAULT 'accepted'
                    CHECK (status IN ('accepted', 'ignored', 'duplicate', 'failed'))
);
CREATE INDEX IF NOT EXISTS deliveries_received_at_idx ON deliveries (received_at DESC);

CREATE TABLE IF NOT EXISTS reviews (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pr_id               BIGINT      NOT NULL REFERENCES pull_requests (id) ON DELETE CASCADE,
    delivery_id         TEXT        NOT NULL REFERENCES deliveries (delivery_id),
    status              TEXT        NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'awaiting_human',
                                          'posted', 'suppressed', 'failed')),
    decision            TEXT        CHECK (decision IN ('auto_post', 'escalate', 'suppress')),
    decision_reason     TEXT,
    overall_confidence  NUMERIC(4, 3)
                        CHECK (overall_confidence IS NULL
                               OR (overall_confidence >= 0 AND overall_confidence <= 1)),
    prompt_bundle       TEXT        NOT NULL DEFAULT 'unknown',
    github_review_id    BIGINT,
    total_cost_usd      NUMERIC(12, 6) NOT NULL DEFAULT 0,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    error               TEXT,
    UNIQUE (pr_id, delivery_id)
);
CREATE INDEX IF NOT EXISTS reviews_status_idx ON reviews (status, started_at DESC);

-- One row per specialist per review. This table is what makes resume cheap:
-- on a retry we re-run only the agents that have no row here. ADR-0004.
CREATE TABLE IF NOT EXISTS agent_verdicts (
    review_id       UUID        NOT NULL REFERENCES reviews (id) ON DELETE CASCADE,
    agent           TEXT        NOT NULL,
    status          TEXT        NOT NULL
                    CHECK (status IN ('ok', 'failed', 'timeout', 'skipped', 'budget_denied')),
    model           TEXT,
    prompt_version  TEXT,
    raw             JSONB       NOT NULL DEFAULT '{}'::jsonb,
    input_tokens    INTEGER     NOT NULL DEFAULT 0,
    output_tokens   INTEGER     NOT NULL DEFAULT 0,
    cached_tokens   INTEGER     NOT NULL DEFAULT 0,
    cost_usd        NUMERIC(12, 6) NOT NULL DEFAULT 0,
    duration_ms     INTEGER     NOT NULL DEFAULT 0,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (review_id, agent)
);

CREATE TABLE IF NOT EXISTS findings (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id     UUID        NOT NULL REFERENCES reviews (id) ON DELETE CASCADE,
    agent         TEXT        NOT NULL,
    category      TEXT        NOT NULL,
    severity      TEXT        NOT NULL
                  CHECK (severity IN ('critical', 'major', 'minor', 'info')),
    file_path     TEXT        NOT NULL,
    line_start    INTEGER     NOT NULL,
    line_end      INTEGER     NOT NULL,
    title         TEXT        NOT NULL,
    body          TEXT        NOT NULL,
    -- INVARIANT-3: a finding without a rationale and a confidence is not a
    -- finding, it is an opinion. Both are NOT NULL on purpose.
    rationale     TEXT        NOT NULL,
    confidence    NUMERIC(4, 3) NOT NULL
                  CHECK (confidence >= 0 AND confidence <= 1),
    evidence      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    agreeing      TEXT[]      NOT NULL DEFAULT '{}',
    merged_into   UUID        REFERENCES findings (id) ON DELETE SET NULL,
    posted        BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS findings_review_idx ON findings (review_id);
CREATE INDEX IF NOT EXISTS findings_open_idx ON findings (review_id) WHERE merged_into IS NULL;

-- The human queue. This is the scarce resource the whole system optimises for.
CREATE TABLE IF NOT EXISTS hitl_items (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id    UUID        NOT NULL REFERENCES reviews (id) ON DELETE CASCADE,
    reason       TEXT        NOT NULL
                 CHECK (reason IN ('low_confidence', 'critical_security', 'agent_failure',
                                   'budget_exceeded', 'policy')),
    priority     INTEGER     NOT NULL DEFAULT 100,
    state        TEXT        NOT NULL DEFAULT 'open'
                 CHECK (state IN ('open', 'approved', 'rejected', 'expired')),
    summary      TEXT        NOT NULL DEFAULT '',
    decided_by   TEXT,
    decided_at   TIMESTAMPTZ,
    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS hitl_open_idx ON hitl_items (priority, created_at) WHERE state = 'open';

-- Disputes and approvals. Read back with a minimum-evidence threshold before
-- any of it is allowed to change behaviour. ADR-0007.
CREATE TABLE IF NOT EXISTS feedback (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id   UUID        REFERENCES reviews (id) ON DELETE CASCADE,
    finding_id  UUID        REFERENCES findings (id) ON DELETE SET NULL,
    source      TEXT        NOT NULL
                CHECK (source IN ('hitl_gate', 'pr_reply', 'resolved_thread', 'manual')),
    verdict     TEXT        NOT NULL
                CHECK (verdict IN ('accepted', 'disputed', 'duplicate', 'wont_fix')),
    actor       TEXT,
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS feedback_finding_idx ON feedback (finding_id);

