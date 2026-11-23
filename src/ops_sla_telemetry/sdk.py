"""Unified Python SDK for ops-sla-telemetry-lakehouse."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from ops_sla_telemetry.collectors import CoreMultiRepoCollector
from ops_sla_telemetry.config import LakehouseSettings
from ops_sla_telemetry.dispatcher import (
    AlertCandidate,
    AlertDispatcher,
    DryRunLogAdapter,
    NotifyWebhookAdapter,
    TickTickBridgeAdapter,
    build_incident_key,
)
from ops_sla_telemetry.ingestion import CdcEventIngestor
from ops_sla_telemetry.simulator import FieldOrderSimulator


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def _resolve_dbt_executable() -> str:
    candidate = Path(sys.executable).with_name("dbt.exe" if sys.platform == "win32" else "dbt")
    if candidate.is_file():
        return str(candidate)
    found = shutil.which("dbt")
    if found:
        return found
    raise FileNotFoundError("Unable to locate 'dbt' executable in the active environment.")


class OpsSlaTelemetry:
    """High-level SDK powering CLI, FastAPI REST, and MCP interfaces identically."""

    def __init__(
        self,
        settings: LakehouseSettings | None = None,
        *,
        duckdb_path: Path | None = None,
        ledger_db_path: Path | None = None,
        dbt_target_path: Path | None = None,
    ) -> None:
        self.settings = settings or LakehouseSettings()
        assert self.settings.gold_dir is not None
        assert self.settings.bronze_db_path is not None
        self.duckdb_path = (
            Path(duckdb_path).resolve()
            if duckdb_path is not None
            else (self.settings.gold_dir / "ops_sla_telemetry.duckdb")
        )
        self.ledger_db_path = (
            Path(ledger_db_path).resolve()
            if ledger_db_path is not None
            else (self.settings.bronze_db_path.parent / "dispatch_ledger.sqlite")
        )
        self.dbt_target_path = (
            Path(dbt_target_path).resolve()
            if dbt_target_path is not None
            else (self.settings.repo_root / "dbt" / "target")
        )

    def _run_dbt_build(self) -> dict[str, Any]:
        dbt_bin = _resolve_dbt_executable()
        dbt_dir = self.settings.repo_root / "dbt"
        assert self.settings.silver_dir is not None
        assert self.settings.reference_dir is not None
        self.duckdb_path.parent.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["OST_DUCKDB_PATH"] = str(self.duckdb_path)
        env["OST_SILVER_GLOB"] = (self.settings.silver_dir / "service_events_*.parquet").as_posix()
        env["OST_HOLIDAYS_CSV"] = (
            self.settings.reference_dir / "br_national_holidays.csv"
        ).as_posix()

        proc = subprocess.run(
            [
                dbt_bin,
                "build",
                "--project-dir",
                str(dbt_dir),
                "--profiles-dir",
                str(dbt_dir),
                "--target-path",
                str(self.dbt_target_path),
            ],
            cwd=str(self.settings.repo_root),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"dbt build failed:\n{proc.stdout}\n{proc.stderr}")
        return {"dbt_returncode": proc.returncode, "duckdb_path": str(self.duckdb_path)}

    def _query_duckdb(
        self,
        sql: str,
        params: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.duckdb_path.is_file():
            raise FileNotFoundError(
                f"DuckDB Gold warehouse not found at {self.duckdb_path}. Run 'ost ingest' first."
            )
        with duckdb.connect(str(self.duckdb_path), read_only=True) as conn:
            cursor = conn.execute(sql, params or [])
            columns = [desc[0] for desc in (cursor.description or ())]
            return [
                {col: _json_safe(val) for col, val in zip(columns, row, strict=True)}
                for row in cursor.fetchall()
            ]

    def ingest(
        self,
        *,
        seed: int = 42,
        order_count: int = 50_000,
        run_dbt: bool = True,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate synthetic lifecycle events, ingest Bronze/Silver CDC, and build dbt marts."""
        self.settings.ensure_directories()
        events = FieldOrderSimulator(seed=seed, order_count=order_count).generate_events()
        ingestor = CdcEventIngestor(settings=self.settings)
        ingest_report, sync_report = ingestor.ingest_and_sync(
            events,
            source_name="field_simulator",
            batch_id=batch_id,
        )

        dbt_info = self._run_dbt_build() if run_dbt else None
        return {
            "seed": seed,
            "order_count": order_count,
            "events_generated": len(events),
            "batch_id": ingest_report.batch_id,
            "inserted_count": ingest_report.inserted_count,
            "duplicates_skipped": ingest_report.duplicates_skipped,
            "quarantined_count": ingest_report.quarantined_count,
            "rows_synced": sync_report.rows_synced,
            "silver_parquet_path": str(sync_report.parquet_path)
            if sync_report.parquet_path
            else None,
            "dbt_executed": run_dbt,
            "duckdb_path": dbt_info["duckdb_path"] if dbt_info else str(self.duckdb_path),
        }

    def sla_status(
        self,
        *,
        region_id: str | None = None,
        priority: str | None = None,
    ) -> dict[str, Any]:
        """Return 99% SLO compliance, error-budget remaining, burn rates, and MTTR/MTBF."""
        clauses: list[str] = []
        params: list[Any] = []
        if region_id:
            clauses.append("region_id = ?")
            params.append(region_id)
        if priority:
            clauses.append("priority = ?")
            params.append(priority)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._query_duckdb(
            f"""
            SELECT
                cohort_key,
                region_id,
                priority,
                evaluated_30d,
                breached_30d,
                sla_compliance_pct_30d,
                error_budget_remaining_pct,
                burn_rate_1h,
                burn_rate_6h,
                burn_rate_24h,
                burn_rate_7d,
                mttr_hours_30d,
                mtbf_hours_30d,
                at_risk_queue_count,
                alert_severity
            FROM mart_sla_burn_rate_alerts
            {where_sql}
            ORDER BY CASE WHEN cohort_key = 'ALL|ALL' THEN 0 ELSE 1 END, cohort_key ASC
            """,
            params,
        )

        global_summary = next(
            (row for row in rows if row["cohort_key"] == "ALL|ALL"),
            rows[0] if rows else None,
        )
        return {
            "duckdb_path": str(self.duckdb_path),
            "slo_target_pct": 99.0,
            "count": len(rows),
            "global_summary": global_summary,
            "cohorts": rows,
        }

    def at_risk(
        self,
        *,
        limit: int = 25,
        region_id: str | None = None,
        priority: str | None = None,
    ) -> dict[str, Any]:
        """Return open orders approaching SLA breach (< 24h remaining) ordered by urgency."""
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}.")
        clauses = ["is_closed = FALSE", "sla_status IN ('AT_RISK', 'SLA_BREACHED')"]
        params: list[Any] = []
        if region_id:
            clauses.append("region_id = ?")
            params.append(region_id)
        if priority:
            clauses.append("priority = ?")
            params.append(priority)
        params.append(limit)

        rows = self._query_duckdb(
            f"""
            SELECT
                order_id,
                region_id,
                team_id,
                priority,
                calendar_mode,
                current_state,
                sla_status,
                active_elapsed_minutes,
                sla_target_minutes,
                remaining_sla_hours,
                created_ts
            FROM fct_service_order_sla
            WHERE {' AND '.join(clauses)}
            ORDER BY remaining_sla_hours ASC, created_ts ASC, order_id ASC
            LIMIT ?
            """,
            params,
        )
        return {"count": len(rows), "limit": limit, "items": rows}

    def team_capacity(
        self,
        *,
        limit: int = 25,
        region_id: str | None = None,
        team_id: str | None = None,
    ) -> dict[str, Any]:
        """Return aggregated team productivity, SLA attainment, MTTR, MTBF, and open at-risk WIP."""
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}.")
        clauses: list[str] = []
        params: list[Any] = []
        if region_id:
            clauses.append("region_id = ?")
            params.append(region_id)
        if team_id:
            clauses.append("team_id = ?")
            params.append(team_id)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)

        rows = self._query_duckdb(
            f"""
            SELECT
                region_id,
                team_id,
                CAST(SUM(orders_created) AS BIGINT) AS total_orders_created,
                CAST(SUM(orders_completed) AS BIGINT) AS total_orders_completed,
                CAST(SUM(orders_breached) AS BIGINT) AS total_orders_breached,
                CAST(SUM(orders_at_risk) AS BIGINT) AS total_orders_at_risk,
                CASE
                    WHEN SUM(orders_completed + orders_breached) > 0 THEN
                        ROUND(
                            100.0 * SUM(orders_completed) / SUM(orders_completed + orders_breached),
                            4
                        )
                    ELSE 100.0
                END AS sla_compliance_pct,
                ROUND(AVG(mttr_hours), 4) AS avg_mttr_hours,
                ROUND(AVG(avg_hold_minutes), 4) AS avg_hold_minutes
            FROM fct_team_daily_capacity
            {where_sql}
            GROUP BY region_id, team_id
            ORDER BY total_orders_at_risk DESC, sla_compliance_pct ASC, team_id ASC
            LIMIT ?
            """,
            params,
        )
        return {"count": len(rows), "limit": limit, "teams": rows}

    def core_health(
        self,
        *,
        core_root: Path | None = None,
        studies_root: Path | None = None,
        write_snapshot: bool = False,
        output_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Collect sanitized read-only C.O.R.E. multi-repo SLA & freshness metadata."""
        resolved_core = (
            Path(core_root).resolve()
            if core_root is not None
            else (self.settings.repo_root.parent / "core")
        )
        collector = CoreMultiRepoCollector(
            core_root=resolved_core,
            studies_root=studies_root,
            output_dir=output_dir,
            public_repo_root=self.settings.repo_root,
        )
        snapshot = collector.collect_all()
        snapshot_path: Path | None = None
        if write_snapshot:
            snapshot_path = collector.write_snapshot(snapshot)

        return {
            "collected_date": snapshot["collected_date"],
            "privacy_mode": snapshot["privacy_mode"],
            "summary": snapshot["summary"],
            "snapshot_path": str(snapshot_path) if snapshot_path is not None else None,
            "sources": snapshot["sources"],
        }

    def sync_ticktick(
        self,
        *,
        execute: bool = False,
        confirm: bool = False,
        max_alerts: int = 10,
        ticktick_script: Path | None = None,
        notify_script: Path | None = None,
        project_name: str = "00_INBOX_AGENTES",
        priority: int = 5,
        window_bucket: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate burn-rate alert candidates and dispatch or preview via dry-run-first gate."""
        rows = self._query_duckdb(
            """
            SELECT
                cohort_key,
                region_id,
                priority,
                sla_compliance_pct_30d,
                burn_rate_1h,
                burn_rate_6h,
                burn_rate_24h,
                burn_rate_7d,
                at_risk_queue_count,
                alert_severity
            FROM mart_sla_burn_rate_alerts
            WHERE alert_severity != 'OK'
            ORDER BY cohort_key ASC
            """
        )

        bucket = window_bucket or "rolling-current"
        candidates = [
            AlertCandidate(
                incident_key=build_incident_key(
                    "FIELD_SERVICE",
                    str(row["cohort_key"]),
                    str(row["alert_severity"]),
                    bucket,
                ),
                cohort_key=str(row["cohort_key"]),
                region_id=str(row["region_id"]),
                priority=str(row["priority"]),
                severity=str(row["alert_severity"]),
                summary=(
                    f"SLA 30d={float(row['sla_compliance_pct_30d']):.2f}%, "
                    f"at_risk={int(row['at_risk_queue_count'])}"
                ),
                burn_rate_1h=float(row["burn_rate_1h"]),
                burn_rate_6h=float(row["burn_rate_6h"]),
                burn_rate_24h=float(row["burn_rate_24h"]),
                burn_rate_7d=float(row["burn_rate_7d"]),
                sla_compliance_pct_30d=float(row["sla_compliance_pct_30d"]),
                at_risk_queue_count=int(row["at_risk_queue_count"]),
            )
            for row in rows
        ]

        resolved_ticktick = ticktick_script
        if resolved_ticktick is None and execute and confirm:
            candidate_script = (
                self.settings.repo_root.parent
                / "core"
                / "scripts"
                / "agent_tools"
                / "ticktick_client.py"
            )
            if candidate_script.is_file():
                resolved_ticktick = candidate_script

        adapters: list[Any] = [
            DryRunLogAdapter(),
            TickTickBridgeAdapter(
                script_path=resolved_ticktick,
                project_name=project_name,
                priority=priority,
            ),
        ]
        if notify_script is not None:
            adapters.append(NotifyWebhookAdapter(script_path=notify_script))

        summary = AlertDispatcher(
            self.ledger_db_path,
            adapters=adapters,
            max_alerts_per_run=max_alerts,
        ).dispatch(candidates, execute=execute, confirm=confirm)

        return {
            "dry_run": summary.dry_run,
            "received_count": summary.received_count,
            "dispatched_count": summary.dispatched_count,
            "suppressed_duplicate_count": summary.suppressed_duplicate_count,
            "capped_count": summary.capped_count,
            "items": [
                {
                    "incident_key": item.incident_key,
                    "severity": item.severity,
                    "runbook_draft": item.runbook_draft,
                    "adapter_results": item.adapter_results,
                }
                for item in summary.items
            ],
        }
