"""FastAPI application factory exposing `/api/v1/*` operational SLA endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from ops_sla_telemetry import __version__
from ops_sla_telemetry.collectors import PrivacyBoundaryError
from ops_sla_telemetry.dispatcher import HumanConfirmationError
from ops_sla_telemetry.sdk import OpsSlaTelemetry


class IngestRequest(BaseModel):
    """Request payload for deterministic event generation and dbt build."""

    seed: int = 42
    order_count: int = Field(default=50_000, ge=1)
    run_dbt: bool = True
    batch_id: str | None = None


class SyncTickTickRequest(BaseModel):
    """Request payload for idempotent alert evaluation and external dispatch."""

    execute: bool = False
    confirm: bool = False
    max_alerts: int = Field(default=10, ge=1)
    project_name: str = "00_INBOX_AGENTES"
    priority: int = 5
    window_bucket: str | None = None


def _handle_sdk_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (HumanConfirmationError, PrivacyBoundaryError)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


def create_app(sdk: OpsSlaTelemetry | None = None) -> FastAPI:
    """Construct a FastAPI application backed by OpsSlaTelemetry."""
    service = sdk or OpsSlaTelemetry()
    app = FastAPI(
        title="Ops SLA Telemetry Lakehouse API",
        version=__version__,
        description="Event-driven field-service & C.O.R.E. operational SLA telemetry gateway.",
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "service": "ops-sla-telemetry-lakehouse",
            "version": __version__,
            "status": "OK",
            "dispatch_default_mode": "dry-run",
        }

    @app.post("/api/v1/ingest")
    def ingest(payload: IngestRequest) -> dict[str, Any]:
        try:
            return service.ingest(
                seed=payload.seed,
                order_count=payload.order_count,
                run_dbt=payload.run_dbt,
                batch_id=payload.batch_id,
            )
        except Exception as exc:
            raise _handle_sdk_error(exc) from exc

    @app.get("/api/v1/sla-status")
    def sla_status(
        region_id: str | None = Query(default=None),
        priority: str | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            return service.sla_status(region_id=region_id, priority=priority)
        except Exception as exc:
            raise _handle_sdk_error(exc) from exc

    @app.get("/api/v1/at-risk")
    def at_risk(
        limit: int = Query(default=25, ge=1, le=500),
        region_id: str | None = Query(default=None),
        priority: str | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            return service.at_risk(limit=limit, region_id=region_id, priority=priority)
        except Exception as exc:
            raise _handle_sdk_error(exc) from exc

    @app.get("/api/v1/team-capacity")
    def team_capacity(
        limit: int = Query(default=25, ge=1, le=500),
        region_id: str | None = Query(default=None),
        team_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            return service.team_capacity(limit=limit, region_id=region_id, team_id=team_id)
        except Exception as exc:
            raise _handle_sdk_error(exc) from exc

    @app.get("/api/v1/core-health")
    def core_health(
        core_root: str | None = Query(default=None),
        studies_root: str | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            return service.core_health(
                core_root=Path(core_root) if core_root else None,
                studies_root=Path(studies_root) if studies_root else None,
                write_snapshot=False,
            )
        except Exception as exc:
            raise _handle_sdk_error(exc) from exc

    @app.post("/api/v1/sync-ticktick")
    def sync_ticktick(payload: SyncTickTickRequest) -> dict[str, Any]:
        try:
            return service.sync_ticktick(
                execute=payload.execute,
                confirm=payload.confirm,
                max_alerts=payload.max_alerts,
                project_name=payload.project_name,
                priority=payload.priority,
                window_bucket=payload.window_bucket,
            )
        except Exception as exc:
            raise _handle_sdk_error(exc) from exc

    return app
