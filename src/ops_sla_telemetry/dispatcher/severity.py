"""SRE multi-window burn-rate severity classification and incident key generation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

SEVERITY_RANK: dict[str, int] = {
    "SEV-1": 1,
    "SEV-2": 2,
    "SEV-3": 3,
    "SEV-4": 4,
    "OK": 99,
}


@dataclass(frozen=True)
class AlertCandidate:
    """Structured operational or field-service SLA alert candidate (zero PII)."""

    incident_key: str
    cohort_key: str
    region_id: str
    priority: str
    severity: str
    summary: str
    burn_rate_1h: float
    burn_rate_6h: float
    burn_rate_24h: float
    burn_rate_7d: float
    sla_compliance_pct_30d: float
    at_risk_queue_count: int
    source_domain: str = "FIELD_SERVICE"


def classify_severity(
    *,
    burn_rate_1h: float,
    burn_rate_6h: float,
    burn_rate_24h: float,
    burn_rate_7d: float,
    at_risk_queue_count: int,
    sla_compliance_pct_30d: float,
) -> str:
    """Classify burn-rate and at-risk metrics into SEV-1..SEV-4 or OK (ADR-002)."""
    if burn_rate_1h >= 14.4 and burn_rate_6h >= 14.4:
        return "SEV-1"
    if burn_rate_6h >= 6.0 and burn_rate_24h >= 6.0:
        return "SEV-2"
    if burn_rate_24h >= 2.0 and burn_rate_7d >= 2.0:
        return "SEV-3"
    if at_risk_queue_count > 0 or sla_compliance_pct_30d < 99.0:
        return "SEV-4"
    return "OK"


def build_incident_key(
    source_domain: str,
    cohort_key: str,
    severity: str,
    window_bucket: str,
) -> str:
    """Build deterministic idempotent incident deduplication key."""
    payload = "|".join(
        part.strip() for part in (source_domain, cohort_key, severity, window_bucket)
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"INC-{digest}"
