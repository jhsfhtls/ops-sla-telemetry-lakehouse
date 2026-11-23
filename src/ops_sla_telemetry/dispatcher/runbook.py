"""Automated SRE runbook and blameless post-mortem drafter (POP_T_036 / POP_B_115)."""

from __future__ import annotations

from ops_sla_telemetry.dispatcher.severity import AlertCandidate


def draft_incident_runbook(alert: AlertCandidate) -> str:
    """Generate a zero-PII operational runbook and post-mortem draft for an alert."""
    return "\n".join(
        [
            f"# Incident Runbook & Post-Mortem Draft — {alert.incident_key}",
            "",
            f"- **Severity:** `{alert.severity}`",
            f"- **Source Domain:** `{alert.source_domain}`",
            f"- **Cohort:** `{alert.cohort_key}` (Region: `{alert.region_id}`, Priority: `{alert.priority}`)",
            f"- **Summary:** {alert.summary}",
            "",
            "## 1. SRE Telemetry Snapshot (99.0% SLO)",
            "",
            "| Metric | Observed Value |",
            "| :--- | :--- |",
            f"| **30d SLA Compliance** | `{alert.sla_compliance_pct_30d:.2f}%` |",
            f"| **Burn Rate (1h / 6h)** | `{alert.burn_rate_1h:.2f}x` / `{alert.burn_rate_6h:.2f}x` |",
            f"| **Burn Rate (24h / 7d)** | `{alert.burn_rate_24h:.2f}x` / `{alert.burn_rate_7d:.2f}x` |",
            f"| **At-Risk Queue (< 24h)** | `{alert.at_risk_queue_count}` |",
            "",
            "## 2. Immediate Triage Checklist (POP_T_036)",
            "",
            "- [ ] Inspect `fct_team_daily_capacity` for overloaded crews and rebalance open WIP.",
            "- [ ] Check `data/quarantine/` for recent ODCS v3.1 contract rejections or delayed syncs.",
            "- [ ] Verify `fct_service_order_sla` for open `AT_RISK` items approaching SLA breach.",
            "- [ ] Confirm mitigation branch / runbook update requires human PR review before merge.",
            "",
            "## 3. Executive Crisis Communication Template (POP_B_115 — Zero PII)",
            "",
            f"> **Status Update [{alert.severity}]:** Cohort `{alert.cohort_key}` triggered an error-budget "
            f"burn-rate alert (`1h={alert.burn_rate_1h:.2f}x`, `6h={alert.burn_rate_6h:.2f}x`, "
            f"`30d SLA={alert.sla_compliance_pct_30d:.2f}%`). Automated triage draft `{alert.incident_key}` "
            "has been generated; crew rebalancing and root-cause verification are underway.",
            "",
            "## 4. Blameless Post-Mortem Skeleton (POP_T_036)",
            "",
            "- **Root Cause Category:** _TBD (Dispatch capacity / Late sync / External hold)_",
            "- **Error Budget Impact:** _TBD_",
            "- **Preventive Action Items:** _TBD_",
            "",
        ]
    )
