"""Append-only Bronze SQLite WAL event store with outbox SHA-256 and CDC watermarks."""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bronze_events (
    ingest_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    order_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_ts TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    team_id TEXT NOT NULL,
    region_id TEXT NOT NULL,
    priority TEXT NOT NULL,
    hold_reason TEXT,
    resolution_code TEXT,
    source_name TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bronze_events_order_id
    ON bronze_events(order_id);

CREATE INDEX IF NOT EXISTS idx_bronze_events_ingested_at
    ON bronze_events(ingested_at);

CREATE TABLE IF NOT EXISTS outbox_manifests (
    batch_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    valid_count INTEGER NOT NULL,
    inserted_count INTEGER NOT NULL,
    duplicates_skipped INTEGER NOT NULL,
    quarantined_count INTEGER NOT NULL,
    payload_sha256 TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cdc_watermarks (
    pipeline_name TEXT PRIMARY KEY,
    last_ingest_seq INTEGER NOT NULL,
    last_ingested_at TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
"""


class BronzeEventStore:
    """Embedded ACID Bronze event store backed by SQLite WAL."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-65536")
        return conn

    def _init_schema(self) -> None:
        with contextlib.closing(self._connect()) as conn:
            conn.executescript(_SCHEMA_SQL)
            conn.commit()

    def append_batch(
        self,
        *,
        batch_id: str,
        source_name: str,
        valid_events: Sequence[dict[str, Any]],
        quarantined_count: int,
        payload_sha256: str,
        ingested_at: str | None = None,
    ) -> tuple[int, int]:
        """Idempotently append validated events and record batch outbox manifest."""
        now_iso = ingested_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
        rows = [
            (
                ev["event_id"],
                ev["order_id"],
                ev["event_type"],
                ev["event_ts"],
                ev["updated_at"],
                ev["team_id"],
                ev["region_id"],
                ev["priority"],
                ev.get("hold_reason"),
                ev.get("resolution_code"),
                source_name,
                batch_id,
                now_iso,
            )
            for ev in valid_events
        ]

        with contextlib.closing(self._connect()) as conn:
            with conn:
                before = conn.total_changes
                if rows:
                    conn.executemany(
                        """
                        INSERT OR IGNORE INTO bronze_events (
                            event_id, order_id, event_type, event_ts, updated_at,
                            team_id, region_id, priority, hold_reason, resolution_code,
                            source_name, batch_id, ingested_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
                inserted_count = conn.total_changes - before
                duplicates_skipped = len(rows) - inserted_count
                conn.execute(
                    """
                    INSERT OR REPLACE INTO outbox_manifests (
                        batch_id, source_name, valid_count, inserted_count,
                        duplicates_skipped, quarantined_count, payload_sha256, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        source_name,
                        len(rows),
                        inserted_count,
                        duplicates_skipped,
                        quarantined_count,
                        payload_sha256,
                        now_iso,
                    ),
                )
        return inserted_count, duplicates_skipped

    def get_watermark(self, pipeline_name: str) -> int:
        """Return persisted CDC high-watermark sequence or 0 if unset."""
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT last_ingest_seq FROM cdc_watermarks WHERE pipeline_name = ?",
                (pipeline_name,),
            ).fetchone()
            return int(row["last_ingest_seq"]) if row is not None else 0

    def fetch_events_since(self, last_ingest_seq: int) -> list[dict[str, Any]]:
        """Read incremental Bronze events strictly above the persisted ingest_seq."""
        with contextlib.closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                SELECT
                    ingest_seq, event_id, order_id, event_type, event_ts, updated_at,
                    team_id, region_id, priority, hold_reason, resolution_code,
                    source_name, batch_id, ingested_at
                FROM bronze_events
                WHERE ingest_seq > ?
                ORDER BY ingest_seq ASC
                """,
                (last_ingest_seq,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def fetch_event_tuples_since(self, last_ingest_seq: int) -> list[tuple[Any, ...]]:
        """Read incremental Bronze event tuples without dict allocation overhead."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            return conn.execute(
                """
                SELECT
                    ingest_seq, event_id, order_id, event_type, event_ts, updated_at,
                    team_id, region_id, priority, hold_reason, resolution_code,
                    source_name, batch_id, ingested_at
                FROM bronze_events
                WHERE ingest_seq > ?
                ORDER BY ingest_seq ASC
                """,
                (last_ingest_seq,),
            ).fetchall()
        finally:
            conn.close()

    def advance_watermark(
        self,
        pipeline_name: str,
        last_ingest_seq: int,
        last_ingested_at: str,
    ) -> None:
        """Persist updated CDC watermark after atomic Silver Parquet materialization."""
        updated_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with contextlib.closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO cdc_watermarks (
                        pipeline_name, last_ingest_seq, last_ingested_at, updated_at_utc
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (pipeline_name, last_ingest_seq, last_ingested_at, updated_utc),
                )

    def get_outbox_manifests(self) -> list[dict[str, Any]]:
        """Return all persisted batch outbox manifests ordered chronologically."""
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM outbox_manifests ORDER BY ingested_at ASC, batch_id ASC"
            ).fetchall()
            return [dict(row) for row in rows]
