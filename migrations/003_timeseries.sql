-- 003_timeseries.sql — the temporal shape: the event spine.
--
-- Every span, LLM call, decision and error lands here. This is what makes a
-- finding defensible three weeks later, and it is the only reason the cost
-- dashboard does not have to scan raw rows.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS agent_events (
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_id        UUID        NOT NULL DEFAULT gen_random_uuid(),
    review_id       UUID,
    delivery_id     TEXT,
    repo_full_name  TEXT,
    span_id         UUID,
    parent_span_id  UUID,
    kind            TEXT        NOT NULL
                    CHECK (kind IN ('span_start', 'span_end', 'llm_call', 'tool_call',
                                    'retrieval', 'decision', 'error')),
    name            TEXT        NOT NULL,
    agent           TEXT,
    model           TEXT,
    prompt_version  TEXT,
    status          TEXT,
    duration_ms     INTEGER,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cached_tokens   INTEGER,
    cost_usd        NUMERIC(12, 6),
    attributes      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (ts, event_id)
);

SELECT create_hypertable('agent_events', 'ts', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS agent_events_review_idx ON agent_events (review_id, ts DESC);
CREATE INDEX IF NOT EXISTS agent_events_kind_idx   ON agent_events (kind, ts DESC);

-- INVARIANT-4: the audit trail is append-only. Enforced in the database, not by
-- convention — a convention is something an incident talks you out of at 2am.
CREATE OR REPLACE FUNCTION sentinel_reject_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'agent_events is append-only (attempted %)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agent_events_no_update ON agent_events;
CREATE TRIGGER agent_events_no_update
    BEFORE UPDATE ON agent_events
    FOR EACH ROW EXECUTE FUNCTION sentinel_reject_mutation();

DROP TRIGGER IF EXISTS agent_events_no_delete ON agent_events;
CREATE TRIGGER agent_events_no_delete
    BEFORE DELETE ON agent_events
    FOR EACH ROW EXECUTE FUNCTION sentinel_reject_mutation();

-- TRUNCATE is its own command with its own execution path: row-level DELETE
-- triggers never fire for it. It needs a statement-level trigger of its own.
DO $$
BEGIN
    EXECUTE 'DROP TRIGGER IF EXISTS agent_events_no_truncate ON agent_events';
    EXECUTE 'CREATE TRIGGER agent_events_no_truncate
               BEFORE TRUNCATE ON agent_events
               FOR EACH STATEMENT EXECUTE FUNCTION sentinel_reject_mutation()';
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'TRUNCATE trigger not supported here (%); relying on role grants', SQLERRM;
END
$$;


-- Continuous aggregates cannot be created inside a transaction block.
CREATE MATERIALIZED VIEW IF NOT EXISTS agent_cost_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', ts)                    AS bucket,
       agent,
       model,
       count(*)                                     AS calls,
       sum(cost_usd)                                AS cost_usd,
       sum(input_tokens)                            AS input_tokens,
       sum(output_tokens)                           AS output_tokens,
       sum(cached_tokens)                           AS cached_tokens
FROM agent_events
WHERE kind = 'llm_call'
GROUP BY bucket, agent, model
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS agent_latency_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', ts)                        AS bucket,
       agent,
       count(*)                                         AS spans,
       count(*) FILTER (WHERE status = 'error')         AS errors,
       avg(duration_ms)                                 AS avg_ms,
       max(duration_ms)                                 AS max_ms
FROM agent_events
WHERE kind = 'span_end' AND duration_ms IS NOT NULL
GROUP BY bucket, agent
WITH NO DATA;

-- Exact percentiles stay on the raw rows. The rollup above answers "is anything
-- slow or erroring", which is the question a dashboard refresh actually asks;
-- this view answers "how slow, exactly" over a window small enough to scan.
CREATE OR REPLACE VIEW agent_latency_recent AS
SELECT agent,
       count(*)                                                          AS spans,
       percentile_disc(0.50) WITHIN GROUP (ORDER BY duration_ms)         AS p50_ms,
       percentile_disc(0.95) WITHIN GROUP (ORDER BY duration_ms)         AS p95_ms,
       max(duration_ms)                                                  AS max_ms
FROM agent_events
WHERE kind = 'span_end' AND duration_ms IS NOT NULL AND ts > now() - INTERVAL '24 hours'
GROUP BY agent;

SELECT add_continuous_aggregate_policy('agent_cost_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '5 minutes', if_not_exists => TRUE);

SELECT add_continuous_aggregate_policy('agent_latency_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '5 minutes', if_not_exists => TRUE);
