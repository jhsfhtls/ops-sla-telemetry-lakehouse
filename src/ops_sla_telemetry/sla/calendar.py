"""Dual SLA calendar engine (24x7 vs Business Hours in America/Manaus)."""

from __future__ import annotations

import csv
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SLA_TARGET_MINUTES: dict[str, int] = {
    "P1": 240,
    "P2": 1440,
    "P3": 4320,
}

PRIORITY_CALENDAR: dict[str, str] = {
    "P1": "24x7",
    "P2": "BUSINESS_HOURS",
    "P3": "BUSINESS_HOURS",
}

BUSINESS_START = time(8, 0, 0)
BUSINESS_END = time(18, 0, 0)

_BUILTIN_HOLIDAY_STRINGS: tuple[str, ...] = (
    "2025-01-01",
    "2025-04-18",
    "2025-04-21",
    "2025-05-01",
    "2025-09-07",
    "2025-10-12",
    "2025-11-02",
    "2025-11-15",
    "2025-11-20",
    "2025-12-25",
    "2026-01-01",
    "2026-04-03",
    "2026-04-21",
    "2026-05-01",
    "2026-09-07",
    "2026-10-12",
    "2026-11-02",
    "2026-11-15",
    "2026-11-20",
    "2026-12-25",
    "2027-01-01",
    "2027-03-26",
    "2027-04-21",
    "2027-05-01",
    "2027-09-07",
    "2027-10-12",
    "2027-11-02",
    "2027-11-15",
    "2027-11-20",
    "2027-12-25",
)
BUILTIN_HOLIDAYS: frozenset[date] = frozenset(
    date.fromisoformat(value) for value in _BUILTIN_HOLIDAY_STRINGS
)


def _manaus_tz() -> ZoneInfo | timezone:
    try:
        return ZoneInfo("America/Manaus")
    except ZoneInfoNotFoundError:  # pragma: no cover - fallback when tzdata unavailable
        return timezone(timedelta(hours=-4))


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def load_holidays(csv_path: Path | None = None) -> frozenset[date]:
    """Load Brazilian national holidays from CSV reference or built-in defaults."""
    target = csv_path or (
        Path(__file__).resolve().parents[3] / "data" / "reference" / "br_national_holidays.csv"
    )
    if not target.is_file():
        return BUILTIN_HOLIDAYS

    parsed: set[date] = set()
    with target.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw_date = (row.get("date_iso") or "").strip()
            if raw_date:
                parsed.add(date.fromisoformat(raw_date))
    return frozenset(parsed) if parsed else BUILTIN_HOLIDAYS


def compute_calendar_minutes(
    start_utc: datetime,
    end_utc: datetime,
    calendar_mode: str,
    holidays: AbstractSet[date] | None = None,
) -> float:
    """Compute elapsed SLA minutes between two timestamps under 24x7 or business hours."""
    start = _ensure_utc(start_utc)
    end = _ensure_utc(end_utc)
    if end <= start:
        return 0.0

    mode = calendar_mode.strip().upper()
    if mode == "24X7":
        return (end - start).total_seconds() / 60.0

    if mode != "BUSINESS_HOURS":
        raise ValueError(f"Unsupported SLA calendar mode: {calendar_mode!r}")

    holiday_set = BUILTIN_HOLIDAYS if holidays is None else holidays
    tz = _manaus_tz()
    start_local = start.astimezone(tz)
    end_local = end.astimezone(tz)

    total_seconds = 0.0
    current_day = start_local.date()
    final_day = end_local.date()

    while current_day <= final_day:
        if current_day.weekday() < 5 and current_day not in holiday_set:
            window_start = datetime.combine(current_day, BUSINESS_START, tzinfo=tz)
            window_end = datetime.combine(current_day, BUSINESS_END, tzinfo=tz)
            overlap_start = max(start_local, window_start)
            overlap_end = min(end_local, window_end)
            if overlap_end > overlap_start:
                total_seconds += (overlap_end - overlap_start).total_seconds()
        current_day += timedelta(days=1)

    return total_seconds / 60.0


def _merge_hold_intervals(
    intervals: Sequence[tuple[datetime, datetime]],
    clip_start: datetime,
    clip_end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Normalize, clip, and merge overlapping ON_HOLD intervals."""
    clipped: list[tuple[datetime, datetime]] = []
    for raw_start, raw_end in intervals:
        h_start = max(_ensure_utc(raw_start), clip_start)
        h_end = min(_ensure_utc(raw_end), clip_end)
        if h_end > h_start:
            clipped.append((h_start, h_end))

    if not clipped:
        return []

    clipped.sort(key=lambda pair: pair[0])
    merged: list[tuple[datetime, datetime]] = [clipped[0]]
    for current_start, current_end in clipped[1:]:
        prev_start, prev_end = merged[-1]
        if current_start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, current_end))
        else:
            merged.append((current_start, current_end))
    return merged


def compute_active_elapsed_minutes(
    created_utc: datetime,
    terminal_utc: datetime,
    hold_intervals: Sequence[tuple[datetime, datetime]],
    calendar_mode: str,
    holidays: AbstractSet[date] | None = None,
) -> float:
    """Compute active SLA elapsed minutes excluding merged ON_HOLD pause intervals."""
    start = _ensure_utc(created_utc)
    end = _ensure_utc(terminal_utc)
    if end <= start:
        return 0.0

    gross = compute_calendar_minutes(start, end, calendar_mode, holidays=holidays)
    merged_holds = _merge_hold_intervals(hold_intervals, start, end)
    held = sum(
        compute_calendar_minutes(h_start, h_end, calendar_mode, holidays=holidays)
        for h_start, h_end in merged_holds
    )
    return max(0.0, gross - held)
