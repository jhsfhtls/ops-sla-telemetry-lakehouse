"""Idempotent alert dispatcher, severity classification, and runbook drafter."""

from ops_sla_telemetry.dispatcher.adapters import (
    DispatchAdapter,
    DryRunLogAdapter,
    NotifyWebhookAdapter,
    TickTickBridgeAdapter,
)
from ops_sla_telemetry.dispatcher.engine import (
    AlertDispatcher,
    DispatchItemResult,
    DispatchLedger,
    DispatchSummary,
    HumanConfirmationError,
)
from ops_sla_telemetry.dispatcher.runbook import draft_incident_runbook
from ops_sla_telemetry.dispatcher.severity import (
    SEVERITY_RANK,
    AlertCandidate,
    build_incident_key,
    classify_severity,
)

__all__ = [
    "AlertCandidate",
    "AlertDispatcher",
    "DispatchAdapter",
    "DispatchItemResult",
    "DispatchLedger",
    "DispatchSummary",
    "DryRunLogAdapter",
    "HumanConfirmationError",
    "NotifyWebhookAdapter",
    "SEVERITY_RANK",
    "TickTickBridgeAdapter",
    "build_incident_key",
    "classify_severity",
    "draft_incident_runbook",
]
