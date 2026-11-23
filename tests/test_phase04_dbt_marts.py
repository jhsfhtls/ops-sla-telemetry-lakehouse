"""Phase 04 tests: dbt incremental merge pipeline, KPIs, and 100% docs coverage."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
from scripts.lint_dbt_docs import evaluate_manifest

from ops_sla_telemetry.config import LakehouseSettings
from ops_sla_telemetry.ingestion import CdcEventIngestor
from ops_sla_telemetry.simulator import FieldOrderSimulator

REPO_ROOT = Path(__file__).resolve().parents[1]


def _resolve_dbt_executable() -> str:
    candidate = Path(sys.executable).with_name("dbt.exe" if sys.platform == "win32" else "dbt")
    if candidate.is_file():
        return str(candidate)
    found = shutil.which("dbt")
    if found:
        return found
    raise FileNotFoundError("Unable to locate dbt executable in active environment.")


def _run_dbt(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    dbt_bin = _resolve_dbt_executable()
    return subprocess.run(
        [dbt_bin, *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_dbt_build_incremental_merge_and_docs_coverage(tmp_path: Path) -> None:
    """Verify full dbt build, 100% doc coverage, and late-event incremental merge."""
    data_dir = tmp_path / "data"
    duckdb_path = data_dir / "gold" / "ops_sla_telemetry.duckdb"
    target_path = tmp_path / "dbt_target"

    settings = LakehouseSettings(repo_root=REPO_ROOT, data_dir=data_dir)
    ingestor = CdcEventIngestor(settings=settings)
    events = FieldOrderSimulator(seed=42, order_count=1000).generate_events()
    ingest_report, sync_report = ingestor.ingest_and_sync(events, batch_id="dbt_initial_1k")
    assert ingest_report.inserted_count == len(events)
    assert sync_report.rows_synced == len(events)

    env = os.environ.copy()
    env["OST_DUCKDB_PATH"] = str(duckdb_path)
    env["OST_SILVER_GLOB"] = (settings.silver_dir / "service_events_*.parquet").as_posix()
    env["OST_HOLIDAYS_CSV"] = (REPO_ROOT / "data" / "reference" / "br_national_holidays.csv").as_posix()

    build_res = _run_dbt(
        [
            "build",
            "--project-dir",
            str(REPO_ROOT / "dbt"),
            "--profiles-dir",
            str(REPO_ROOT / "dbt"),
            "--target-path",
            str(target_path),
        ],
        env=env,
    )
    assert build_res.returncode == 0, f"dbt build failed:\n{build_res.stdout}\n{build_res.stderr}"

    docs_res = _run_dbt(
        [
            "docs",
            "generate",
            "--project-dir",
            str(REPO_ROOT / "dbt"),
            "--profiles-dir",
            str(REPO_ROOT / "dbt"),
            "--target-path",
            str(target_path),
        ],
        env=env,
    )
    assert docs_res.returncode == 0, f"dbt docs failed:\n{docs_res.stdout}\n{docs_res.stderr}"

    lint_code, bad_models, bad_cols = evaluate_manifest(target_path / "manifest.json")
    assert lint_code == 0, f"Undocumented models={bad_models}, cols={bad_cols}"

    with duckdb.connect(str(duckdb_path), read_only=True) as conn:
        order_count = conn.execute("SELECT COUNT(*) FROM fct_service_order_sla").fetchone()[0]
        assert order_count == 1000

        open_row = conn.execute(
            """
            SELECT order_id, team_id, region_id, priority, created_ts
            FROM fct_service_order_sla
            WHERE is_closed = FALSE
            ORDER BY order_id
            LIMIT 1
            """
        ).fetchone()
        assert open_row is not None
        open_order_id, team_id, region_id, priority, created_ts = open_row

        global_mart = conn.execute(
            """
            SELECT evaluated_30d, sla_compliance_pct_30d, mttr_hours_30d, alert_severity
            FROM mart_sla_burn_rate_alerts
            WHERE cohort_key = 'ALL|ALL'
            """
        ).fetchone()
        assert global_mart is not None
        evaluated_30d, sla_pct_30d, mttr_30d, severity = global_mart
        assert evaluated_30d > 0
        assert 95.0 <= sla_pct_30d <= 100.0
        assert mttr_30d is not None and mttr_30d > 0.0
        assert severity in ("SEV-1", "SEV-2", "SEV-3", "SEV-4", "OK")

        team_cap_count = conn.execute("SELECT COUNT(*) FROM fct_team_daily_capacity").fetchone()[0]
        assert team_cap_count > 0

    # Append a late-arriving COMPLETED event for the open order and re-run incremental dbt build
    late_completion = {
        "event_id": f"EVT-{open_order_id}-LATE-COMPLETE",
        "order_id": open_order_id,
        "event_type": "COMPLETED",
        "event_ts": "2026-11-22T23:55:00Z",
        "updated_at": "2026-11-23T01:00:00Z",
        "team_id": team_id,
        "region_id": region_id,
        "priority": priority,
        "resolution_code": "FIXED_FIRST_VISIT",
    }
    ing2, sync2 = ingestor.ingest_and_sync([late_completion], batch_id="dbt_late_inc")
    assert ing2.inserted_count == 1
    assert sync2.rows_synced == 1

    inc_res = _run_dbt(
        [
            "build",
            "--project-dir",
            str(REPO_ROOT / "dbt"),
            "--profiles-dir",
            str(REPO_ROOT / "dbt"),
            "--target-path",
            str(target_path),
        ],
        env=env,
    )
    assert inc_res.returncode == 0, f"Incremental dbt build failed:\n{inc_res.stdout}\n{inc_res.stderr}"

    with duckdb.connect(str(duckdb_path), read_only=True) as conn:
        total_after = conn.execute("SELECT COUNT(*) FROM fct_service_order_sla").fetchone()[0]
        assert total_after == 1000
        updated_row = conn.execute(
            """
            SELECT is_closed, terminal_event_type, sla_status
            FROM fct_service_order_sla
            WHERE order_id = ?
            """,
            [open_order_id],
        ).fetchone()
        assert updated_row is not None
        assert updated_row[0] is True
        assert updated_row[1] == "COMPLETED"
