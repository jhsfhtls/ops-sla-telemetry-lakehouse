"""Validate state/project_progress.json and deterministically render JORNADA_IMPLEMENTACAO.md."""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROGRESS_PATH = REPO_ROOT / "state" / "project_progress.json"
DEFAULT_REGISTRY_PATH = REPO_ROOT / "catalog" / "events_and_slas.yaml"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "JORNADA_IMPLEMENTACAO.md"

VALID_PHASE_STATUSES = {"COMPLETED", "IN_PROGRESS", "PENDING"}


def load_json(path: Path) -> dict[str, Any]:
    """Load JSON progress state strictly with UTF-8."""
    if not path.is_file():
        raise FileNotFoundError(f"Progress file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Progress JSON root must be an object.")
    return data


def load_yaml(path: Path) -> dict[str, Any]:
    """Load catalog YAML strictly with UTF-8."""
    if not path.is_file():
        raise FileNotFoundError(f"Catalog file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Catalog YAML root must be a mapping.")
    return data


def validate_progress(data: dict[str, Any]) -> None:
    """Validate project_progress.json schema and phase status integrity."""
    for key in ("schema_version", "project_name", "active_phase", "overall_status", "last_updated_date", "phases"):
        if key not in data:
            raise ValueError(f"Missing required progress key: '{key}'")
    phases = data["phases"]
    if not isinstance(phases, list) or not phases:
        raise ValueError("'phases' must be a non-empty list.")
    seen_ids: set[str] = set()
    for phase in phases:
        pid = str(phase["phase_id"])
        if pid in seen_ids:
            raise ValueError(f"Duplicate phase_id '{pid}'")
        seen_ids.add(pid)
        if phase["status"] not in VALID_PHASE_STATUSES:
            raise ValueError(f"Invalid status '{phase['status']}' in phase '{pid}'")


def render_markdown(progress: dict[str, Any], registry: dict[str, Any]) -> str:
    """Render deterministic JORNADA_IMPLEMENTACAO.md without wall-clock timestamps."""
    phases = progress["phases"]
    completed = sum(1 for p in phases if p["status"] == "COMPLETED")
    total = len(phases)
    pct = (completed / total) * 100.0
    event_count = len(registry.get("event_types", []))
    policy_count = len(registry.get("sla_policies", []))

    status_badge = {
        "COMPLETED": "COMPLETED",
        "IN_PROGRESS": "IN_PROGRESS",
        "PENDING": "PENDING",
    }

    lines: list[str] = [
        "# Implementation Journey — Ops SLA Telemetry Lakehouse",
        "",
        "> Deterministic Single Source of Truth generated from `state/project_progress.json` and `catalog/events_and_slas.yaml`.",
        "",
        "## 1. Executive Status",
        "",
        f"- **Active Phase:** `{progress['active_phase']}`",
        f"- **Overall Governance Status:** `{progress['overall_status']}`",
        f"- **Phase Completion:** **{completed} / {total} ({pct:.1f}%)**",
        f"- **Cataloged Contracts:** **{event_count} event types**, **{policy_count} SLA policies**",
        f"- **Last Updated Date:** `{progress['last_updated_date']}`",
        "",
        "## 2. Phase Roadmap & Acceptance Gates",
        "",
        "| Phase | Milestone | Status | Acceptance Gate | Key Deliverables |",
        "| :---: | :--- | :---: | :--- | :--- |",
    ]

    for phase in phases:
        deliverables_str = "<br/>".join(f"- {item}" for item in phase.get("deliverables", []))
        lines.append(
            f"| **Phase {phase['phase_id']}** | {phase['name']} | `{status_badge[phase['status']]}` | "
            f"{phase['gate']} | {deliverables_str} |"
        )

    lines.extend([
        "",
        "## 3. Verification Commands",
        "",
        "```bash",
        "python scripts/render_progress.py --check",
        "python scripts/render_catalog.py --check",
        "ruff check .",
        "pytest -m \"not live\"",
        "```",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    """CLI entrypoint for rendering or checking JORNADA_IMPLEMENTACAO.md."""
    parser = argparse.ArgumentParser(description="Render or verify JORNADA_IMPLEMENTACAO.md.")
    parser.add_argument("--check", action="store_true", help="Verify JORNADA_IMPLEMENTACAO.md is in sync.")
    parser.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS_PATH, help="Path to project_progress.json.")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH, help="Path to events_and_slas.yaml.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Path to JORNADA_IMPLEMENTACAO.md.")
    args = parser.parse_args()

    try:
        progress_data = load_json(args.progress)
        validate_progress(progress_data)
        registry_data = load_yaml(args.registry)
        rendered = render_markdown(progress_data, registry_data)
    except Exception as exc:
        sys.stderr.write(f"[ERROR] Progress rendering failed: {exc}\n")
        return 2

    if args.check:
        if not args.output.is_file():
            sys.stderr.write(f"[CHECK FAILED] Output file does not exist: {args.output}\n")
            return 1
        current = args.output.read_text(encoding="utf-8")
        if current != rendered:
            sys.stderr.write(f"[CHECK FAILED] {args.output} is out of sync with {args.progress}.\n")
            diff = difflib.unified_diff(
                current.splitlines(),
                rendered.splitlines(),
                fromfile=str(args.output),
                tofile="rendered_progress",
                lineterm="",
                n=3,
            )
            for line in list(diff)[:20]:
                sys.stderr.write(f"{line}\n")
            return 1
        print(f"[CHECK PASSED] {args.output} is synchronized with {args.progress}.")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered)
    print(f"[RENDER SUCCESS] {args.output} generated successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
