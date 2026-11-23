"""Incremental CDC event ingestor from Bronze SQLite WAL to Silver Parquet ZSTD."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ops_sla_telemetry.config import LakehouseSettings
from ops_sla_telemetry.ingestion.bronze_store import BronzeEventStore
from ops_sla_telemetry.ingestion.contracts import EventContractValidator

SILVER_ARROW_SCHEMA = pa.schema(
    [
        pa.field("ingest_seq", pa.int64(), nullable=False),
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("order_id", pa.string(), nullable=False),
        pa.field("event_type", pa.string(), nullable=False),
        pa.field("event_ts", pa.string(), nullable=False),
        pa.field("updated_at", pa.string(), nullable=False),
        pa.field("team_id", pa.string(), nullable=False),
        pa.field("region_id", pa.string(), nullable=False),
        pa.field("priority", pa.string(), nullable=False),
        pa.field("hold_reason", pa.string(), nullable=True),
        pa.field("resolution_code", pa.string(), nullable=True),
        pa.field("source_name", pa.string(), nullable=False),
        pa.field("batch_id", pa.string(), nullable=False),
        pa.field("ingested_at", pa.string(), nullable=False),
    ]
)


@dataclass(frozen=True)
class IngestionReport:
    """Summary of one Bronze ingestion and ODCS v3.1 contract gate execution."""

    batch_id: str
    source_name: str
    received_count: int
    valid_count: int
    inserted_count: int
    duplicates_skipped: int
    quarantined_count: int
    payload_sha256: str
    quarantine_path: Path | None


@dataclass(frozen=True)
class CdcSyncReport:
    """Summary of one incremental Bronze-to-Silver Parquet CDC watermark sync."""

    pipeline_name: str
    rows_synced: int
    from_seq: int
    to_seq: int
    parquet_path: Path | None
    parquet_sha256: str | None
    watermark_seq: int


def _compute_file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65_536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class CdcEventIngestor:
    """Orchestrates ODCS validation, Bronze outbox persistence, and Silver CDC Parquet."""

    def __init__(
        self,
        settings: LakehouseSettings | None = None,
        pipeline_name: str = "field_service_events",
    ) -> None:
        self.settings = settings or LakehouseSettings()
        self.settings.ensure_directories()
        self.pipeline_name = pipeline_name
        assert self.settings.bronze_db_path is not None
        self.bronze_store = BronzeEventStore(self.settings.bronze_db_path)
        self.validator = EventContractValidator()

    @staticmethod
    def _compute_events_sha256(valid_events: list[dict[str, Any]]) -> str:
        hasher = hashlib.sha256()
        for ev in valid_events:
            line = (
                f"{ev['event_id']}|{ev['order_id']}|{ev['event_type']}|{ev['event_ts']}|"
                f"{ev['updated_at']}|{ev['team_id']}|{ev['region_id']}|{ev['priority']}|"
                f"{ev.get('hold_reason') or ''}|{ev.get('resolution_code') or ''}\n"
            )
            hasher.update(line.encode("utf-8"))
        return hasher.hexdigest()

    def _write_quarantine(self, batch_id: str, records: list[dict[str, Any]]) -> Path | None:
        if not records:
            return None
        assert self.settings.quarantine_dir is not None
        target = self.settings.quarantine_dir / f"{batch_id}_quarantine.jsonl"
        with target.open("w", encoding="utf-8", newline="\n") as handle:
            for item in records:
                handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
        return target

    def ingest_batch(
        self,
        events: Iterable[Mapping[str, Any]],
        *,
        source_name: str = "field_simulator",
        batch_id: str | None = None,
    ) -> IngestionReport:
        """Validate incoming events and append valid records idempotently into Bronze WAL."""
        raw_list = list(events)
        resolved_batch_id = batch_id or f"batch_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
        valid_events, quarantined = self.validator.validate_batch(raw_list)
        quarantine_path = self._write_quarantine(resolved_batch_id, quarantined)
        payload_sha256 = self._compute_events_sha256(valid_events)

        inserted_count, duplicates_skipped = self.bronze_store.append_batch(
            batch_id=resolved_batch_id,
            source_name=source_name,
            valid_events=valid_events,
            quarantined_count=len(quarantined),
            payload_sha256=payload_sha256,
        )

        return IngestionReport(
            batch_id=resolved_batch_id,
            source_name=source_name,
            received_count=len(raw_list),
            valid_count=len(valid_events),
            inserted_count=inserted_count,
            duplicates_skipped=duplicates_skipped,
            quarantined_count=len(quarantined),
            payload_sha256=payload_sha256,
            quarantine_path=quarantine_path,
        )

    def sync_silver(self) -> CdcSyncReport:
        """Extract events above CDC watermark into ZSTD-compressed Parquet via atomic swap."""
        last_seq = self.bronze_store.get_watermark(self.pipeline_name)
        delta_rows = self.bronze_store.fetch_event_tuples_since(last_seq)
        if not delta_rows:
            return CdcSyncReport(
                pipeline_name=self.pipeline_name,
                rows_synced=0,
                from_seq=last_seq,
                to_seq=last_seq,
                parquet_path=None,
                parquet_sha256=None,
                watermark_seq=last_seq,
            )

        from_seq = int(delta_rows[0][0])
        to_seq = int(delta_rows[-1][0])
        last_ingested_at = str(delta_rows[-1][13])

        assert self.settings.silver_dir is not None
        final_path = (
            self.settings.silver_dir / f"service_events_{from_seq:09d}_{to_seq:09d}.parquet"
        )
        tmp_path = self.settings.silver_dir / f".{final_path.name}.{uuid.uuid4().hex[:8]}.tmp.parquet"

        column_names = SILVER_ARROW_SCHEMA.names
        columns = [list(col) for col in zip(*delta_rows, strict=True)]
        table = pa.table(dict(zip(column_names, columns, strict=True)), schema=SILVER_ARROW_SCHEMA)
        pq.write_table(table, tmp_path, compression="zstd")
        os.replace(tmp_path, final_path)

        parquet_sha256 = _compute_file_sha256(final_path)
        self.bronze_store.advance_watermark(self.pipeline_name, to_seq, last_ingested_at)

        return CdcSyncReport(
            pipeline_name=self.pipeline_name,
            rows_synced=len(delta_rows),
            from_seq=from_seq,
            to_seq=to_seq,
            parquet_path=final_path,
            parquet_sha256=parquet_sha256,
            watermark_seq=to_seq,
        )

    def ingest_and_sync(
        self,
        events: Iterable[Mapping[str, Any]],
        *,
        source_name: str = "field_simulator",
        batch_id: str | None = None,
    ) -> tuple[IngestionReport, CdcSyncReport]:
        """Run Bronze ingestion and incremental Silver Parquet CDC sync in sequence."""
        ingest_report = self.ingest_batch(events, source_name=source_name, batch_id=batch_id)
        sync_report = self.sync_silver()
        return ingest_report, sync_report
