"""Command-line entrypoint (`ost`) for ops-sla-telemetry-lakehouse."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ops_sla_telemetry import OpsSlaTelemetry, __version__
from ops_sla_telemetry.collectors import PrivacyBoundaryError
from ops_sla_telemetry.config import LakehouseSettings
from ops_sla_telemetry.dispatcher import HumanConfirmationError


def _add_shared_flags(parser: argparse.ArgumentParser, *, suppress_defaults: bool = False) -> None:
    default_unset = argparse.SUPPRESS if suppress_defaults else None
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=default_unset,
        help="Override lakehouse data directory",
    )
    parser.add_argument(
        "--duckdb-path",
        type=Path,
        default=default_unset,
        help="Override Gold DuckDB warehouse file path",
    )
    parser.add_argument(
        "--ledger-db-path",
        type=Path,
        default=default_unset,
        help="Override idempotent dispatch ledger SQLite path",
    )
    parser.add_argument(
        "--dbt-target-path",
        type=Path,
        default=default_unset,
        help="Override dbt target artifact directory",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        default=argparse.SUPPRESS if suppress_defaults else False,
        help="Emit output as formatted JSON",
    )


def build_parser() -> argparse.ArgumentParser:
    """Create the root CLI argument parser with full 4-interface command parity."""
    parser = argparse.ArgumentParser(
        prog="ost",
        description="Operational SLA Telemetry Lakehouse CLI",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _add_shared_flags(parser, suppress_defaults=False)

    subparsers = parser.add_subparsers(dest="command")

    status_p = subparsers.add_parser("status", help="Show lakehouse foundation readiness")
    _add_shared_flags(status_p, suppress_defaults=True)

    ingest_p = subparsers.add_parser(
        "ingest", help="Generate deterministic events, sync CDC Silver Parquet, and run dbt build"
    )
    _add_shared_flags(ingest_p, suppress_defaults=True)
    ingest_p.add_argument("--seed", type=int, default=42, help="Deterministic simulator seed")
    ingest_p.add_argument("--orders", type=int, default=50_000, help="Number of simulated orders")
    ingest_p.add_argument("--skip-dbt", action="store_true", help="Skip dbt build after CDC sync")
    ingest_p.add_argument("--batch-id", default=None, help="Optional explicit Bronze batch_id")

    sla_p = subparsers.add_parser("sla-status", help="Query 99% SLO burn-rate & MTTR/MTBF marts")
    _add_shared_flags(sla_p, suppress_defaults=True)
    sla_p.add_argument("--region", default=None, help="Filter by region_id")
    sla_p.add_argument("--priority", default=None, help="Filter by priority (P1, P2, P3)")

    risk_p = subparsers.add_parser("at-risk", help="List open orders approaching SLA breach (< 24h)")
    _add_shared_flags(risk_p, suppress_defaults=True)
    risk_p.add_argument("--limit", type=int, default=25, help="Max open orders returned")
    risk_p.add_argument("--region", default=None, help="Filter by region_id")
    risk_p.add_argument("--priority", default=None, help="Filter by priority")

    cap_p = subparsers.add_parser("team-capacity", help="Inspect team capacity, MTTR, and compliance")
    _add_shared_flags(cap_p, suppress_defaults=True)
    cap_p.add_argument("--limit", type=int, default=25, help="Max teams returned")
    cap_p.add_argument("--region", default=None, help="Filter by region_id")
    cap_p.add_argument("--team", default=None, help="Filter by team_id")

    core_p = subparsers.add_parser(
        "core-health", help="Collect sanitized read-only C.O.R.E. multi-repo telemetry"
    )
    _add_shared_flags(core_p, suppress_defaults=True)
    core_p.add_argument("--core-root", type=Path, default=None, help="Path to local core repo")
    core_p.add_argument("--studies-root", type=Path, default=None, help="Path to local studies repo")
    core_p.add_argument(
        "--write-snapshot",
        action="store_true",
        help="Persist sanitized snapshot outside public repo",
    )
    core_p.add_argument("--output-dir", type=Path, default=None, help="Snapshot output directory")

    sync_p = subparsers.add_parser(
        "sync-ticktick", help="Evaluate alerts and dispatch to TickTick (dry-run by default)"
    )
    _add_shared_flags(sync_p, suppress_defaults=True)
    sync_p.add_argument("--execute", action="store_true", help="Enable live external dispatch")
    sync_p.add_argument(
        "--confirm",
        action="store_true",
        help="Explicit human confirmation required alongside --execute",
    )
    sync_p.add_argument("--max-alerts", type=int, default=10, help="Max alerts per execution")
    sync_p.add_argument(
        "--project-name",
        default="00_INBOX_AGENTES",
        help="Target TickTick project (POP_B_179)",
    )
    sync_p.add_argument("--priority", type=int, default=5, help="TickTick priority (5 = High)")
    sync_p.add_argument("--ticktick-script", type=Path, default=None, help="Path to ticktick_client.py")
    sync_p.add_argument("--notify-script", type=Path, default=None, help="Path to notify.py")
    sync_p.add_argument("--window-bucket", default=None, help="Deterministic incident window bucket")

    return parser


def _emit(payload: dict[str, Any], *, as_json: bool, summary: str) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(summary)


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the `ost` CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "status":
        payload = {
            "service": "ops-sla-telemetry-lakehouse",
            "version": __version__,
            "status": "FOUNDATION_READY",
            "slo_target": 0.99,
            "dispatch_default_mode": "dry-run",
        }
        _emit(
            payload,
            as_json=args.as_json,
            summary=(
                f"ops-sla-telemetry-lakehouse v{__version__} "
                f"[status={payload['status']}, slo={payload['slo_target']:.0%}, "
                f"dispatch={payload['dispatch_default_mode']}]"
            ),
        )
        return 0

    settings = LakehouseSettings(data_dir=args.data_dir) if args.data_dir else LakehouseSettings()
    sdk = OpsSlaTelemetry(
        settings=settings,
        duckdb_path=args.duckdb_path,
        ledger_db_path=args.ledger_db_path,
        dbt_target_path=args.dbt_target_path,
    )

    try:
        if args.command == "ingest":
            res = sdk.ingest(
                seed=args.seed,
                order_count=args.orders,
                run_dbt=not args.skip_dbt,
                batch_id=args.batch_id,
            )
            _emit(
                res,
                as_json=args.as_json,
                summary=(
                    f"Ingested {res['events_generated']} events ({res['order_count']} orders, "
                    f"inserted={res['inserted_count']}, synced={res['rows_synced']}, "
                    f"dbt={res['dbt_executed']})."
                ),
            )
            return 0

        if args.command == "sla-status":
            res = sdk.sla_status(region_id=args.region, priority=args.priority)
            g = res["global_summary"] or {}
            _emit(
                res,
                as_json=args.as_json,
                summary=(
                    f"SLA 30d={g.get('sla_compliance_pct_30d', 'N/A')}% | "
                    f"Severity={g.get('alert_severity', 'N/A')} | "
                    f"MTTR={g.get('mttr_hours_30d', 'N/A')}h | Cohorts={res['count']}"
                ),
            )
            return 0

        if args.command == "at-risk":
            res = sdk.at_risk(limit=args.limit, region_id=args.region, priority=args.priority)
            _emit(
                res,
                as_json=args.as_json,
                summary=f"Open at-risk/breached orders: {res['count']} (limit={res['limit']}).",
            )
            return 0

        if args.command == "team-capacity":
            res = sdk.team_capacity(limit=args.limit, region_id=args.region, team_id=args.team)
            _emit(
                res,
                as_json=args.as_json,
                summary=f"Team capacity records: {res['count']} (limit={res['limit']}).",
            )
            return 0

        if args.command == "core-health":
            res = sdk.core_health(
                core_root=args.core_root,
                studies_root=args.studies_root,
                write_snapshot=args.write_snapshot,
                output_dir=args.output_dir,
            )
            _emit(
                res,
                as_json=args.as_json,
                summary=f"C.O.R.E. health summary ({res['collected_date']}): {res['summary']}",
            )
            return 0

        if args.command == "sync-ticktick":
            res = sdk.sync_ticktick(
                execute=args.execute,
                confirm=args.confirm,
                max_alerts=args.max_alerts,
                ticktick_script=args.ticktick_script,
                notify_script=args.notify_script,
                project_name=args.project_name,
                priority=args.priority,
                window_bucket=args.window_bucket,
            )
            mode = "DRY-RUN" if res["dry_run"] else "LIVE"
            _emit(
                res,
                as_json=args.as_json,
                summary=(
                    f"[{mode}] Alerts evaluated={res['received_count']}, "
                    f"dispatched={res['dispatched_count']}, "
                    f"duplicates={res['suppressed_duplicate_count']}, "
                    f"capped={res['capped_count']}."
                ),
            )
            return 0
    except HumanConfirmationError as exc:
        print(f"[CONFIRMATION REQUIRED] {exc}", file=sys.stderr)
        return 2
    except (PrivacyBoundaryError, FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
