"""ODCS v3.1 event contract validator and sentinel normalizer."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

REQUIRED_FIELDS: tuple[str, ...] = (
    "event_id",
    "order_id",
    "event_type",
    "event_ts",
    "updated_at",
    "team_id",
    "region_id",
    "priority",
)
VALID_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "CREATED",
        "ASSIGNED",
        "IN_TRANSIT",
        "IN_EXECUTION",
        "ON_HOLD",
        "COMPLETED",
        "SLA_BREACHED",
    }
)
VALID_PRIORITIES: frozenset[str] = frozenset({"P1", "P2", "P3"})
SENTINEL_STRINGS: frozenset[str] = frozenset(
    {"", "N/A", "NA", "-1", "99", "999", "SEM INFORMACAO", "NULL", "NONE"}
)


def normalize_optional_sentinel(value: Any) -> str | None:
    """Convert sentinel placeholders on optional string attributes into None."""
    if value is None:
        return None
    text = value.strip() if isinstance(value, str) else str(value).strip()
    if not text or (len(text) <= 14 and text.upper() in SENTINEL_STRINGS):
        return None
    return text


def _normalize_utc_timestamp(raw_value: Any, field_name: str) -> str:
    if not isinstance(raw_value, str):
        raise ValueError(f"missing_or_invalid_{field_name}")
    cleaned = raw_value.strip()
    if not cleaned:
        raise ValueError(f"missing_or_invalid_{field_name}")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError(f"malformed_timestamp_{field_name}") from exc
    if len(cleaned) == 20 and cleaned[10] == "T" and cleaned.endswith("Z"):
        return cleaned
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


class EventContractValidator:
    """Validates field-service event records against the ODCS v3.1 event contract."""

    def validate_record(self, raw_event: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and normalize a single event dictionary or raise ValueError."""
        if not isinstance(raw_event, Mapping):
            raise ValueError("record_not_mapping")

        normalized: dict[str, Any] = {}
        for field_name in REQUIRED_FIELDS:
            val = raw_event.get(field_name)
            if val is None:
                raise ValueError(f"missing_required_{field_name}")
            text = val.strip() if isinstance(val, str) else str(val).strip()
            if not text:
                raise ValueError(f"missing_required_{field_name}")
            if len(text) <= 14 and text.upper() in SENTINEL_STRINGS:
                raise ValueError(f"sentinel_in_required_{field_name}")
            normalized[field_name] = text

        if normalized["event_type"] not in VALID_EVENT_TYPES:
            raise ValueError(f"invalid_event_type:{normalized['event_type']}")
        if normalized["priority"] not in VALID_PRIORITIES:
            raise ValueError(f"invalid_priority:{normalized['priority']}")

        normalized["event_ts"] = _normalize_utc_timestamp(normalized["event_ts"], "event_ts")
        normalized["updated_at"] = _normalize_utc_timestamp(
            normalized["updated_at"], "updated_at"
        )
        normalized["hold_reason"] = normalize_optional_sentinel(raw_event.get("hold_reason"))
        normalized["resolution_code"] = normalize_optional_sentinel(
            raw_event.get("resolution_code")
        )
        return normalized

    def validate_batch(
        self, events: Iterable[Mapping[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split a batch into contract-compliant events and quarantined records."""
        valid: list[dict[str, Any]] = []
        quarantined: list[dict[str, Any]] = []

        for raw in events:
            try:
                valid.append(self.validate_record(raw))
            except ValueError as exc:
                quarantined.append(
                    {
                        "rejection_reason": str(exc),
                        "raw_event": dict(raw) if isinstance(raw, Mapping) else {"raw": repr(raw)},
                    }
                )
        return valid, quarantined
