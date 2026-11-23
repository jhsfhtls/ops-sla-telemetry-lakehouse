"""Phase 07 chaos and property-based resilience test suite."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from ops_sla_telemetry import OpsSlaTelemetry
from ops_sla_telemetry.config import LakehouseSettings
from ops_sla_telemetry.ingestion import CdcEventIngestor
from ops_sla_telemetry.simulator import FieldOrderSimulator
from ops_sla_telemetry.sla import (
    compute_active_elapsed_minutes,
    compute_calendar_minutes,
    load_holidays,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

_ORDER_COMPARE_SQL = """
SELECT
    order_id,
    team_id,
    region_id,
    priority,
    calendar_mode,
    sla_target_minutes,
    created_ts,
    terminal_ts,
    terminal_event_type,
    current_state,
    order_date,
    ROUND(gross_wall_minutes, 4) AS gross_wall_minutes,
    ROUND(on_hold_minutes, 4) AS on_hold_minutes,
    ROUND(active_elapsed_minutes, 4) AS active_elapsed_minutes,
    is_closed,
    sla_status,
    remaining_sla_hours,
    resolution_hours
FROM fct_service_order_sla
ORDER BY order_id ASC
"""

_MART_COMPARE_SQL = """
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
ORDER BY cohort_key ASC
"""


def _build_sdk(tmp_path: Path, name: str) -> OpsSlaTelemetry:
    root = tmp_path / name
    settings = LakehouseSettings(repo_root=REPO_ROOT, data_dir=root / "data")
    return OpsSlaTelemetry(
        settings=settings,
        duckdb_path=root / "data" / "gold" / "ops_sla_telemetry.duckdb",
        ledger_db_path=root / "data" / "bronze" / "dispatch_ledger.sqlite",
        dbt_target_path=root / "dbt_target",
    )


def test_chaos_out_of_order_and_poisoned_batches_converge_to_baseline(tmp_path: Path) -> None:
    """Shuffled multi-batch ingestion + duplicates + poisoned payloads match ordered baseline."""
    events = FieldOrderSimulator(seed=42, order_count=250).generate_events()

    # Pipeline A: ordered baseline
    sdk_a = _build_sdk(tmp_path, "baseline")
    ingestor_a = CdcEventIngestor(settings=sdk_a.settings)
    ingestor_a.ingest_and_sync(events, batch_id="baseline_full")
    sdk_a._run_dbt_build()

    # Pipeline B: shuffled across 3 asynchronous out-of-order micro-batches
    sdk_b = _build_sdk(tmp_path, "chaos")
    ingestor_b = CdcEventIngestor(settings=sdk_b.settings)
    shuffled = list(events)
    random.Random(1337).shuffle(shuffled)
    chunk = len(shuffled) // 3
    slices = [shuffled[:chunk], shuffled[chunk : 2 * chunk], shuffled[2 * chunk :]]

    for idx, batch in enumerate(slices, start=1):
        ingestor_b.ingest_and_sync(batch, batch_id=f"chaos_slice_{idx}")
        sdk_b._run_dbt_build()

    with duckdb.connect(str(sdk_a.duckdb_path), read_only=True) as conn_a:
        orders_a = conn_a.execute(_ORDER_COMPARE_SQL).fetchall()
        marts_a = conn_a.execute(_MART_COMPARE_SQL).fetchall()

    with duckdb.connect(str(sdk_b.duckdb_path), read_only=True) as conn_b:
        orders_b = conn_b.execute(_ORDER_COMPARE_SQL).fetchall()
        marts_b = conn_b.execute(_MART_COMPARE_SQL).fetchall()

    assert orders_a == orders_b
    assert marts_a == marts_b

    # Inject duplicate replay + 25 poisoned payloads into Pipeline B
    poisoned: list[Any] = [
        {
            "event_id": f"EVT-POISON-TYPE-{i}",
            "order_id": "ORD-POISON",
            "event_type": "CORRUPTED_STATE",
            "event_ts": "2026-11-22T12:00:00Z",
            "updated_at": "2026-11-22T12:00:05Z",
            "team_id": "TEAM-01",
            "region_id": "REG-NORTE",
            "priority": "P1",
        }
        for i in range(5)
    ] + [
        {
            "event_id": f"EVT-POISON-TS-{i}",
            "order_id": "ORD-POISON",
            "event_type": "CREATED",
            "event_ts": "invalid-iso-ts",
            "updated_at": "2026-11-22T12:00:05Z",
            "team_id": "TEAM-01",
            "region_id": "REG-NORTE",
            "priority": "P1",
        }
        for i in range(5)
    ] + [
        {
            "event_id": f"EVT-POISON-ORDER-{i}",
            "order_id": "",
            "event_type": "CREATED",
            "event_ts": "2026-11-22T12:00:00Z",
            "updated_at": "2026-11-22T12:00:05Z",
            "team_id": "TEAM-01",
            "region_id": "REG-NORTE",
            "priority": "P1",
        }
        for i in range(5)
    ] + [
        {
            "event_id": f"EVT-POISON-PRIO-{i}",
            "order_id": "ORD-POISON",
            "event_type": "CREATED",
            "event_ts": "2026-11-22T12:00:00Z",
            "updated_at": "2026-11-22T12:00:05Z",
            "team_id": "TEAM-01",
            "region_id": "REG-NORTE",
            "priority": "P99",
        }
        for i in range(5)
    ] + [
        "not-a-mapping-record"
        for _ in range(5)
    ]

    chaos_replay = shuffled[:100] + poisoned
    ing_rep, sync_rep = ingestor_b.ingest_and_sync(chaos_replay, batch_id="chaos_poison_replay")
    assert ing_rep.quarantined_count == 25
    assert ing_rep.inserted_count == 0
    assert ing_rep.duplicates_skipped == 100
    assert sync_rep.rows_synced == 0
    assert ing_rep.quarantine_path is not None
    assert len(ing_rep.quarantine_path.read_text(encoding="utf-8").splitlines()) == 25

    sdk_b._run_dbt_build()
    with duckdb.connect(str(sdk_b.duckdb_path), read_only=True) as conn_b:
        orders_after = conn_b.execute(_ORDER_COMPARE_SQL).fetchall()
        marts_after = conn_b.execute(_MART_COMPARE_SQL).fetchall()
    assert orders_after == orders_a
    assert marts_after == marts_a


def test_chaos_late_arriving_hold_reclassifies_breached_to_met(tmp_path: Path) -> None:
    """Late-arriving ON_HOLD + resume events recompute active SLA from BREACHED to MET."""
    sdk = _build_sdk(tmp_path, "late_hold")
    ingestor = CdcEventIngestor(settings=sdk.settings)

    # P1 (24x7, 240 min SLA): created 10:00Z, completed 14:30Z -> 270 min gross -> SLA_BREACHED
    initial_events = [
        {
            "event_id": "EVT-HOLD-01",
            "order_id": "ORD-CHAOS-HOLD-001",
            "event_type": "CREATED",
            "event_ts": "2026-11-22T10:00:00Z",
            "updated_at": "2026-11-22T10:00:01Z",
            "team_id": "TEAM-01",
            "region_id": "REG-NORTE",
            "priority": "P1",
        },
        {
            "event_id": "EVT-HOLD-02",
            "order_id": "ORD-CHAOS-HOLD-001",
            "event_type": "IN_EXECUTION",
            "event_ts": "2026-11-22T10:30:00Z",
            "updated_at": "2026-11-22T10:30:01Z",
            "team_id": "TEAM-01",
            "region_id": "REG-NORTE",
            "priority": "P1",
        },
        {
            "event_id": "EVT-HOLD-05",
            "order_id": "ORD-CHAOS-HOLD-001",
            "event_type": "COMPLETED",
            "event_ts": "2026-11-22T14:30:00Z",
            "updated_at": "2026-11-22T14:30:01Z",
            "team_id": "TEAM-01",
            "region_id": "REG-NORTE",
            "priority": "P1",
            "resolution_code": "FIXED_FIRST_VISIT",
        },
    ]
    ingestor.ingest_and_sync(initial_events, batch_id="hold_batch_1")
    sdk._run_dbt_build()

    with duckdb.connect(str(sdk.duckdb_path), read_only=True) as conn:
        before = conn.execute(
            "SELECT on_hold_minutes, active_elapsed_minutes, sla_status FROM fct_service_order_sla"
        ).fetchone()
    assert before == (0.0, 270.0, "SLA_BREACHED")

    # Late-arriving offline sync reveals 60-minute ON_HOLD between 11:00Z and 12:00Z
    late_hold_events = [
        {
            "event_id": "EVT-HOLD-03",
            "order_id": "ORD-CHAOS-HOLD-001",
            "event_type": "ON_HOLD",
            "event_ts": "2026-11-22T11:00:00Z",
            "updated_at": "2026-11-22T16:00:00Z",
            "team_id": "TEAM-01",
            "region_id": "REG-NORTE",
            "priority": "P1",
            "hold_reason": "CUSTOMER_ACCESS",
        },
        {
            "event_id": "EVT-HOLD-04",
            "order_id": "ORD-CHAOS-HOLD-001",
            "event_type": "IN_EXECUTION",
            "event_ts": "2026-11-22T12:00:00Z",
            "updated_at": "2026-11-22T16:00:01Z",
            "team_id": "TEAM-01",
            "region_id": "REG-NORTE",
            "priority": "P1",
        },
    ]
    ingestor.ingest_and_sync(late_hold_events, batch_id="hold_batch_2")
    sdk._run_dbt_build()

    with duckdb.connect(str(sdk.duckdb_path), read_only=True) as conn:
        count_after = conn.execute("SELECT COUNT(*) FROM fct_service_order_sla").fetchone()[0]
        after = conn.execute(
            "SELECT on_hold_minutes, active_elapsed_minutes, sla_status FROM fct_service_order_sla"
        ).fetchone()
    assert count_after == 1
    assert after == (60.0, 210.0, "SLA_MET")


def test_chaos_replay_storm_generates_zero_duplicate_alerts(tmp_path: Path) -> None:
    """Repeated re-ingestions and dbt builds never dispatch duplicate alerts."""
    sdk = _build_sdk(tmp_path, "replay_storm")
    events = FieldOrderSimulator(seed=42, order_count=300).generate_events()
    for idx in range(1, 4):
        oid = f"ORD-STORM-BREACH-{idx:02d}"
        events.extend(
            [
                {
                    "event_id": f"EVT-{oid}-01",
                    "order_id": oid,
                    "event_type": "CREATED",
                    "event_ts": "2026-11-22T18:00:00Z",
                    "updated_at": "2026-11-22T18:00:01Z",
                    "team_id": "TEAM-01",
                    "region_id": "REG-NORTE",
                    "priority": "P1",
                },
                {
                    "event_id": f"EVT-{oid}-02",
                    "order_id": oid,
                    "event_type": "SLA_BREACHED",
                    "event_ts": "2026-11-22T23:30:00Z",
                    "updated_at": "2026-11-22T23:30:01Z",
                    "team_id": "TEAM-01",
                    "region_id": "REG-NORTE",
                    "priority": "P1",
                    "resolution_code": "SLA_TIMEOUT",
                },
            ]
        )
    ingestor = CdcEventIngestor(settings=sdk.settings)
    ingestor.ingest_and_sync(events, batch_id="storm_initial")
    sdk._run_dbt_build()

    calls_log = tmp_path / "ticktick_calls.jsonl"
    mock_ticktick = tmp_path / "mock_ticktick.py"
    mock_ticktick.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                f"with Path({str(calls_log)!r}).open('a', encoding='utf-8') as f:",
                "    f.write(json.dumps(sys.argv[1:]) + '\\n')",
                "print(json.dumps({'status': 'ok'}))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    first = sdk.sync_ticktick(
        execute=True,
        confirm=True,
        max_alerts=25,
        ticktick_script=mock_ticktick,
        window_bucket="storm-bucket",
    )
    assert first["dry_run"] is False
    assert first["dispatched_count"] > 0
    initial_calls = len(calls_log.read_text(encoding="utf-8").splitlines())
    assert initial_calls == first["dispatched_count"]

    # Trigger 3 replay storms with duplicate events + incremental dbt builds
    for storm_idx in range(1, 4):
        ingestor.ingest_and_sync(events, batch_id=f"storm_replay_{storm_idx}")
        sdk._run_dbt_build()
        replay = sdk.sync_ticktick(
            execute=True,
            confirm=True,
            max_alerts=25,
            ticktick_script=mock_ticktick,
            window_bucket="storm-bucket",
        )
        assert replay["dispatched_count"] == 0
        assert replay["suppressed_duplicate_count"] == first["dispatched_count"]

    final_calls = len(calls_log.read_text(encoding="utf-8").splitlines())
    assert final_calls == initial_calls


def test_property_dual_calendar_invariants_across_200_random_spans() -> None:
    """Verify mathematical invariants across 200 randomized spans and hold intervals."""
    holidays = load_holidays(REPO_ROOT / "data" / "reference" / "br_national_holidays.csv")
    rng = random.Random(2026)
    base = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)

    for _ in range(200):
        offset_a = rng.randint(0, 30 * 24 * 60)
        delta_ab = rng.randint(0, 5 * 24 * 60)
        delta_bc = rng.randint(0, 5 * 24 * 60)

        ts_a = base + timedelta(minutes=offset_a)
        ts_b = ts_a + timedelta(minutes=delta_ab)
        ts_c = ts_b + timedelta(minutes=delta_bc)

        mins_24x7 = compute_calendar_minutes(ts_a, ts_c, "24x7", holidays=holidays)
        mins_biz_ac = compute_calendar_minutes(ts_a, ts_c, "BUSINESS_HOURS", holidays=holidays)
        mins_biz_ab = compute_calendar_minutes(ts_a, ts_b, "BUSINESS_HOURS", holidays=holidays)
        mins_biz_bc = compute_calendar_minutes(ts_b, ts_c, "BUSINESS_HOURS", holidays=holidays)

        assert 0.0 <= mins_biz_ac <= mins_24x7 + 1e-9
        assert mins_biz_ab + mins_biz_bc == pytest.approx(mins_biz_ac, abs=1e-6)

        total_span = delta_ab + delta_bc
        if total_span > 10:
            h1_start = ts_a + timedelta(minutes=rng.randint(0, total_span // 2))
            h1_end = h1_start + timedelta(minutes=rng.randint(1, total_span // 2))
            h2_start = ts_a + timedelta(minutes=rng.randint(0, total_span // 2))
            h2_end = h2_start + timedelta(minutes=rng.randint(1, total_span // 2))
            holds = [(h1_start, h1_end), (h2_start, h2_end)]

            active_biz = compute_active_elapsed_minutes(
                ts_a, ts_c, holds, "BUSINESS_HOURS", holidays=holidays
            )
            active_biz_dup = compute_active_elapsed_minutes(
                ts_a, ts_c, holds + holds, "BUSINESS_HOURS", holidays=holidays
            )
            assert 0.0 <= active_biz <= mins_biz_ac + 1e-9
            assert active_biz == pytest.approx(active_biz_dup, abs=1e-6)
