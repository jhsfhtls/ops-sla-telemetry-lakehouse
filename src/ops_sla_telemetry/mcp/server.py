"""Stdio MCP server entrypoint (`ost-mcp`) placeholder for Phase 06."""

from __future__ import annotations

import json
import sys


def main() -> int:
    """Emit server capability metadata until full Phase 06 stdio handler is wired."""
    payload = {
        "server": "ost-mcp",
        "status": "PLACEHOLDER_PHASE_01",
        "untrusted_wrapper_tag": "untrusted_data",
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
