# ADR-001: Lightweight Event Sourcing, Idempotent Deduplication, and Watermark CDC

- **Status:** Accepted
- **Date:** 2026-11-22
- **Governance & Standards:** `ODCS v3.1`, `SQLite WAL`, `DuckDB + Apache Parquet (ZSTD)`, `SHA-256 Outbox`, `dbt Incremental Merge`

## Context

Field-service and operational telemetry workloads generate high-frequency lifecycle transitions across teams and regions (benchmarked at 50,000+ service orders per year, yielding roughly 200,000 to 300,000 state-transition events across `CREATED -> ASSIGNED -> IN_TRANSIT -> IN_EXECUTION -> ON_HOLD -> COMPLETED | SLA_BREACHED`) alongside local operational telemetry streams from the C.O.R.E. ecosystem.

In distributed field operations and asynchronous agent pipelines, event ingestion faces four deterministic failure modes:
1. **Duplicate deliveries:** Mobile clients, ITSM queues, or local collectors re-emit identical events during retries or replays.
2. **Out-of-order and late-arriving events:** A technician in an offline zone can sync a `COMPLETED` event hours after the transition occurred, after an earlier incremental batch already advanced the business-event clock.
3. **Partial batch corruption:** Upstream producers can emit missing canonical keys, invalid state enums, or sentinel timestamps that must not poison analytical Silver/Gold layers.
4. **Local FinOps constraints:** Running a continuous cloud warehouse or distributed streaming cluster locally adds unnecessary infrastructure cost and setup latency; a self-contained lakehouse must run deterministically offline in under 60 seconds.

## Decision

We adopt a three-tier local Medallion event lakehouse built on embedded ACID and columnar engines:

1. **Bronze Append-Only Event Store (`SQLite WAL` + `SHA-256` Outbox):**
   - Raw events are persisted into an append-only SQLite table configured with `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, and `PRAGMA busy_timeout=5000`.
   - Each ingestion batch generates an immutable outbox manifest sealed with batch metadata (`batch_id`, `source_name`, `record_count`, `payload_sha256`, `ingested_at_utc`) to provide verifiable lineage and replay auditability.

2. **Idempotent Deduplication & Deterministic Tie-Breaking:**
   - Every event carries a unique `event_id` (`STRING`) and monotonic/high-precision `updated_at` (`UTC` timestamp) alongside its business `event_ts` and ingestion timestamp `ingested_at`.
   - Bronze ingestion enforces `event_id` uniqueness (`INSERT OR IGNORE` / conflict accounting) so repeated deliveries of the exact same `event_id` are dropped idempotently and recorded as `duplicates_skipped` metrics.
   - When multiple versions or updates exist for a lifecycle transition, Silver compaction and dbt staging order deterministically by `(order_id, updated_at, ingested_at, event_id)`, ensuring full or partial replays converge to identical downstream state.

3. **ODCS v3.1 Event Contract Gate & Quarantine Isolation:**
   - Before entering Silver storage, `CdcEventIngestor` validates every record against an ODCS v3.1 contract enforcing:
     - Required canonical `STRING` identifiers (`event_id`, `order_id`, `team_id`, `region_id`, `priority`).
     - Valid lifecycle state enum (`CREATED`, `ASSIGNED`, `IN_TRANSIT`, `IN_EXECUTION`, `ON_HOLD`, `COMPLETED`, `SLA_BREACHED`).
     - Valid ISO-8601 UTC timestamps (`event_ts`, `updated_at`) and sentinel normalization (`"N/A"`, `"-1"`, `""` -> `NULL` on optional attributes).
   - Events failing contract validation are diverted atomically to `quarantine/` with structured violation codes and batch manifests, preserving clean Silver partitions.

4. **Ingestion-Watermark CDC (`CdcEventIngestor`) & dbt Incremental `merge`:**
   - Incremental extraction tracks a persisted `ingested_at` (and monotonic row sequence) watermark rather than filtering solely by business `event_ts`. Consequently, late-arriving or backfilled events with older `event_ts` values are captured reliably on the next CDC run.
   - Validated incremental slices are materialized by embedded DuckDB as `ZSTD`-compressed Apache Parquet via atomic swaps (`.tmp.parquet` -> atomic rename).
   - Downstream `dbt-duckdb` models operate incrementally (`materialized='incremental'`, `incremental_strategy='merge'`, `unique_key='order_id'` at order grain): whenever any new or late event touches an `order_id` in the incremental CDC window, the pipeline re-aggregates the complete event history of that `order_id` from Silver before merging, guaranteeing idempotent span, pause, and SLA recalculation.

## Consequences

- **Positive:**
  - Zero-cost, offline-first ACID event store with deterministic replay and sub-minute local execution for 50,000+ service orders.
  - Immunity to duplicate deliveries, out-of-order events, late-arriving syncs, and partial re-ingestion.
  - Clean separation between rejected records (`quarantine/`) and trusted analytics (`silver/` and `dbt` marts).
- **Trade-offs:**
  - Single-node embedded storage (`SQLite WAL` + `DuckDB`) targets operational analytics up to millions of events per host rather than multi-writer horizontal cluster ingestion; future cloud scaling can mirror Silver/Gold Parquet artifacts without altering upstream event contracts.
  - Re-aggregating touched `order_id` lifecycles during incremental `merge` requires Silver Parquet partitions to remain queryable by `order_id`, which DuckDB columnar pruning handles efficiently at this scale.

## Verification Criteria

- Repeated ingestion of the same synthetic batch (`seed=42`) yields zero duplicate rows in Bronze/Silver and identical output manifest hashes.
- Chaos test suites inject out-of-order timestamps, duplicate `event_id` records, late-arriving `COMPLETED` transitions, and malformed payloads, proving 0 Silver/Gold corruption and exact quarantine capture.
