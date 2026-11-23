"""Linter enforcing 100% model and column description coverage in dbt manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "dbt" / "target" / "manifest.json"
PROJECT_MODEL_PREFIX = "model.ops_sla_telemetry."


def evaluate_manifest(manifest_path: Path) -> tuple[int, list[str], list[str]]:
    """Return exit code, undocumented model names, and undocumented column identifiers."""
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"dbt manifest not found at {manifest_path}. Run 'dbt docs generate' first."
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    nodes = manifest.get("nodes", {})
    sources = manifest.get("sources", {})

    total_models = 0
    total_columns = 0
    undocumented_models: list[str] = []
    undocumented_columns: list[str] = []

    for unique_id, node in nodes.items():
        if not unique_id.startswith(PROJECT_MODEL_PREFIX):
            continue

        model_name = str(node.get("name", unique_id))
        total_models += 1

        if not str(node.get("description", "")).strip():
            undocumented_models.append(model_name)

        for col_name, col_meta in node.get("columns", {}).items():
            total_columns += 1
            if not str(col_meta.get("description", "")).strip():
                undocumented_columns.append(f"{model_name}.{col_name}")

    print("=================================================================")
    print("dbt Documentation & Metadata Governance Linter (ODCS v3.1)")
    print("=================================================================")
    print(f"Total Models Evaluated:  {total_models}")
    print(f"Total Columns Evaluated: {total_columns}")
    print(f"Total Sources Evaluated: {len(sources)}")

    exit_code = 1 if (undocumented_models or undocumented_columns) else 0
    return exit_code, undocumented_models, undocumented_columns


def main() -> int:
    """CLI entrypoint for dbt documentation coverage audit."""
    parser = argparse.ArgumentParser(description="Verify 100% dbt model and column doc coverage.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="Path to dbt target/manifest.json",
    )
    args = parser.parse_args()

    try:
        exit_code, undocumented_models, undocumented_columns = evaluate_manifest(args.manifest)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    if undocumented_models:
        print(f"\n[FAIL] Found {len(undocumented_models)} undocumented models:")
        for model in undocumented_models:
            print(f"  - {model}")

    if undocumented_columns:
        print(f"\n[FAIL] Found {len(undocumented_columns)} undocumented columns:")
        for col in undocumented_columns:
            print(f"  - {col}")

    if exit_code != 0:
        print(
            "\nResult: REJECTED - 100% model and column description coverage is mandatory.",
            file=sys.stderr,
        )
        return 1

    print("\nResult: APPROVED - 100% Documentation Coverage across all models and columns!")
    print("=================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
