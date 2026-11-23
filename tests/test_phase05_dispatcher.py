"""Phase 05 tests: severity classification, runbook drafts, dry-run gate, cap, and idempotency."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops_sla_telemetry.dispatcher import (
    AlertCandidate,
    AlertDispatcher,
    HumanConfirmationError,
    NotifyWebhookAdapter,
    TickTickBridgeAdapter,
    build_incident_key,
    classify_severity,
    draft_incident_runbook,
)


def _make_alert(
    cohort_key: str,
    severity: str,
    *,
    window_bucket: str = "2026-11-23T18",
    burn_1h: float = 15.0,
    burn_6h: float = 15.0,
    burn_24h: float = 8.0,
    burn_7d: float = 3.0,
    sla_pct: float = 97.5,
    at_risk: int = 4,
) -> AlertCandidate:
    region_id, priority = cohort_key.split("|", 1)
    return AlertCandidate(
        incident_key=build_incident_key("FIELD_SERVICE", cohort_key, severity, window_bucket),
        cohort_key=cohort_key,
        region_id=region_id,
        priority=priority,
        severity=severity,
        summary=f"Burn rate breach on {cohort_key}",
        burn_rate_1h=burn_1h,
        burn_rate_6h=burn_6h,
        burn_rate_24h=burn_24h,
        burn_rate_7d=burn_7d,
        sla_compliance_pct_30d=sla_pct,
        at_risk_queue_count=at_risk,
    )


def test_severity_classification_boundaries() -> None:
    """Verify multi-window SRE burn-rate classification across SEV-1..SEV-4 and OK."""
    assert (
        classify_severity(
            burn_rate_1h=14.4,
            burn_rate_6h=14.4,
            burn_rate_24h=1.0,
            burn_rate_7d=0.5,
            at_risk_queue_count=0,
            sla_compliance_pct_30d=99.5,
        )
        == "SEV-1"
    )
    assert (
        classify_severity(
            burn_rate_1h=20.0,
            burn_rate_6h=6.0,
            burn_rate_24h=6.0,
            burn_rate_7d=1.0,
            at_risk_queue_count=0,
            sla_compliance_pct_30d=99.5,
        )
        == "SEV-2"
    )
    assert (
        classify_severity(
            burn_rate_1h=1.0,
            burn_rate_6h=2.0,
            burn_rate_24h=2.0,
            burn_rate_7d=2.0,
            at_risk_queue_count=0,
            sla_compliance_pct_30d=99.5,
        )
        == "SEV-3"
    )
    assert (
        classify_severity(
            burn_rate_1h=0.5,
            burn_rate_6h=0.5,
            burn_rate_24h=0.5,
            burn_rate_7d=0.5,
            at_risk_queue_count=3,
            sla_compliance_pct_30d=99.2,
        )
        == "SEV-4"
    )
    assert (
        classify_severity(
            burn_rate_1h=0.0,
            burn_rate_6h=0.0,
            burn_rate_24h=0.5,
            burn_rate_7d=0.8,
            at_risk_queue_count=0,
            sla_compliance_pct_30d=99.2,
        )
        == "OK"
    )


def test_runbook_draft_contains_pop_sections_without_pii() -> None:
    """Generated runbook/post-mortem draft follows POP_T_036 and POP_B_115."""
    alert = _make_alert("REG-NORTE|P1", "SEV-1")
    draft = draft_incident_runbook(alert)
    assert alert.incident_key in draft
    assert "POP_T_036" in draft
    assert "POP_B_115" in draft
    assert "REG-NORTE|P1" in draft
    assert "SEV-1" in draft


def test_dispatcher_refuses_live_execution_without_confirmation(tmp_path: Path) -> None:
    """Calling execute=True without confirm=True must raise HumanConfirmationError."""
    marker = tmp_path / "should_not_exist.txt"
    mock_ticktick = tmp_path / "mock_ticktick.py"
    mock_ticktick.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('called')\n",
        encoding="utf-8",
    )

    dispatcher = AlertDispatcher(
        tmp_path / "ledger.sqlite",
        adapters=[TickTickBridgeAdapter(script_path=mock_ticktick)],
    )
    alert = _make_alert("REG-NORTE|P1", "SEV-1")

    with pytest.raises(HumanConfirmationError):
        dispatcher.dispatch([alert], execute=True, confirm=False)

    assert not marker.exists()
    assert dispatcher.ledger.list_dispatched() == []


def test_dispatcher_dry_run_default_idempotency_and_cap(tmp_path: Path) -> None:
    """Verify dry-run default, highest-severity-first cap, and cross-run deduplication."""
    calls_log = tmp_path / "calls.jsonl"
    mock_ticktick = tmp_path / "mock_ticktick.py"
    mock_ticktick.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                f"log = Path({str(calls_log)!r})",
                "with log.open('a', encoding='utf-8') as f:",
                "    f.write(json.dumps(sys.argv[1:]) + '\\n')",
                "print(json.dumps({'id': 'task-123', 'status': 'created'}))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    mock_notify = tmp_path / "mock_notify.py"
    mock_notify.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                f"log = Path({str(calls_log)!r})",
                "with log.open('a', encoding='utf-8') as f:",
                "    f.write(json.dumps(sys.argv[1:]) + '\\n')",
                "print('[SUCESSO] mock webhook')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    dispatcher = AlertDispatcher(
        tmp_path / "ledger.sqlite",
        adapters=[
            TickTickBridgeAdapter(script_path=mock_ticktick),
            NotifyWebhookAdapter(script_path=mock_notify),
        ],
        max_alerts_per_run=2,
    )

    sev4 = _make_alert("REG-SUL|P3", "SEV-4")
    sev1 = _make_alert("REG-NORTE|P1", "SEV-1")
    sev1_dup = _make_alert("REG-NORTE|P1", "SEV-1")
    sev2 = _make_alert("REG-SUDESTE|P2", "SEV-2")
    ok_alert = _make_alert("REG-CENTRO|P3", "OK")

    # 1. Default dry-run: no external subprocesses, no ledger writes, cap=2 picks SEV-1 & SEV-2
    dry_summary = dispatcher.dispatch([sev4, sev1, sev1_dup, sev2, ok_alert])
    assert dry_summary.dry_run is True
    assert dry_summary.received_count == 5
    assert dry_summary.dispatched_count == 2
    assert dry_summary.suppressed_duplicate_count == 1
    assert dry_summary.capped_count == 1
    assert [item.severity for item in dry_summary.items] == ["SEV-1", "SEV-2"]
    assert dry_summary.items[0].adapter_results[0]["project_name"] == "00_INBOX_AGENTES"
    assert dry_summary.items[0].adapter_results[0]["priority"] == 5
    assert not calls_log.exists()
    assert dispatcher.ledger.list_dispatched() == []

    # 2. Confirmed live execution: invokes both adapters and records ledger
    live_summary = dispatcher.dispatch(
        [sev4, sev1, sev1_dup, sev2, ok_alert],
        execute=True,
        confirm=True,
    )
    assert live_summary.dry_run is False
    assert live_summary.dispatched_count == 2
    assert live_summary.suppressed_duplicate_count == 1
    assert live_summary.capped_count == 1
    assert [item.severity for item in live_summary.items] == ["SEV-1", "SEV-2"]

    logged_calls = [json.loads(ln) for ln in calls_log.read_text(encoding="utf-8").splitlines()]
    assert len(logged_calls) == 4  # 2 alerts * 2 adapters
    assert "--priority" in logged_calls[0]
    assert "00_INBOX_AGENTES" in logged_calls[0]
    assert "5" in logged_calls[0]

    # 3. Replay of previously dispatched SEV-1 & SEV-2 is suppressed by DispatchLedger,
    #    freeing capacity for SEV-4 to dispatch cleanly
    replay_summary = dispatcher.dispatch([sev1, sev2, sev4], execute=True, confirm=True)
    assert replay_summary.dispatched_count == 1
    assert replay_summary.suppressed_duplicate_count == 2
    assert replay_summary.capped_count == 0
    assert replay_summary.items[0].severity == "SEV-4"
    assert len(dispatcher.ledger.list_dispatched()) == 3
