"""Phase 06 tests: 4-interface parity across SDK, ost CLI, FastAPI, and ost-mcp."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ops_sla_telemetry import OpsSlaTelemetry
from ops_sla_telemetry.api import create_app
from ops_sla_telemetry.cli.main import main as cli_main
from ops_sla_telemetry.config import LakehouseSettings
from ops_sla_telemetry.dispatcher import HumanConfirmationError
from ops_sla_telemetry.mcp.server import OpsSlaMcpDispatcher

REPO_ROOT = Path(__file__).resolve().parents[1]


def _unwrap_mcp(resp: dict[str, Any]) -> dict[str, Any]:
    text = resp["result"]["content"][0]["text"]
    assert text.startswith("<untrusted_data>\n")
    assert text.endswith("\n</untrusted_data>")
    inner = text.removeprefix("<untrusted_data>\n").removesuffix("\n</untrusted_data>")
    return json.loads(inner)


def _run_cli_json(
    args: list[str],
    capsys: pytest.CaptureFixture[str],
    *,
    expected_code: int = 0,
) -> dict[str, Any]:
    capsys.readouterr()
    code = cli_main(args)
    captured = capsys.readouterr()
    assert code == expected_code, f"CLI exited {code}: {captured.err}"
    return json.loads(captured.out) if captured.out.strip() else {}


def test_four_interface_parity_and_confirmation_gate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Assert identical payload parity across SDK, CLI, FastAPI, and MCP."""
    data_dir = tmp_path / "data"
    duckdb_path = data_dir / "gold" / "ops_sla_telemetry.duckdb"
    ledger_path = data_dir / "bronze" / "dispatch_ledger.sqlite"
    dbt_target_path = tmp_path / "dbt_target"

    settings = LakehouseSettings(repo_root=REPO_ROOT, data_dir=data_dir)
    sdk = OpsSlaTelemetry(
        settings=settings,
        duckdb_path=duckdb_path,
        ledger_db_path=ledger_path,
        dbt_target_path=dbt_target_path,
    )

    ingest_res = sdk.ingest(seed=42, order_count=500, run_dbt=True, batch_id="parity_500")
    assert ingest_res["order_count"] == 500
    assert ingest_res["dbt_executed"] is True

    client = TestClient(create_app(sdk))
    mcp = OpsSlaMcpDispatcher(sdk)

    # MCP protocol handshake & tool discovery
    init_resp = mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init_resp["result"]["serverInfo"]["name"] == "ops-sla-telemetry-mcp"
    ping_resp = mcp.handle_request({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert ping_resp["result"] == {}
    list_resp = mcp.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tool_names = {tool["name"] for tool in list_resp["result"]["tools"]}
    assert tool_names == {
        "ost_ingest",
        "ost_sla_status",
        "ost_at_risk",
        "ost_team_capacity",
        "ost_core_health",
        "ost_sync_ticktick",
    }

    cli_base = [
        "--data-dir",
        str(data_dir),
        "--duckdb-path",
        str(duckdb_path),
        "--ledger-db-path",
        str(ledger_path),
        "--dbt-target-path",
        str(dbt_target_path),
    ]

    # 1. sla-status parity
    sdk_sla = sdk.sla_status()
    cli_sla = _run_cli_json([*cli_base, "sla-status", "--json"], capsys)
    api_sla_resp = client.get("/api/v1/sla-status")
    assert api_sla_resp.status_code == 200
    api_sla = api_sla_resp.json()
    mcp_sla = _unwrap_mcp(
        mcp.handle_request(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "ost_sla_status"}}
        )
    )
    assert sdk_sla == cli_sla == api_sla == mcp_sla
    assert sdk_sla["global_summary"]["cohort_key"] == "ALL|ALL"

    # 2. at-risk parity
    sdk_risk = sdk.at_risk(limit=5)
    cli_risk = _run_cli_json([*cli_base, "at-risk", "--limit", "5", "--json"], capsys)
    api_risk_resp = client.get("/api/v1/at-risk", params={"limit": 5})
    assert api_risk_resp.status_code == 200
    api_risk = api_risk_resp.json()
    mcp_risk = _unwrap_mcp(
        mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "ost_at_risk", "arguments": {"limit": 5}},
            }
        )
    )
    assert sdk_risk == cli_risk == api_risk == mcp_risk

    # 3. team-capacity parity
    sdk_cap = sdk.team_capacity(limit=10)
    cli_cap = _run_cli_json([*cli_base, "team-capacity", "--limit", "10", "--json"], capsys)
    api_cap_resp = client.get("/api/v1/team-capacity", params={"limit": 10})
    assert api_cap_resp.status_code == 200
    api_cap = api_cap_resp.json()
    mcp_cap = _unwrap_mcp(
        mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "ost_team_capacity", "arguments": {"limit": 10}},
            }
        )
    )
    assert sdk_cap == cli_cap == api_cap == mcp_cap
    assert len(sdk_cap["teams"]) == 10

    # 4. core-health parity (against synthetic core & studies tree)
    core_root = tmp_path / "core_tree"
    studies_root = tmp_path / "studies_tree"
    (core_root / ".agents" / "state").mkdir(parents=True, exist_ok=True)
    (studies_root / ".agents" / "memory").mkdir(parents=True, exist_ok=True)
    (core_root / ".agents" / "state" / "career_pipeline.json").write_text(
        json.dumps(
            {
                "candidaturas": [
                    {
                        "id": "JOB-1",
                        "empresa": "CONFIDENTIAL_CORP",
                        "data_aplicacao": "2026-11-10",
                        "status": "Aplicado",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    sdk_core = sdk.core_health(core_root=core_root, studies_root=studies_root)
    cli_core = _run_cli_json(
        [
            *cli_base,
            "core-health",
            "--core-root",
            str(core_root),
            "--studies-root",
            str(studies_root),
            "--json",
        ],
        capsys,
    )
    api_core_resp = client.get(
        "/api/v1/core-health",
        params={"core_root": str(core_root), "studies_root": str(studies_root)},
    )
    assert api_core_resp.status_code == 200
    api_core = api_core_resp.json()
    mcp_core = _unwrap_mcp(
        mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "ost_core_health",
                    "arguments": {
                        "core_root": str(core_root),
                        "studies_root": str(studies_root),
                    },
                },
            }
        )
    )
    assert sdk_core == cli_core == api_core == mcp_core
    assert "CONFIDENTIAL_CORP" not in json.dumps(sdk_core)

    # 5. sync-ticktick dry-run parity + unconfirmed execution refusal across all 4 interfaces
    sdk_sync = sdk.sync_ticktick(window_bucket="parity-test")
    cli_sync = _run_cli_json(
        [*cli_base, "sync-ticktick", "--window-bucket", "parity-test", "--json"],
        capsys,
    )
    api_sync_resp = client.post(
        "/api/v1/sync-ticktick",
        json={"window_bucket": "parity-test"},
    )
    assert api_sync_resp.status_code == 200
    api_sync = api_sync_resp.json()
    mcp_sync = _unwrap_mcp(
        mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "ost_sync_ticktick",
                    "arguments": {"window_bucket": "parity-test"},
                },
            }
        )
    )
    assert sdk_sync == cli_sync == api_sync == mcp_sync
    assert sdk_sync["dry_run"] is True

    # Confirm refusal without human confirmation across all 4 interfaces
    with pytest.raises(HumanConfirmationError):
        sdk.sync_ticktick(execute=True, confirm=False)

    cli_fail_code = cli_main([*cli_base, "sync-ticktick", "--execute", "--json"])
    assert cli_fail_code == 2

    api_fail = client.post("/api/v1/sync-ticktick", json={"execute": True, "confirm": False})
    assert api_fail.status_code == 403

    mcp_fail = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "ost_sync_ticktick",
                "arguments": {"execute": True, "confirm": False},
            },
        }
    )
    assert mcp_fail["error"]["code"] == -32003
