"""Deterministic 50,000-order field-service lifecycle simulator."""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

REGIONS: tuple[str, ...] = (
    "REG-NORTE",
    "REG-NORDESTE",
    "REG-CENTRO",
    "REG-SUDESTE",
    "REG-SUL",
)
TEAMS: tuple[str, ...] = tuple(f"TEAM-{idx:02d}" for idx in range(1, 26))
PRIORITY_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("P1", 0.15),
    ("P2", 0.50),
    ("P3", 1.00),
)
HOLD_REASONS: tuple[str, ...] = (
    "CUSTOMER_ACCESS",
    "UTILITY_SHUTOFF",
    "PERMIT_WAIT",
)
RESOLUTION_CODES: tuple[str, ...] = (
    "FIXED_FIRST_VISIT",
    "PART_REPLACED",
    "NETWORK_RESTORED",
)


def _format_utc(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=UTC).isoformat().replace("+00:00", "Z")


class FieldOrderSimulator:
    """Generates deterministic field-service lifecycle events (~50k orders/year)."""

    def __init__(
        self,
        seed: int = 42,
        order_count: int = 50_000,
        reference_utc: datetime | None = None,
    ) -> None:
        if order_count <= 0:
            raise ValueError(f"order_count must be positive, got {order_count}.")
        self.seed = seed
        self.order_count = order_count
        self.reference_utc = (
            reference_utc.astimezone(UTC)
            if reference_utc is not None
            else datetime(2026, 11, 22, 23, 59, 59, tzinfo=UTC)
        )

    @staticmethod
    def _pick_priority(roll: float) -> str:
        for priority, threshold in PRIORITY_WEIGHTS:
            if roll < threshold:
                return priority
        return "P3"

    @staticmethod
    def _duration_seconds(rng: random.Random, priority: str, breached: bool) -> int:
        if priority == "P1":
            minutes = rng.randint(260, 540) if breached else rng.randint(35, 205)
            return minutes * 60
        if priority == "P2":
            if breached:
                return rng.randint(7, 10) * 86_400
            return rng.randint(45, 900) * 60
        if breached:
            return rng.randint(14, 20) * 86_400
        return rng.randint(60, 2600) * 60

    def _build_event(
        self,
        *,
        order_id: str,
        seq: int,
        event_type: str,
        ts_epoch: int,
        update_lag_s: int,
        team_id: str,
        region_id: str,
        priority: str,
        hold_reason: str | None = None,
        resolution_code: str | None = None,
    ) -> dict[str, Any]:
        return {
            "event_id": f"EVT-{order_id}-{seq:02d}",
            "order_id": order_id,
            "event_type": event_type,
            "event_ts": _format_utc(ts_epoch),
            "updated_at": _format_utc(ts_epoch + update_lag_s),
            "team_id": team_id,
            "region_id": region_id,
            "priority": priority,
            "hold_reason": hold_reason,
            "resolution_code": resolution_code,
        }

    def _generate_closed_order(
        self,
        rng: random.Random,
        order_id: str,
        team_id: str,
        region_id: str,
        priority: str,
        ref_epoch: int,
    ) -> list[dict[str, Any]]:
        days_back = rng.randint(22, 360)
        # Anchor between 12:00 and 18:00 UTC (08:00-14:00 America/Manaus)
        day_anchor = ref_epoch - (days_back * 86_400)
        day_midnight = day_anchor - (day_anchor % 86_400)
        created_epoch = day_midnight + rng.randint(12 * 3600, 18 * 3600)

        breached = rng.random() < 0.01
        active_seconds = self._duration_seconds(rng, priority, breached)
        assign_s = max(180, int(active_seconds * 0.12))
        transit_s = max(300, int(active_seconds * 0.22))
        exec_s = max(600, int(active_seconds * 0.35))

        has_hold = rng.random() < 0.15
        hold_s = rng.randint(30, 120) * 60 if has_hold else 0
        hold_reason = rng.choice(HOLD_REASONS) if has_hold else None

        t1 = created_epoch + assign_s
        t2 = t1 + transit_s
        t3 = t2 + exec_s
        events = [
            self._build_event(
                order_id=order_id,
                seq=1,
                event_type="CREATED",
                ts_epoch=created_epoch,
                update_lag_s=rng.randint(0, 12),
                team_id=team_id,
                region_id=region_id,
                priority=priority,
            ),
            self._build_event(
                order_id=order_id,
                seq=2,
                event_type="ASSIGNED",
                ts_epoch=t1,
                update_lag_s=rng.randint(0, 12),
                team_id=team_id,
                region_id=region_id,
                priority=priority,
            ),
            self._build_event(
                order_id=order_id,
                seq=3,
                event_type="IN_TRANSIT",
                ts_epoch=t2,
                update_lag_s=rng.randint(0, 12),
                team_id=team_id,
                region_id=region_id,
                priority=priority,
            ),
            self._build_event(
                order_id=order_id,
                seq=4,
                event_type="IN_EXECUTION",
                ts_epoch=t3,
                update_lag_s=rng.randint(0, 12),
                team_id=team_id,
                region_id=region_id,
                priority=priority,
            ),
        ]

        seq = 5
        last_ts = t3
        if has_hold:
            t_hold = last_ts + max(300, int(active_seconds * 0.10))
            t_resume = t_hold + hold_s
            events.append(
                self._build_event(
                    order_id=order_id,
                    seq=seq,
                    event_type="ON_HOLD",
                    ts_epoch=t_hold,
                    update_lag_s=rng.randint(0, 12),
                    team_id=team_id,
                    region_id=region_id,
                    priority=priority,
                    hold_reason=hold_reason,
                )
            )
            seq += 1
            events.append(
                self._build_event(
                    order_id=order_id,
                    seq=seq,
                    event_type="IN_EXECUTION",
                    ts_epoch=t_resume,
                    update_lag_s=rng.randint(0, 12),
                    team_id=team_id,
                    region_id=region_id,
                    priority=priority,
                )
            )
            seq += 1
            last_ts = t_resume

        terminal_epoch = created_epoch + active_seconds + hold_s
        if terminal_epoch <= last_ts:
            terminal_epoch = last_ts + 300
        terminal_type = "SLA_BREACHED" if breached else "COMPLETED"
        res_code = "SLA_TIMEOUT" if breached else rng.choice(RESOLUTION_CODES)
        events.append(
            self._build_event(
                order_id=order_id,
                seq=seq,
                event_type=terminal_type,
                ts_epoch=terminal_epoch,
                update_lag_s=rng.randint(0, 12),
                team_id=team_id,
                region_id=region_id,
                priority=priority,
                resolution_code=res_code,
            )
        )
        return events

    def _generate_open_order(
        self,
        rng: random.Random,
        order_id: str,
        team_id: str,
        region_id: str,
        priority: str,
        ref_epoch: int,
    ) -> list[dict[str, Any]]:
        age_hours = rng.randint(1, 3) if priority == "P1" else rng.randint(2, 18)
        created_epoch = ref_epoch - (age_hours * 3600)
        stages = ("CREATED", "ASSIGNED", "IN_TRANSIT", "IN_EXECUTION")
        depth = rng.randint(2, 4)
        step_seconds = max(300, (age_hours * 3600) // (depth + 1))

        events: list[dict[str, Any]] = []
        for idx in range(depth):
            events.append(
                self._build_event(
                    order_id=order_id,
                    seq=idx + 1,
                    event_type=stages[idx],
                    ts_epoch=created_epoch + (idx * step_seconds),
                    update_lag_s=rng.randint(0, 10),
                    team_id=team_id,
                    region_id=region_id,
                    priority=priority,
                )
            )
        return events

    def generate_events(self) -> list[dict[str, Any]]:
        """Generate deterministic lifecycle events for all synthetic orders."""
        rng = random.Random(self.seed)
        ref_epoch = int(self.reference_utc.timestamp())
        events: list[dict[str, Any]] = []

        for idx in range(1, self.order_count + 1):
            order_id = f"ORD-2026-{idx:06d}"
            team_idx = rng.randrange(len(TEAMS))
            team_id = TEAMS[team_idx]
            region_id = REGIONS[team_idx // 5]
            priority = self._pick_priority(rng.random())

            if rng.random() < 0.004:
                order_events = self._generate_open_order(
                    rng, order_id, team_id, region_id, priority, ref_epoch
                )
            else:
                order_events = self._generate_closed_order(
                    rng, order_id, team_id, region_id, priority, ref_epoch
                )
            events.extend(order_events)

        return events

    @staticmethod
    def compute_payload_sha256(events: Sequence[dict[str, Any]]) -> str:
        """Compute canonical SHA-256 digest over an ordered event sequence."""
        encoded = json.dumps(list(events), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
