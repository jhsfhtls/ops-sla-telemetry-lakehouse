"""Validate catalog/events_and_slas.yaml and deterministically render CATALOG.md."""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY_PATH = REPO_ROOT / "catalog" / "events_and_slas.yaml"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "CATALOG.md"

VALID_DOMAINS = {"FIELD_SERVICE", "CORE_OBSERVABILITY"}
VALID_PRIVACY_TIERS = {"SYNTHETIC_PUBLIC", "LOCAL_ALLOWLIST_ONLY"}
VALID_CALENDARS = {"24x7", "BUSINESS_HOURS"}


def load_registry(path: Path) -> dict[str, Any]:
    """Load YAML catalog strictly with UTF-8 encoding."""
    if not path.is_file():
        raise FileNotFoundError(f"Catalog registry not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Catalog YAML root must be a mapping.")
    return data


def validate_registry(data: dict[str, Any]) -> None:
    """Validate event types, SLA policies, SLO defaults, and burn-rate windows."""
    for required_section in ("schema_version", "slo_defaults", "event_types", "sla_policies", "burn_rate_windows"):
        if required_section not in data:
            raise ValueError(f"Missing required catalog section: '{required_section}'")

    slo_defaults = data["slo_defaults"]
    if not (0.0 < float(slo_defaults["global_slo"]) < 1.0):
        raise ValueError("slo_defaults.global_slo must be between 0 and 1.")

    seen_events: set[str] = set()
    for entry in data["event_types"]:
        event_type = str(entry["event_type"]).strip()
        if not event_type or event_type in seen_events:
            raise ValueError(f"Duplicate or empty event_type: '{event_type}'")
        seen_events.add(event_type)
        if entry["domain"] not in VALID_DOMAINS:
            raise ValueError(f"Invalid domain '{entry['domain']}' on event '{event_type}'")
        if entry["privacy_tier"] not in VALID_PRIVACY_TIERS:
            raise ValueError(f"Invalid privacy_tier '{entry['privacy_tier']}' on event '{event_type}'")
        if not entry.get("required_fields"):
            raise ValueError(f"Event '{event_type}' must declare required_fields.")

    seen_policies: set[str] = set()
    for policy in data["sla_policies"]:
        policy_id = str(policy["policy_id"]).strip()
        if not policy_id or policy_id in seen_policies:
            raise ValueError(f"Duplicate or empty policy_id: '{policy_id}'")
        seen_policies.add(policy_id)
        if policy["domain"] not in VALID_DOMAINS:
            raise ValueError(f"Invalid domain '{policy['domain']}' on policy '{policy_id}'")
        if policy["calendar"] not in VALID_CALENDARS:
            raise ValueError(f"Invalid calendar '{policy['calendar']}' on policy '{policy_id}'")
        if int(policy["target_hours"]) <= 0:
            raise ValueError(f"Policy '{policy_id}' target_hours must be positive.")

    if not data["burn_rate_windows"]:
        raise ValueError("burn_rate_windows must contain at least one severity rule.")


def render_markdown(data: dict[str, Any]) -> str:
    """Render deterministic CATALOG.md without wall-clock timestamps."""
    slo = data["slo_defaults"]
    events = data["event_types"]
    policies = data["sla_policies"]
    windows = data["burn_rate_windows"]

    lines: list[str] = [
        "# Event & SLA Policy Catalog",
        "",
        "> Auto-generated deterministically from `catalog/events_and_slas.yaml` (`scripts/render_catalog.py`).",
        "",
        "## 1. Global SLO Defaults",
        "",
        "| Parameter | Value |",
        "| :--- | :--- |",
        f"| **Schema Version** | `{data['schema_version']}` |",
        f"| **Global SLO Target** | `{float(slo['global_slo']) * 100:.1f}%` |",
        f"| **Error Budget** | `{float(slo['error_budget']) * 100:.1f}%` |",
        f"| **Operational Timezone** | `{slo['timezone']}` |",
        f"| **Business Hours Calendar** | `{slo['business_hours']}` |",
        f"| **Burn-Rate Baseline Window** | `{slo['burn_rate_baseline_window_hours']}h` |",
        "",
        f"## 2. Canonical Event Types ({len(events)})",
        "",
        "| Event Type | Domain | Privacy Tier | Terminal | Required Fields | Description |",
        "| :--- | :--- | :--- | :---: | :--- | :--- |",
    ]

    for entry in events:
        req = ", ".join(f"`{col}`" for col in entry["required_fields"])
        terminal = "Yes" if entry.get("terminal") else "No"
        lines.append(
            f"| `{entry['event_type']}` | `{entry['domain']}` | `{entry['privacy_tier']}` | "
            f"{terminal} | {req} | {entry['description']} |"
        )

    lines.extend([
        "",
        f"## 3. SLA Policies ({len(policies)})",
        "",
        "| Policy ID | Domain | Priority | Calendar | Target (h) | At-Risk Threshold (h) | Description |",
        "| :--- | :--- | :---: | :--- | :---: | :---: | :--- |",
    ])

    for policy in policies:
        lines.append(
            f"| `{policy['policy_id']}` | `{policy['domain']}` | `{policy['priority']}` | "
            f"`{policy['calendar']}` | `{policy['target_hours']}h` | "
            f"`{policy['at_risk_threshold_hours']}h` | {policy['description']} |"
        )

    lines.extend([
        "",
        f"## 4. Multi-Window Error-Budget Burn-Rate Matrix ({len(windows)})",
        "",
        "| Severity | Short Window | Long Window | Burn-Rate Threshold | Automated Action |",
        "| :---: | :---: | :---: | :---: | :--- |",
    ])

    for win in windows:
        lines.append(
            f"| `{win['severity']}` | `{win['short_window_hours']}h` | `{win['long_window_hours']}h` | "
            f"`{win['burn_rate_threshold']}x` | {win['action']} |"
        )

    lines.extend([
        "",
        "## 5. Sync Verification",
        "",
        "```bash",
        "python scripts/render_catalog.py --check",
        "python scripts/render_catalog.py",
        "```",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    """CLI entrypoint for rendering or checking CATALOG.md."""
    parser = argparse.ArgumentParser(description="Render or verify CATALOG.md from catalog YAML.")
    parser.add_argument("--check", action="store_true", help="Verify CATALOG.md matches rendered YAML output.")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH, help="Path to events_and_slas.yaml.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Path to CATALOG.md.")
    args = parser.parse_args()

    try:
        data = load_registry(args.registry)
        validate_registry(data)
        rendered = render_markdown(data)
    except Exception as exc:
        sys.stderr.write(f"[ERROR] Catalog validation failed: {exc}\n")
        return 2

    if args.check:
        if not args.output.is_file():
            sys.stderr.write(f"[CHECK FAILED] Output file does not exist: {args.output}\n")
            return 1
        current = args.output.read_text(encoding="utf-8")
        if current != rendered:
            sys.stderr.write(f"[CHECK FAILED] {args.output} is out of sync with {args.registry}.\n")
            diff = difflib.unified_diff(
                current.splitlines(),
                rendered.splitlines(),
                fromfile=str(args.output),
                tofile="rendered_catalog",
                lineterm="",
                n=3,
            )
            for line in list(diff)[:20]:
                sys.stderr.write(f"{line}\n")
            return 1
        print(f"[CHECK PASSED] {args.output} is synchronized with {args.registry}.")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered)
    print(f"[RENDER SUCCESS] {args.output} generated ({len(data['event_types'])} events, {len(data['sla_policies'])} SLAs).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
