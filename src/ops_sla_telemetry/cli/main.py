"""Command-line entrypoint (`ost`) for ops-sla-telemetry-lakehouse."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from ops_sla_telemetry import __version__


def build_parser() -> argparse.ArgumentParser:
    """Create the root CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="ost",
        description="Operational SLA Telemetry Lakehouse CLI",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")
    status_parser = subparsers.add_parser(
        "status",
        help="Show lakehouse foundation readiness and environment status",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit status output as JSON",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the `ost` CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "status":
        payload = {
            "service": "ops-sla-telemetry-lakehouse",
            "version": __version__,
            "status": "FOUNDATION_READY",
            "slo_target": 0.99,
            "dispatch_default_mode": "dry-run",
        }
        if args.as_json:
            print(json.dumps(payload, indent=2))
        else:
            print(
                f"ops-sla-telemetry-lakehouse v{__version__} "
                f"[status={payload['status']}, slo={payload['slo_target']:.0%}, "
                f"dispatch={payload['dispatch_default_mode']}]"
            )
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
