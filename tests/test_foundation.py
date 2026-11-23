"""Phase 01 foundation verification tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.lint_dbt_docs import evaluate_manifest
from scripts.render_catalog import load_registry, validate_registry

from ops_sla_telemetry import __version__
from ops_sla_telemetry.cli.main import main as cli_main

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_catalog_yaml_integrity() -> None:
    """Ensure events_and_slas.yaml defines all expected domains, events, and SLAs."""
    registry = load_registry(REPO_ROOT / "catalog" / "events_and_slas.yaml")
    validate_registry(registry)

    event_names = {entry["event_type"] for entry in registry["event_types"]}
    expected_field_events = {
        "CREATED",
        "ASSIGNED",
        "IN_TRANSIT",
        "IN_EXECUTION",
        "ON_HOLD",
        "COMPLETED",
        "SLA_BREACHED",
    }
    assert expected_field_events.issubset(event_names)
    assert registry["slo_defaults"]["global_slo"] == 0.99


def test_render_catalog_check_passes() -> None:
    """CATALOG.md must remain synchronized with catalog/events_and_slas.yaml."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "render_catalog.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_render_progress_check_passes() -> None:
    """JORNADA_IMPLEMENTACAO.md must remain synchronized with state/project_progress.json."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "render_progress.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_gitignore_has_satellite_shield_and_data_isolation() -> None:
    """Verify .gitignore protects secrets, lakehouse outputs, and AI satellite files."""
    content = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for marker in (
        "AI SATELLITE SHIELD",
        ".env",
        ".venv/",
        "data/*",
        "!data/reference/",
        ".logs/",
    ):
        assert marker in content


def test_requirements_strict_pinning() -> None:
    """Verify every dependency in requirements*.txt uses exact == pinning."""
    for req_file in ("requirements.txt", "requirements-dev.txt"):
        lines = (REPO_ROOT / req_file).read_text(encoding="utf-8").splitlines()
        non_empty = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
        assert non_empty, f"{req_file} must not be empty"
        for line in non_empty:
            assert "==" in line, f"Missing exact == pin in {req_file}: {line}"
            assert not any(op in line for op in (">=", "<=", "~="))


def test_cli_status_and_version(capsys: object) -> None:
    """Verify CLI status subcommand returns dry-run default and foundation status."""
    exit_code = cli_main(["status", "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert payload["version"] == __version__
    assert payload["slo_target"] == 0.99
    assert payload["dispatch_default_mode"] == "dry-run"


def test_lint_dbt_docs_detects_documented_and_undocumented_columns(tmp_path: Path) -> None:
    """Verify lint_dbt_docs passes at 100% coverage and fails on missing column docs."""
    manifest_ok = tmp_path / "manifest_ok.json"
    manifest_ok.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.ops_sla_telemetry.stg_service_events": {
                        "name": "stg_service_events",
                        "description": "Staged field events.",
                        "columns": {"event_id": {"description": "Unique event identifier."}},
                    }
                },
                "sources": {},
            }
        ),
        encoding="utf-8",
    )
    code_ok, bad_models, bad_cols = evaluate_manifest(manifest_ok)
    assert code_ok == 0
    assert bad_models == []
    assert bad_cols == []

    manifest_fail = tmp_path / "manifest_fail.json"
    manifest_fail.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.ops_sla_telemetry.stg_service_events": {
                        "name": "stg_service_events",
                        "description": "Staged field events.",
                        "columns": {"event_id": {"description": ""}},
                    }
                },
                "sources": {},
            }
        ),
        encoding="utf-8",
    )
    code_fail, _, bad_cols_fail = evaluate_manifest(manifest_fail)
    assert code_fail == 1
    assert bad_cols_fail == ["stg_service_events.event_id"]
