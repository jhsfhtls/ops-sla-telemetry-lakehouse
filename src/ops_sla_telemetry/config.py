"""Path and runtime configuration settings for ops-sla-telemetry-lakehouse."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass
class LakehouseSettings:
    """Configuration paths for Bronze, Silver, Gold, Quarantine, and reference data."""

    repo_root: Path = field(default_factory=_default_repo_root)
    data_dir: Path | None = None
    bronze_db_path: Path | None = None
    silver_dir: Path | None = None
    gold_dir: Path | None = None
    quarantine_dir: Path | None = None
    reference_dir: Path | None = None

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root).resolve()
        resolved_data = Path(self.data_dir).resolve() if self.data_dir else (self.repo_root / "data")
        self.data_dir = resolved_data
        self.bronze_db_path = (
            Path(self.bronze_db_path).resolve()
            if self.bronze_db_path
            else resolved_data / "bronze" / "events_bronze.sqlite"
        )
        self.silver_dir = (
            Path(self.silver_dir).resolve() if self.silver_dir else resolved_data / "silver"
        )
        self.gold_dir = Path(self.gold_dir).resolve() if self.gold_dir else resolved_data / "gold"
        self.quarantine_dir = (
            Path(self.quarantine_dir).resolve()
            if self.quarantine_dir
            else resolved_data / "quarantine"
        )
        self.reference_dir = (
            Path(self.reference_dir).resolve()
            if self.reference_dir
            else self.repo_root / "data" / "reference"
        )

    def ensure_directories(self) -> None:
        """Create local writable lakehouse directories if absent."""
        assert self.bronze_db_path is not None
        assert self.silver_dir is not None
        assert self.gold_dir is not None
        assert self.quarantine_dir is not None
        self.bronze_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.silver_dir.mkdir(parents=True, exist_ok=True)
        self.gold_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
