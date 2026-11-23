"""Idempotent alert dispatcher with dry-run default, rate ceiling, and human gate."""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ops_sla_telemetry.dispatcher.adapters import DispatchAdapter, DryRunLogAdapter
from ops_sla_telemetry.dispatcher.runbook import draft_incident_runbook
from ops_sla_telemetry.dispatcher.severity import SEVERITY_RANK, AlertCandidate

_LEDGER_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS dispatched_incidents (
    incident_key TEXT PRIMARY KEY,
    severity TEXT NOT NULL,
    adapter_names TEXT NOT NULL,
    dispatched_at_utc TEXT NOT NULL,
    status TEXT NOT NULL
);
"""


class HumanConfirmationError(PermissionError):
    """Raised when live external execution is requested without explicit confirmation."""


@dataclass(frozen=True)
class DispatchItemResult:
    """Outcome for a single evaluated alert candidate."""

    incident_key: str
    severity: str
    runbook_draft: str
    adapter_results: list[dict[str, Any]]


@dataclass(frozen=True)
class DispatchSummary:
    """Aggregate dispatch execution summary."""

    dry_run: bool
    received_count: int
    dispatched_count: int
    suppressed_duplicate_count: int
    capped_count: int
    items: list[DispatchItemResult]


class DispatchLedger:
    """SQLite WAL ledger storing confirmed dispatches for cross-run idempotency."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(self._connect()) as conn:
            conn.executescript(_LEDGER_SCHEMA_SQL)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def contains(self, incident_key: str) -> bool:
        """Return True if incident_key was already dispatched in a confirmed run."""
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM dispatched_incidents WHERE incident_key = ?",
                (incident_key,),
            ).fetchone()
            return row is not None

    def record(
        self,
        *,
        incident_key: str,
        severity: str,
        adapter_names: Sequence[str],
        status: str = "DISPATCHED",
    ) -> None:
        """Persist confirmed incident dispatch timestamp and adapters."""
        now_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with contextlib.closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO dispatched_incidents (
                        incident_key, severity, adapter_names, dispatched_at_utc, status
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (incident_key, severity, ",".join(adapter_names), now_utc, status),
                )

    def list_dispatched(self) -> list[dict[str, Any]]:
        """Return all persisted incident dispatch records ordered chronologically."""
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM dispatched_incidents ORDER BY dispatched_at_utc ASC"
            ).fetchall()
            return [dict(row) for row in rows]


class AlertDispatcher:
    """Idempotent SRE alert dispatcher defaulting strictly to dry-run mode."""

    def __init__(
        self,
        ledger_db_path: Path,
        *,
        adapters: Sequence[DispatchAdapter] | None = None,
        max_alerts_per_run: int = 10,
    ) -> None:
        if max_alerts_per_run < 1:
            raise ValueError(f"max_alerts_per_run must be >= 1, got {max_alerts_per_run}.")
        self.ledger = DispatchLedger(ledger_db_path)
        self.adapters: list[DispatchAdapter] = (
            list(adapters) if adapters is not None else [DryRunLogAdapter()]
        )
        self.max_alerts_per_run = max_alerts_per_run

    def dispatch(
        self,
        alerts: Iterable[AlertCandidate],
        *,
        execute: bool = False,
        confirm: bool = False,
    ) -> DispatchSummary:
        """Evaluate, deduplicate, cap, draft runbooks, and dispatch or preview alerts."""
        if execute and not confirm:
            raise HumanConfirmationError(
                "Refusing external write execution without explicit human confirmation "
                "(--execute --confirm)."
            )

        dry_run = not execute
        raw_alerts = list(alerts)
        actionable = [alert for alert in raw_alerts if alert.severity != "OK"]
        actionable.sort(
            key=lambda item: (SEVERITY_RANK.get(item.severity, 99), item.incident_key)
        )

        seen_in_batch: set[str] = set()
        suppressed_duplicate_count = 0
        capped_count = 0
        items: list[DispatchItemResult] = []

        for alert in actionable:
            if alert.incident_key in seen_in_batch or self.ledger.contains(alert.incident_key):
                suppressed_duplicate_count += 1
                continue
            seen_in_batch.add(alert.incident_key)

            if len(items) >= self.max_alerts_per_run:
                capped_count += 1
                continue

            runbook_md = draft_incident_runbook(alert)
            adapter_results = [
                adapter.send(alert, runbook_md, dry_run=dry_run)
                for adapter in self.adapters
            ]

            if not dry_run:
                self.ledger.record(
                    incident_key=alert.incident_key,
                    severity=alert.severity,
                    adapter_names=[adapter.name for adapter in self.adapters],
                )

            items.append(
                DispatchItemResult(
                    incident_key=alert.incident_key,
                    severity=alert.severity,
                    runbook_draft=runbook_md,
                    adapter_results=adapter_results,
                )
            )

        return DispatchSummary(
            dry_run=dry_run,
            received_count=len(raw_alerts),
            dispatched_count=len(items),
            suppressed_duplicate_count=suppressed_duplicate_count,
            capped_count=capped_count,
            items=items,
        )
