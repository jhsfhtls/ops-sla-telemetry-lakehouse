"""SLA calculation utilities aligned with ADR-002."""

from ops_sla_telemetry.sla.calendar import (
    PRIORITY_CALENDAR,
    SLA_TARGET_MINUTES,
    compute_active_elapsed_minutes,
    compute_calendar_minutes,
    load_holidays,
)

__all__ = [
    "PRIORITY_CALENDAR",
    "SLA_TARGET_MINUTES",
    "compute_active_elapsed_minutes",
    "compute_calendar_minutes",
    "load_holidays",
]
