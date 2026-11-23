"""JSON-RPC 2.0 stdio MCP server (`ost-mcp`) with `<untrusted_data>` wrapping."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ops_sla_telemetry import OpsSlaTelemetry, __version__
from ops_sla_telemetry.collectors import PrivacyBoundaryError
from ops_sla_telemetry.dispatcher import HumanConfirmationError

MCP_PROTOCOL_VERSION = "2024-11-05"


def wrap_untrusted_data(payload: Any) -> str:
    """Wrap serialized telemetry outputs in `<untrusted_data>` tags (OWASP LLM01)."""
    serialized = json.dumps(payload, indent=2, ensure_ascii=False)
    return f"<untrusted_data>\n{serialized}\n</untrusted_data>"


class OpsSlaMcpDispatcher:
    """In-memory testable JSON-RPC 2.0 dispatcher for `ost-mcp`."""

    def __init__(self, sdk: OpsSlaTelemetry | None = None) -> None:
        self.sdk = sdk or OpsSlaTelemetry()
        self._tool_specs: list[dict[str, Any]] = [
            {
                "name": "ost_ingest",
                "description": "Generate deterministic field-service events, run CDC sync, and execute dbt build.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "seed": {"type": "integer"},
                        "order_count": {"type": "integer"},
                        "run_dbt": {"type": "boolean"},
                        "batch_id": {"type": "string"},
                    },
                },
            },
            {
                "name": "ost_sla_status",
                "description": "Query 99% SLO compliance, error budget, multi-window burn rates, and MTTR/MTBF.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "region_id": {"type": "string"},
                        "priority": {"type": "string"},
                    },
                },
            },
            {
                "name": "ost_at_risk",
                "description": "List open field orders approaching SLA breach (< 24h remaining).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer"},
                        "region_id": {"type": "string"},
                        "priority": {"type": "string"},
                    },
                },
            },
            {
                "name": "ost_team_capacity",
                "description": "Query team productivity, SLA attainment, MTTR, and at-risk WIP.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer"},
                        "region_id": {"type": "string"},
                        "team_id": {"type": "string"},
                    },
                },
            },
            {
                "name": "ost_core_health",
                "description": "Read sanitized multi-repo C.O.R.E. telemetry using closed field allowlists.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "core_root": {"type": "string"},
                        "studies_root": {"type": "string"},
                    },
                },
            },
            {
                "name": "ost_sync_ticktick",
                "description": "Evaluate SLA alerts and preview/dispatch tasks (dry-run default; requires execute+confirm).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "execute": {"type": "boolean"},
                        "confirm": {"type": "boolean"},
                        "max_alerts": {"type": "integer"},
                        "project_name": {"type": "string"},
                        "priority": {"type": "integer"},
                        "window_bucket": {"type": "string"},
                    },
                },
            },
        ]

    def _dispatch_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "ost_ingest": lambda a: self.sdk.ingest(
                seed=int(a.get("seed", 42)),
                order_count=int(a.get("order_count", 50_000)),
                run_dbt=bool(a.get("run_dbt", True)),
                batch_id=a.get("batch_id"),
            ),
            "ost_sla_status": lambda a: self.sdk.sla_status(
                region_id=a.get("region_id"),
                priority=a.get("priority"),
            ),
            "ost_at_risk": lambda a: self.sdk.at_risk(
                limit=int(a.get("limit", 25)),
                region_id=a.get("region_id"),
                priority=a.get("priority"),
            ),
            "ost_team_capacity": lambda a: self.sdk.team_capacity(
                limit=int(a.get("limit", 25)),
                region_id=a.get("region_id"),
                team_id=a.get("team_id"),
            ),
            "ost_core_health": lambda a: self.sdk.core_health(
                core_root=Path(a["core_root"]) if a.get("core_root") else None,
                studies_root=Path(a["studies_root"]) if a.get("studies_root") else None,
                write_snapshot=False,
            ),
            "ost_sync_ticktick": lambda a: self.sdk.sync_ticktick(
                execute=bool(a.get("execute", False)),
                confirm=bool(a.get("confirm", False)),
                max_alerts=int(a.get("max_alerts", 10)),
                project_name=str(a.get("project_name", "00_INBOX_AGENTES")),
                priority=int(a.get("priority", 5)),
                window_bucket=a.get("window_bucket"),
            ),
        }
        handler = handlers.get(tool_name)
        if handler is None:
            raise KeyError(f"Unknown MCP tool: {tool_name}")
        return handler(args)

    def handle_request(self, req: dict[str, Any]) -> dict[str, Any]:
        """Process a single JSON-RPC 2.0 MCP request dictionary."""
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}

        if not method:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32600, "message": "Invalid Request: missing method"},
            }

        try:
            if method == "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": "ops-sla-telemetry-mcp",
                            "version": __version__,
                        },
                    },
                }

            if method == "ping":
                return {"jsonrpc": "2.0", "id": req_id, "result": {}}

            if method == "tools/list":
                return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": self._tool_specs}}

            if method == "tools/call":
                tool_name = str(params.get("name", ""))
                tool_args = params.get("arguments") or {}
                result = self._dispatch_tool(tool_name, tool_args)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": wrap_untrusted_data(result)}],
                    },
                }

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        except (HumanConfirmationError, PrivacyBoundaryError) as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32003, "message": str(exc)},
            }
        except (KeyError, ValueError, FileNotFoundError) as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": str(exc)},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": str(exc)},
            }


def main() -> int:
    """Run newline-delimited JSON-RPC 2.0 stdio loop for `ost-mcp`."""
    dispatcher = OpsSlaMcpDispatcher()
    for raw_line in sys.stdin:
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            req = json.loads(stripped)
        except json.JSONDecodeError as exc:
            resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
        else:
            resp = dispatcher.handle_request(req)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
