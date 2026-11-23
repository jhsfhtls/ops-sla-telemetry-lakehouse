"""Decoupled optional adapters for TickTick bridge (POP_B_179) and webhook notifications."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

from ops_sla_telemetry.dispatcher.severity import AlertCandidate


class DispatchAdapter(Protocol):
    """Protocol implemented by all alert dispatch targets."""

    name: str

    def send(
        self,
        alert: AlertCandidate,
        runbook_md: str,
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Deliver or simulate delivery of an alert with its runbook draft."""
        ...


class DryRunLogAdapter:
    """In-memory preview and structured log adapter with zero external network I/O."""

    name = "dry_run_log"

    def send(
        self,
        alert: AlertCandidate,
        runbook_md: str,
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Return structured preview or recorded local dispatch metadata."""
        return {
            "adapter": self.name,
            "dry_run": dry_run,
            "status": "DRY_RUN_PREVIEW" if dry_run else "LOGGED",
            "incident_key": alert.incident_key,
            "severity": alert.severity,
            "runbook_chars": len(runbook_md),
        }


class TickTickBridgeAdapter:
    """Optional decoupled bridge invoking C.O.R.E. ticktick_client.py via subprocess."""

    name = "ticktick_bridge"

    def __init__(
        self,
        *,
        script_path: Path | None = None,
        project_name: str = "00_INBOX_AGENTES",
        priority: int = 5,
        python_executable: str = sys.executable,
    ) -> None:
        self.script_path = Path(script_path).resolve() if script_path is not None else None
        self.project_name = project_name
        self.priority = int(priority)
        self.python_executable = python_executable

    def send(
        self,
        alert: AlertCandidate,
        runbook_md: str,
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Preview TickTick task creation in dry-run or invoke ticktick_client.py live."""
        title = f"[{alert.severity}] SLA Alert {alert.cohort_key} ({alert.incident_key})"
        content = (
            f"Summary: {alert.summary}\n"
            f"30d SLA: {alert.sla_compliance_pct_30d:.2f}% | "
            f"At-risk: {alert.at_risk_queue_count}\n\n"
            f"{runbook_md[:600]}"
        )

        if dry_run:
            return {
                "adapter": self.name,
                "dry_run": True,
                "status": "DRY_RUN_PLANNED",
                "project_name": self.project_name,
                "priority": self.priority,
                "title": title,
                "incident_key": alert.incident_key,
            }

        if self.script_path is None or not self.script_path.is_file():
            raise FileNotFoundError(
                f"TickTick client script not found for live execution: {self.script_path}"
            )

        proc = subprocess.run(
            [
                self.python_executable,
                str(self.script_path),
                "--create-task",
                "--project-name",
                self.project_name,
                "--title",
                title,
                "--content",
                content,
                "--priority",
                str(self.priority),
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"TickTick adapter failed with exit code {proc.returncode}."
            )

        parsed_response: Any = proc.stdout.strip()
        try:
            parsed_response = json.loads(proc.stdout)
        except json.JSONDecodeError:
            pass

        return {
            "adapter": self.name,
            "dry_run": False,
            "status": "DISPATCHED",
            "project_name": self.project_name,
            "priority": self.priority,
            "incident_key": alert.incident_key,
            "response": parsed_response,
        }


class NotifyWebhookAdapter:
    """Optional decoupled bridge invoking C.O.R.E. notify.py via subprocess."""

    name = "notify_webhook"

    def __init__(
        self,
        *,
        script_path: Path | None = None,
        python_executable: str = sys.executable,
    ) -> None:
        self.script_path = Path(script_path).resolve() if script_path is not None else None
        self.python_executable = python_executable

    def send(
        self,
        alert: AlertCandidate,
        runbook_md: str,
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Preview webhook notification in dry-run or invoke notify.py live."""
        del runbook_md
        message = (
            f"[{alert.severity}] {alert.cohort_key} ({alert.incident_key}): "
            f"{alert.summary} | SLA30d={alert.sla_compliance_pct_30d:.2f}%"
        )
        if dry_run:
            return {
                "adapter": self.name,
                "dry_run": True,
                "status": "DRY_RUN_PLANNED",
                "message": message,
                "incident_key": alert.incident_key,
            }

        if self.script_path is None or not self.script_path.is_file():
            raise FileNotFoundError(
                f"Notify script not found for live execution: {self.script_path}"
            )

        proc = subprocess.run(
            [self.python_executable, str(self.script_path), "--msg", message],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Notify webhook adapter failed with exit code {proc.returncode}."
            )

        return {
            "adapter": self.name,
            "dry_run": False,
            "status": "DISPATCHED",
            "incident_key": alert.incident_key,
            "output": proc.stdout.strip(),
        }
