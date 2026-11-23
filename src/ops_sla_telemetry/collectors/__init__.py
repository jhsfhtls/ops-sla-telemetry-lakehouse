"""Read-only C.O.R.E. multi-repo telemetry collectors with strict allowlists."""

from ops_sla_telemetry.collectors.allowlist import (
    AGENT_TELEMETRY_ALLOWLIST,
    CAREER_ALLOWLIST,
    CONSULTING_ALLOWLIST,
    POP_FRESHNESS_ALLOWLIST,
    SRS_ALLOWLIST,
    PrivacyBoundaryError,
    assert_outside_public_repo,
    hash_opaque_id,
    project_allowlist,
)
from ops_sla_telemetry.collectors.core_collectors import (
    CoreMultiRepoCollector,
    collect_agent_telemetry,
    collect_career_followups,
    collect_consulting_sla,
    collect_pop_freshness,
    collect_srs_due_cards,
)

__all__ = [
    "AGENT_TELEMETRY_ALLOWLIST",
    "CAREER_ALLOWLIST",
    "CONSULTING_ALLOWLIST",
    "CoreMultiRepoCollector",
    "POP_FRESHNESS_ALLOWLIST",
    "PrivacyBoundaryError",
    "SRS_ALLOWLIST",
    "assert_outside_public_repo",
    "collect_agent_telemetry",
    "collect_career_followups",
    "collect_consulting_sla",
    "collect_pop_freshness",
    "collect_srs_due_cards",
    "hash_opaque_id",
    "project_allowlist",
]
