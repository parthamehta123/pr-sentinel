-- 004_realtime_aggregates.sql
--
-- TimescaleDB 2.13+ creates continuous aggregates with `materialized_only = true`,
-- so a rollup only shows data the background policy has already processed — up to
-- five minutes stale here. For a cost guard that is a correctness bug, not a
-- latency one: a webhook storm can spend the daily cap inside a single refresh
-- interval and the guard would never see it.
--
-- Real-time aggregation makes the view transparently union the materialised
-- buckets with a live scan of the recent tail. The scan is bounded by the refresh
-- interval, so it stays cheap no matter how large the hypertable grows.

ALTER MATERIALIZED VIEW agent_cost_hourly    SET (timescaledb.materialized_only = false);
ALTER MATERIALIZED VIEW agent_latency_hourly SET (timescaledb.materialized_only = false);
