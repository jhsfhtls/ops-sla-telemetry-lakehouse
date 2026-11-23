"""Read-only local collectors for C.O.R.E. operational telemetry (POP_T_154 / ADR-003)."""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ops_sla_telemetry.collectors.allowlist import (
    AGENT_TELEMETRY_ALLOWLIST,
    CAREER_ALLOWLIST,
    CONSULTING_ALLOWLIST,
    POP_FRESHNESS_ALLOWLIST,
    SRS_ALLOWLIST,
    assert_outside_public_repo,
    hash_opaque_id,
    project_allowlist,
)

_POP_ID_RE = re.compile(r"^(POP_[A-Z]+_\d+)")
_REVIEW_DATE_RE = re.compile(
    r"(?:Last[ _-]Reviewed|Última\s+Atualização|Ultima\s+Atualizacao)"
    r"(?:\*\*|</b>)?\s*:\s*(?:\*\*|</b>)?\s*['\"]?(\d{4}-\d{2}-\d{2})['\"]?",
    re.IGNORECASE,
)
_CONSULTING_CLOSED_STATUSES: frozenset[str] = frozenset(
    {"CONCLUIDO", "CONCLUÍDO", "ENCERRADO", "CLOSED", "CANCELADO", "LOST", "DONE"}
)
_CAREER_CLOSED_STATUSES: frozenset[str] = frozenset(
    {
        "REJEITADA",
        "REJEITADO",
        "REJECTED",
        "ENCERRADA",
        "ENCERRADO",
        "CLOSED",
        "OFFER_ACCEPTED",
        "DESISTENCIA",
        "WITHDRAWN",
    }
)


def _parse_date_prefix(raw_value: Any) -> date | None:
    if not isinstance(raw_value, str):
        return None
    cleaned = raw_value.strip()
    if len(cleaned) < 10:
        return None
    try:
        return date.fromisoformat(cleaned[:10])
    except ValueError:
        return None


def collect_agent_telemetry(log_path: Path) -> list[dict[str, Any]]:
    """Read agent_telemetry.jsonl read-only and project strictly allowlisted fields."""
    if not log_path.is_file():
        return []

    records: list[dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, Mapping):
                records.append(project_allowlist(parsed, AGENT_TELEMETRY_ALLOWLIST))
    return records


def collect_pop_freshness(
    docs_dir: Path,
    *,
    reference_date: date | None = None,
    max_age_days: int = 180,
) -> list[dict[str, Any]]:
    """Scan POP_*.md headers read-only for freshness dates without storing prose."""
    if max_age_days < 0:
        raise ValueError(f"max_age_days must be non-negative, got {max_age_days}.")

    resolved_docs = Path(docs_dir).resolve()
    if not resolved_docs.is_dir():
        return []

    ref_date = reference_date or datetime.now(UTC).date()
    findings: list[dict[str, Any]] = []

    for pop_path in sorted(resolved_docs.rglob("POP_*.md")):
        if not pop_path.is_file():
            continue

        id_match = _POP_ID_RE.match(pop_path.stem)
        pop_id = id_match.group(1) if id_match else pop_path.stem
        rel_path = pop_path.relative_to(resolved_docs).as_posix()

        # Read header slice only so full SOP body prose never enters memory.
        with pop_path.open("r", encoding="utf-8", errors="replace") as handle:
            header_slice = handle.read(4096)

        date_match = _REVIEW_DATE_RE.search(header_slice)
        if date_match is None:
            record: dict[str, Any] = {
                "pop_id": pop_id,
                "relative_path": rel_path,
                "last_reviewed": None,
                "age_days": None,
                "status": "missing_review_date",
            }
            findings.append(project_allowlist(record, POP_FRESHNESS_ALLOWLIST))
            continue

        raw_date = date_match.group(1)
        try:
            reviewed_date = date.fromisoformat(raw_date)
        except ValueError:
            record = {
                "pop_id": pop_id,
                "relative_path": rel_path,
                "last_reviewed": raw_date,
                "age_days": None,
                "status": "invalid_review_date",
            }
            findings.append(project_allowlist(record, POP_FRESHNESS_ALLOWLIST))
            continue

        age_days = (ref_date - reviewed_date).days
        status = "stale" if age_days > max_age_days else "fresh"
        record = {
            "pop_id": pop_id,
            "relative_path": rel_path,
            "last_reviewed": reviewed_date.isoformat(),
            "age_days": age_days,
            "status": status,
        }
        findings.append(project_allowlist(record, POP_FRESHNESS_ALLOWLIST))

    return findings


