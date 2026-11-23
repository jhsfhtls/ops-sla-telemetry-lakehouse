"""Closed field allowlists, opaque entity hashing, and output privacy boundary (ADR-003)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

AGENT_TELEMETRY_ALLOWLIST: frozenset[str] = frozenset(
    {
        "timestamp_utc",
        "trace_id",
        "span_id",
        "skill",
        "model",
        "provider",
        "tokens_total",
        "cost_usd",
        "duration_ms",
        "status",
        "repo",
    }
)
POP_FRESHNESS_ALLOWLIST: frozenset[str] = frozenset(
    {"pop_id", "relative_path", "last_reviewed", "age_days", "status"}
)
CONSULTING_ALLOWLIST: frozenset[str] = frozenset(
    {"id", "fase_atual", "status", "data_abertura", "data_entrada"}
)
CAREER_ALLOWLIST: frozenset[str] = frozenset({"id", "data_aplicacao", "status"})
SRS_ALLOWLIST: frozenset[str] = frozenset(
    {"card_id", "last_reviewed", "next_review", "interval", "review_count", "last_result"}
)


class PrivacyBoundaryError(ValueError):
    """Raised when a collector output path violates the public repository boundary."""


def project_allowlist(record: Mapping[str, Any], allowed_keys: frozenset[str]) -> dict[str, Any]:
    """Return a shallow copy containing strictly keys defined in allowed_keys."""
    return {key: record[key] for key in allowed_keys if key in record}


def hash_opaque_id(prefix: str, raw_id: str) -> str:
    """Convert raw entity identifiers into non-reversible opaque codes."""
    digest = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def assert_outside_public_repo(output_dir: Path, public_repo_root: Path) -> None:
    """Fail closed if output_dir resolves inside the public repository tree."""
    resolved_out = Path(output_dir).resolve()
    resolved_public = Path(public_repo_root).resolve()
    if resolved_out == resolved_public or resolved_out.is_relative_to(resolved_public):
        raise PrivacyBoundaryError(
            f"Refusing to write real C.O.R.E. telemetry inside public repo: {resolved_out}"
        )
