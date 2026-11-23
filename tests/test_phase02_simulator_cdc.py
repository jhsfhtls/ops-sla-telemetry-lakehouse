"""Phase 02 tests: 50k simulator determinism, dual SLA calendar, ODCS quarantine, and CDC."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from ops_sla_telemetry.config import LakehouseSettings
from ops_sla_telemetry.ingestion import CdcEventIngestor, EventContractValidator
from ops_sla_telemetry.simulator import FieldOrderSimulator
from ops_sla_telemetry.sla import (
    PRIORITY_CALENDAR,
    SLA_TARGET_MINUTES,
    compute_active_elapsed_minutes,
    compute_calendar_minutes,
    load_holidays,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_simulator_determinism_by_seed() -> None:
    """Identical seeds yield byte-identical events and payload SHA-256; different seeds diverge."""
    sim_a = FieldOrderSimulator(seed=42, order_count=250)
    sim_b = FieldOrderSimulator(seed=42, order_count=250)
    sim_c = FieldOrderSimulator(seed=99, order_count=250)

    events_a = sim_a.generate_events()
    events_b = sim_b.generate_events()
    events_c = sim_c.generate_events()

    assert events_a == events_b
    hash_a = FieldOrderSimulator.compute_payload_sha256(events_a)
    hash_b = FieldOrderSimulator.compute_payload_sha256(events_b)
    hash_c = FieldOrderSimulator.compute_payload_sha256(events_c)

    assert hash_a == hash_b
    assert hash_a != hash_c


def test_dual_sla_calendar_and_hold_exclusion() -> None:
    """Verify 24x7, business hours (Manaus), national holidays, and ON_HOLD subtraction."""
    holidays = load_holidays(REPO_ROOT / "data" / "reference" / "br_national_holidays.csv")
    assert datetime(2026, 11, 20).date() in holidays

    # 2026-11-19 (Thu) 17:00 Manaus (21:00 UTC) -> 2026-11-23 (Mon) 10:00 Manaus (14:00 UTC)
    # 2026-11-20 is a Brazilian national holiday; Nov 21-22 are Sat/Sun.
    # Expected business hours: 1h on Thu + 2h on Mon = 180 minutes.
    start_utc = datetime(2026, 11, 19, 21, 0, tzinfo=UTC)
    end_utc = datetime(2026, 11, 23, 14, 0, tzinfo=UTC)

    mins_24x7 = compute_calendar_minutes(start_utc, end_utc, "24x7", holidays=holidays)
    assert mins_24x7 == pytest.approx(89 * 60.0)

    mins_biz = compute_calendar_minutes(start_utc, end_utc, "BUSINESS_HOURS", holidays=holidays)
    assert mins_biz == pytest.approx(180.0)

    # Subtract a 45-minute overlapping hold inside Monday 08:30-09:15 Manaus (12:30-13:15 UTC)
    holds = [
        (datetime(2026, 11, 23, 12, 30, tzinfo=UTC), datetime(2026, 11, 23, 13, 0, tzinfo=UTC)),
        (datetime(2026, 11, 23, 12, 45, tzinfo=UTC), datetime(2026, 11, 23, 13, 15, tzinfo=UTC)),
    ]
    active_biz = compute_active_elapsed_minutes(
        start_utc, end_utc, holds, "BUSINESS_HOURS", holidays=holidays
    )
    assert active_biz == pytest.approx(135.0)


def test_odcs_contract_quarantine_and_sentinel_normalization(tmp_path: Path) -> None:
    """Verify ODCS v3.1 quarantines invalid events while normalizing optional sentinels."""
    settings = LakehouseSettings(repo_root=REPO_ROOT, data_dir=tmp_path / "data")
    ingestor = CdcEventIngestor(settings=settings)

    batch = [
        {
            "event_id": "EVT-OK-01",
            "order_id": "ORD-TEST-001",
            "event_type": "COMPLETED",
            "event_ts": "2026-11-22T15:00:00Z",
            "updated_at": "2026-11-22T15:00:05Z",
            "team_id": "TEAM-01",
            "region_id": "REG-NORTE",
            "priority": "P1",
            "hold_reason": "N/A",
            "resolution_code": "FIXED_FIRST_VISIT",
        },
        {
            "event_id": "EVT-BAD-TYPE",
            "order_id": "ORD-TEST-002",
            "event_type": "UNKNOWN_STATE",
            "event_ts": "2026-11-22T15:00:00Z",
            "updated_at": "2026-11-22T15:00:05Z",
            "team_id": "TEAM-01",
            "region_id": "REG-NORTE",
            "priority": "P1",
        },
        {
            "event_id": "EVT-BAD-TS",
            "order_id": "ORD-TEST-003",
            "event_type": "CREATED",
            "event_ts": "not-a-timestamp",
            "updated_at": "2026-11-22T15:00:05Z",
            "team_id": "TEAM-02",
            "region_id": "REG-NORTE",
            "priority": "P2",
        },
        {
            "event_id": "EVT-MISSING-ORDER",
            "order_id": "",
            "event_type": "CREATED",
            "event_ts": "2026-11-22T15:00:00Z",
            "updated_at": "2026-11-22T15:00:05Z",
            "team_id": "TEAM-02",
            "region_id": "REG-NORTE",
            "priority": "P2",
        },
        {
            "event_id": "EVT-BAD-PRIO",
            "order_id": "ORD-TEST-004",
            "event_type": "CREATED",
            "event_ts": "2026-11-22T15:00:00Z",
            "updated_at": "2026-11-22T15:00:05Z",
            "team_id": "TEAM-02",
            "region_id": "REG-NORTE",
            "priority": "P9",
        },
    ]

    validator = EventContractValidator()
    valid_preview, quarantined_preview = validator.validate_batch(batch)
    assert len(valid_preview) == 1
    assert valid_preview[0]["hold_reason"] is None
    assert len(quarantined_preview) == 4

    ingest_report, sync_report = ingestor.ingest_and_sync(batch, batch_id="batch_quarantine_test")
    assert ingest_report.received_count == 5
    assert ingest_report.valid_count == 1
    assert ingest_report.inserted_count == 1
    assert ingest_report.quarantined_count == 4
    assert ingest_report.quarantine_path is not None
    assert ingest_report.quarantine_path.is_file()

    quarantine_lines = [
        json.loads(line)
        for line in ingest_report.quarantine_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(quarantine_lines) == 4
    reasons = {item["rejection_reason"] for item in quarantine_lines}
    assert any(r.startswith("invalid_event_type:") for r in reasons)
    assert "malformed_timestamp_event_ts" in reasons
    assert "missing_required_order_id" in reasons
    assert any(r.startswith("invalid_priority:") for r in reasons)

    assert sync_report.rows_synced == 1
    assert sync_report.parquet_path is not None
    with duckdb.connect() as conn:
        row = conn.execute(
            "SELECT event_id, hold_reason, resolution_code FROM read_parquet(?)",
            [str(sync_report.parquet_path)],
        ).fetchone()
    assert row == ("EVT-OK-01", None, "FIXED_FIRST_VISIT")


def test_cdc_idempotency_and_late_arriving_watermark(tmp_path: Path) -> None:
    """Verify replay idempotency and capture of late-arriving older event_ts via ingest_seq."""
    settings = LakehouseSettings(repo_root=REPO_ROOT, data_dir=tmp_path / "data")
    ingestor = CdcEventIngestor(settings=settings)

    sim = FieldOrderSimulator(seed=7, order_count=10)
    batch1 = sim.generate_events()

    rep1, sync1 = ingestor.ingest_and_sync(batch1, batch_id="batch_1")
    assert rep1.inserted_count == len(batch1)
    assert rep1.duplicates_skipped == 0
    assert sync1.rows_synced == len(batch1)
    assert sync1.parquet_path is not None and sync1.parquet_path.is_file()

    # Re-ingest identical batch1 -> zero inserts, no-op Silver sync
    rep1_replay, sync1_replay = ingestor.ingest_and_sync(batch1, batch_id="batch_1_replay")
    assert rep1_replay.inserted_count == 0
    assert rep1_replay.duplicates_skipped == len(batch1)
    assert sync1_replay.rows_synced == 0
    assert sync1_replay.parquet_path is None

    # Late-arriving event with older event_ts (2025-01-15) must still sync via ingest_seq
    late_event = {
        "event_id": "EVT-LATE-0001",
        "order_id": "ORD-2026-000001",
        "event_type": "COMPLETED",
        "event_ts": "2025-01-15T14:30:00Z",
        "updated_at": "2026-11-23T00:05:00Z",
        "team_id": "TEAM-01",
        "region_id": "REG-NORTE",
        "priority": "P2",
        "resolution_code": "FIXED_FIRST_VISIT",
    }
    rep2, sync2 = ingestor.ingest_and_sync([late_event], batch_id="batch_late")
    assert rep2.inserted_count == 1
    assert sync2.rows_synced == 1
    assert sync2.from_seq > sync1.to_seq
    assert sync2.parquet_path is not None and sync2.parquet_path.is_file()

    with duckdb.connect() as conn:
        total_silver_rows = conn.execute(
            "SELECT COUNT(*) FROM read_parquet(?)",
            [str(settings.silver_dir / "*.parquet")],
        ).fetchone()[0]
        late_found = conn.execute(
            "SELECT event_id FROM read_parquet(?) WHERE event_id = 'EVT-LATE-0001'",
            [str(settings.silver_dir / "*.parquet")],
        ).fetchone()
    assert total_silver_rows == len(batch1) + 1
    assert late_found == ("EVT-LATE-0001",)


def test_50k_orders_scale_sla_and_parquet_performance(tmp_path: Path) -> None:
    """Generate 50,000 orders, ingest Bronze WAL, sync Silver ZSTD Parquet under 30s."""
    settings = LakehouseSettings(repo_root=REPO_ROOT, data_dir=tmp_path / "data")
    ingestor = CdcEventIngestor(settings=settings)
    holidays = load_holidays(REPO_ROOT / "data" / "reference" / "br_national_holidays.csv")

    t0 = time.perf_counter()
    events = FieldOrderSimulator(seed=42, order_count=50_000).generate_events()
    ingest_report, sync_report = ingestor.ingest_and_sync(events, batch_id="scale_50k_seed42")
    elapsed_s = time.perf_counter() - t0

    assert elapsed_s < 30.0, f"50k pipeline exceeded 30s budget: {elapsed_s:.2f}s"
    assert len(events) > 200_000
    assert ingest_report.inserted_count == len(events)
    assert ingest_report.quarantined_count == 0
    assert sync_report.rows_synced == len(events)
    assert sync_report.parquet_path is not None

    with duckdb.connect() as conn:
        counts = conn.execute(
            """
            SELECT
                COUNT(*) AS total_events,
                COUNT(DISTINCT order_id) AS distinct_orders,
                COUNT(*) FILTER (WHERE event_type = 'COMPLETED') AS completed_orders,
                COUNT(*) FILTER (WHERE event_type = 'SLA_BREACHED') AS breached_orders
            FROM read_parquet(?)
            """,
            [str(sync_report.parquet_path)],
        ).fetchone()

    total_events, distinct_orders, completed_orders, breached_orders = counts
    assert total_events == len(events)
    assert distinct_orders == 50_000
    closed_orders = completed_orders + breached_orders
    assert closed_orders > 49_500
    sla_attainment = completed_orders / closed_orders
    assert 0.985 <= sla_attainment <= 0.995

    # Spot-check calendar math consistency across first 500 closed orders
    by_order: dict[str, list[dict[str, str]]] = {}
    for ev in events:
        if len(by_order) >= 600 and ev["order_id"] not in by_order:
            break
        by_order.setdefault(ev["order_id"], []).append(ev)

    checked_closed = 0
    for order_evs in by_order.values():
        term = order_evs[-1]
        if term["event_type"] not in ("COMPLETED", "SLA_BREACHED"):
            continue
        created_dt = datetime.fromisoformat(order_evs[0]["event_ts"].replace("Z", "+00:00"))
        term_dt = datetime.fromisoformat(term["event_ts"].replace("Z", "+00:00"))
        holds: list[tuple[datetime, datetime]] = []
        hold_start: datetime | None = None
        for ev in order_evs:
            dt = datetime.fromisoformat(ev["event_ts"].replace("Z", "+00:00"))
            if ev["event_type"] == "ON_HOLD":
                hold_start = dt
            elif ev["event_type"] == "IN_EXECUTION" and hold_start is not None:
                holds.append((hold_start, dt))
                hold_start = None
        priority = term["priority"]
        active_min = compute_active_elapsed_minutes(
            created_dt,
            term_dt,
            holds,
            PRIORITY_CALENDAR[priority],
            holidays=holidays,
        )
        if term["event_type"] == "COMPLETED":
            assert active_min <= SLA_TARGET_MINUTES[priority]
        else:
            assert active_min > SLA_TARGET_MINUTES[priority]
        checked_closed += 1

    assert checked_closed >= 450