def collect_consulting_sla(
    state_path: Path,
    *,
    reference_date: date | None = None,
) -> list[dict[str, Any]]:
    """Read consulting_pipeline.json read-only with strict allowlist and hashed code IDs."""
    if not state_path.is_file():
        return []

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        return []

    ref_date = reference_date or datetime.now(UTC).date()
    entries: list[dict[str, Any]] = []
    for idx, raw_item in enumerate(payload.get("clientes", [])):
        if not isinstance(raw_item, Mapping):
            continue
        projected = project_allowlist(raw_item, CONSULTING_ALLOWLIST)
        raw_id = str(projected.get("id") or f"idx_{idx}")
        projected["id"] = hash_opaque_id("OPS-CON", raw_id)

        anchor_date = _parse_date_prefix(projected.get("data_entrada")) or _parse_date_prefix(
            projected.get("data_abertura")
        )
        days_in_phase = (ref_date - anchor_date).days if anchor_date is not None else None
        status_norm = str(projected.get("status") or "").strip().upper()

        if status_norm in _CONSULTING_CLOSED_STATUSES:
            sla_status = "CLOSED"
        elif days_in_phase is None:
            sla_status = "UNKNOWN"
        elif days_in_phase > 7:
            sla_status = "SLA_BREACHED"
        elif days_in_phase >= 6:
            sla_status = "AT_RISK"
        else:
            sla_status = "ON_TRACK"

        projected["days_in_phase"] = days_in_phase
        projected["sla_status"] = sla_status
        entries.append(projected)
    return entries


def collect_career_followups(
    state_path: Path,
    *,
    reference_date: date | None = None,
) -> list[dict[str, Any]]:
    """Read career_pipeline.json read-only and compute D+3/D+7 follow-up SLA statuses."""
    if not state_path.is_file():
        return []

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        return []

    ref_date = reference_date or datetime.now(UTC).date()
    entries: list[dict[str, Any]] = []
    for idx, raw_item in enumerate(payload.get("candidaturas", [])):
        if not isinstance(raw_item, Mapping):
            continue
        projected = project_allowlist(raw_item, CAREER_ALLOWLIST)
        raw_id = str(projected.get("id") or f"idx_{idx}")
        projected["id"] = hash_opaque_id("CAR", raw_id)

        app_date = _parse_date_prefix(projected.get("data_aplicacao"))
        days_since = (ref_date - app_date).days if app_date is not None else None
        status_norm = str(projected.get("status") or "").strip().upper()

        if status_norm in _CAREER_CLOSED_STATUSES:
            followup_status = "CLOSED"
        elif days_since is None:
            followup_status = "OK"
        elif days_since >= 7:
            followup_status = "OVERDUE_D7"
        elif days_since >= 3:
            followup_status = "DUE_D3"
        else:
            followup_status = "OK"

        projected["days_since_application"] = days_since
        projected["followup_status"] = followup_status
        entries.append(projected)
    return entries


