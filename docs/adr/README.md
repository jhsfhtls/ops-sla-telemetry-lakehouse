# Architecture Decision Records (ADRs)

This directory contains the Architecture Decision Records for **ops-sla-telemetry-lakehouse**.

| ID | Title | Status | Date | Governance & Standards |
| :--- | :--- | :--- | :--- | :--- |
| [ADR-001](ADR-001-event-sourcing-idempotent-cdc.md) | Lightweight Event Sourcing, Idempotent Deduplication, and Watermark CDC | Accepted | 2026-11-22 | `ODCS v3.1`, `SQLite WAL`, `DuckDB Parquet ZSTD`, `dbt Incremental Merge` |
| [ADR-002](ADR-002-sla-mathematics-burn-rate-windows.md) | Dual-Calendar SLA Mathematics, Multi-Window Error-Budget Burn Rate, and MTTR/MTBF | Accepted | 2026-11-22 | `99.0% SLO`, `SRE Burn Rate`, `Business Hours vs 24x7`, `MTTR/MTBF` |
| [ADR-003](ADR-003-multi-repo-privacy-boundary.md) | Multi-Repo Read-Only Privacy Boundary, Strict Allowlists, and Dry-Run Dispatch Gate | Accepted | 2026-11-22 | `POP_T_154`, `POP_T_009`, `POP_B_179`, `Zero-Trust Human Gate` |