def collect_srs_due_cards(
    srs_state_path: Path,
    *,
    reference_date: date | None = None,
) -> list[dict[str, Any]]:
    """Read srs_state.json read-only and compute due/overdue review status by hashed card_id."""
    if not srs_state_path.is_file():
        return []

    payload = json.loads(srs_state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        return []

    ref_date = reference_date or datetime.now(UTC).date()
    entries: list[dict[str, Any]] = []
    for raw_card_id, card_data in sorted(payload.items()):
        if not isinstance(card_data, Mapping):
            continue
        merged = dict(card_data)
        merged["card_id"] = str(raw_card_id)
        projected = project_allowlist(merged, SRS_ALLOWLIST)
        projected["card_id"] = hash_opaque_id("SRS", str(raw_card_id))

        next_rev = _parse_date_prefix(projected.get("next_review"))
        if next_rev is None:
            is_due = True
            days_overdue = 0
        else:
            is_due = next_rev <= ref_date
            days_overdue = max(0, (ref_date - next_rev).days) if is_due else 0

        projected["is_due"] = is_due
        projected["days_overdue"] = days_overdue
        entries.append(projected)
    return entries


class CoreMultiRepoCollector:
    """Orchestrates read-only metadata collection across C.O.R.E. repositories."""

    def __init__(
        self,
        core_root: Path,
        studies_root: Path | None = None,
        output_dir: Path | None = None,
        public_repo_root: Path | None = None,
    ) -> None:
        self.core_root = Path(core_root).resolve()
        self.studies_root = (
            Path(studies_root).resolve()
            if studies_root is not None
            else (self.core_root.parent / "studies")
        )
        self.output_dir = (
            Path(output_dir).resolve()
            if output_dir is not None
            else (self.core_root / ".logs" / "ops_sla_telemetry")
        )
        self.public_repo_root = (
            Path(public_repo_root).resolve()
            if public_repo_root is not None
            else Path(__file__).resolve().parents[3]
        )

    def collect_all(
        self,
        reference_date: date | None = None,
        *,
        max_pop_age_days: int = 180,
    ) -> dict[str, Any]:
        """Collect sanitized metadata from all C.O.R.E. sources without mutating repos."""
        ref_date = reference_date or datetime.now(UTC).date()
        agent_spans = collect_agent_telemetry(self.core_root / ".logs" / "agent_telemetry.jsonl")
        pop_freshness = collect_pop_freshness(
            self.core_root / "docs",
            reference_date=ref_date,
            max_age_days=max_pop_age_days,
        )
        consulting_sla = collect_consulting_sla(
            self.core_root / ".agents" / "state" / "consulting_pipeline.json",
            reference_date=ref_date,
        )
        career_followups = collect_career_followups(
            self.core_root / ".agents" / "state" / "career_pipeline.json",
            reference_date=ref_date,
        )
        srs_cards = collect_srs_due_cards(
            self.studies_root / ".agents" / "memory" / "srs_state.json",
            reference_date=ref_date,
        )

        summary = {
            "agent_spans_total": len(agent_spans),
            "agent_errors_total": sum(
                1 for item in agent_spans if item.get("status") in ("error", "blocked")
            ),
            "stale_pops_count": sum(
                1
                for item in pop_freshness
                if item.get("status") in ("stale", "missing_review_date", "invalid_review_date")
            ),
            "consulting_at_risk_count": sum(
                1 for item in consulting_sla if item.get("sla_status") in ("AT_RISK", "SLA_BREACHED")
            ),
            "career_followup_due_count": sum(
                1
                for item in career_followups
                if item.get("followup_status") in ("DUE_D3", "OVERDUE_D7")
            ),
            "srs_overdue_count": sum(1 for item in srs_cards if item.get("is_due")),
        }

        return {
            "schema_version": "1.0.0",
            "collected_date": ref_date.isoformat(),
            "privacy_mode": "STRICT_ALLOWLIST_READ_ONLY",
            "summary": summary,
            "sources": {
                "agent_telemetry": agent_spans,
                "pop_freshness": pop_freshness,
                "consulting_sla": consulting_sla,
                "career_followups": career_followups,
                "srs_cards": srs_cards,
            },
        }

    def write_snapshot(
        self,
        snapshot: dict[str, Any],
        filename: str = "core_health_snapshot.json",
    ) -> Path:
        """Persist sanitized snapshot strictly outside the public repository tree."""
        assert_outside_public_repo(self.output_dir, self.public_repo_root)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.output_dir.parent.name == ".logs":
            logs_gitignore = self.output_dir.parent / ".gitignore"
            if not logs_gitignore.exists():
                with logs_gitignore.open("w", encoding="utf-8", newline="\n") as fh:
                    fh.write("*\n")

        target = self.output_dir / filename
        tmp_path = self.output_dir / f".{filename}.{uuid.uuid4().hex[:8]}.tmp"
        serialized = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"
        with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
        os.replace(tmp_path, target)
        return target
