"""Phase 03 privacy boundary, strict allowlist, and read-only C.O.R.E. collector tests."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from ops_sla_telemetry.collectors import (
    AGENT_TELEMETRY_ALLOWLIST,
    CAREER_ALLOWLIST,
    CONSULTING_ALLOWLIST,
    POP_FRESHNESS_ALLOWLIST,
    SRS_ALLOWLIST,
    CoreMultiRepoCollector,
    PrivacyBoundaryError,
    collect_agent_telemetry,
    collect_career_followups,
    collect_consulting_sla,
    collect_pop_freshness,
    collect_srs_due_cards,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_synthetic_core_tree(tmp_path: Path) -> tuple[Path, Path, Path, list[Path]]:
    core_root = tmp_path / "core"
    studies_root = tmp_path / "studies"
    public_repo = tmp_path / "public-repo"
    public_repo.mkdir(parents=True, exist_ok=True)

    # 1. agent_telemetry.jsonl (includes forbidden 'notes' and 'operation')
    telemetry_path = core_root / ".logs" / "agent_telemetry.jsonl"
    telemetry_path.parent.mkdir(parents=True, exist_ok=True)
    telemetry_lines = [
        json.dumps(
            {
                "timestamp_utc": "2026-11-22T10:00:00Z",
                "trace_id": "trace-1",
                "span_id": "span-1",
                "parent_span_id": "",
                "agent_role": "primary",
                "skill": "tech-code-reviewer",
                "operation": "PRIVATE_OP_SECRET",
                "model": "gemini-4-neon",
                "provider": "google",
                "tokens_in": 120,
                "tokens_out": 80,
                "tokens_total": 200,
                "cost_usd": 0.001,
                "duration_ms": 450,
                "status": "error",
                "repo": "core",
                "notes": "SECRET_TOKEN_NOTE_DO_NOT_LEAK",
            }
        ),
        "{broken json line",
    ]
    telemetry_path.write_text("\n".join(telemetry_lines) + "\n", encoding="utf-8")

    # 2. POP files (stale, fresh, missing review date) with confidential prose body
    pop_dir = core_root / "docs" / "Tecnicos"
    pop_dir.mkdir(parents=True, exist_ok=True)
    pop_stale = pop_dir / "POP_T_901_Sample_Stale.md"
    pop_stale.write_text(
        "# POP_T_901\n- Last Reviewed: 2025-01-01\n\nCONFIDENTIAL_BODY_PROSE_SHOULD_NOT_APPEAR\n",
        encoding="utf-8",
    )
    pop_fresh = pop_dir / "POP_T_902_Sample_Fresh.md"
    pop_fresh.write_text(
        "# POP_T_902\n- **Última Atualização:** 2026-10-01\n\nINTERNAL_BODY_PROSE_902\n",
        encoding="utf-8",
    )
    pop_missing = pop_dir / "POP_T_903_No_Date.md"
    pop_missing.write_text("# POP_T_903\n\nNO_DATE_BODY_PROSE\n", encoding="utf-8")

    # 3. consulting_pipeline.json with synthetic PII
    state_dir = core_root / ".agents" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    consulting_path = state_dir / "consulting_pipeline.json"
    consulting_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "last_updated": "2026-11-23",
                "clientes": [
                    {
                        "id": "ACME_CORP_NDA_RAW_ID",
                        "cliente": "ACME_CORP_NDA",
                        "nome": "ACME Secret Project",
                        "setor": "Energia",
                        "segmento": "Enterprise",
                        "sponsor": "Joao Silva",
                        "fase_atual": "S08_SRE",
                        "status": "Em andamento",
                        "data_abertura": "2026-10-01",
                        "data_entrada": "2026-11-10",
                        "proxima_acao": "SECRET_NEXT_ACTION_CONFIDENTIAL",
                        "next_action": "SECRET_NEXT_ACTION_CONFIDENTIAL",
                        "path_jornada": "../operations/Clientes/ACME_CORP_NDA/Jornada.md",
                    },
                    {
                        "id": "BETA_CLIENT_ID",
                        "cliente": "BETA_CONFIDENTIAL",
                        "sponsor": "Maria Souza",
                        "fase_atual": "S05_ONBOARDING",
                        "status": "CONCLUIDO",
                        "data_abertura": "2026-09-01",
                        "data_entrada": "2026-09-15",
                        "path_jornada": "../operations/Clientes/BETA/Jornada.md",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    # 4. career_pipeline.json with forbidden company/role/folder fields
    career_path = state_dir / "career_pipeline.json"
    career_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "last_updated": "2026-11-23",
                "candidaturas": [
                    {
                        "id": "BIGTECH_RAW_JOB_01",
                        "empresa": "BIGTECH_CONFIDENTIAL",
                        "vaga": "Staff Data Engineer",
                        "data_aplicacao": "2026-11-10",
                        "status": "Aplicado",
                        "proxima_acao": "FOLLOWUP_RECRUITER_JANE",
                        "artifact_folder": "candidaturas/BIGTECH_CONFIDENTIAL/",
                    },
                    {
                        "id": "STARTUP_JOB_02",
                        "empresa": "STARTUP_SECRET",
                        "vaga": "Lead Architect",
                        "data_aplicacao": "2026-11-19",
                        "status": "Em Processo",
                    },
                    {
                        "id": "CLOSED_JOB_03",
                        "empresa": "OLD_CORP_SECRET",
                        "vaga": "Principal Engineer",
                        "data_aplicacao": "2026-10-01",
                        "status": "Rejeitado",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    # 5. studies/.agents/memory/srs_state.json with extra private fields
    srs_path = studies_root / ".agents" / "memory" / "srs_state.json"
    srs_path.parent.mkdir(parents=True, exist_ok=True)
    srs_path.write_text(
        json.dumps(
            {
                "erro_mod1_raw_topic_secret": {
                    "last_reviewed": "2026-11-10",
                    "next_review": "2026-11-20",
                    "interval": 3,
                    "review_count": 2,
                    "last_result": "PASS",
                    "question": "PRIVATE_STUDY_QUESTION_TEXT",
                    "answer": "PRIVATE_STUDY_ANSWER_TEXT",
                },
                "erro_mod2_future_card": {
                    "last_reviewed": "2026-11-22",
                    "next_review": "2026-11-29",
                    "interval": 7,
                    "review_count": 3,
                    "last_result": "PASS",
                },
            }
        ),
        encoding="utf-8",
    )

    input_files = [
        telemetry_path,
        pop_stale,
        pop_fresh,
        pop_missing,
        consulting_path,
        career_path,
        srs_path,
    ]
    return core_root, studies_root, public_repo, input_files


def test_strict_allowlist_drops_all_forbidden_fields_and_pii(tmp_path: Path) -> None:
    """Prove no forbidden field, raw ID, or private prose/PII enters output snapshots."""
    core_root, studies_root, public_repo, input_files = _create_synthetic_core_tree(tmp_path)
    before_hashes = {p: _sha256(p) for p in input_files}

    collector = CoreMultiRepoCollector(
        core_root=core_root,
        studies_root=studies_root,
        public_repo_root=public_repo,
    )
    snapshot = collector.collect_all(reference_date=date(2026, 11, 23))
    out_file = collector.write_snapshot(snapshot)

    # Read-only guarantee: input files remain untouched
    after_hashes = {p: _sha256(p) for p in input_files}
    assert before_hashes == after_hashes

    # Check exact key allowlists per source
    sources = snapshot["sources"]
    assert len(sources["agent_telemetry"]) == 1
    assert set(sources["agent_telemetry"][0].keys()) <= AGENT_TELEMETRY_ALLOWLIST

    for pop_row in sources["pop_freshness"]:
        assert set(pop_row.keys()) == POP_FRESHNESS_ALLOWLIST

    for con_row in sources["consulting_sla"]:
        assert set(con_row.keys()) == CONSULTING_ALLOWLIST | {"days_in_phase", "sla_status"}
        assert con_row["id"].startswith("OPS-CON-")

    for car_row in sources["career_followups"]:
        assert set(car_row.keys()) == CAREER_ALLOWLIST | {
            "days_since_application",
            "followup_status",
        }
        assert car_row["id"].startswith("CAR-")

    for srs_row in sources["srs_cards"]:
        assert set(srs_row.keys()) == SRS_ALLOWLIST | {"is_due", "days_overdue"}
        assert srs_row["card_id"].startswith("SRS-")

    # Zero forbidden values or raw IDs anywhere in written JSON snapshot
    persisted_text = out_file.read_text(encoding="utf-8")
    forbidden_substrings = (
        "SECRET_TOKEN_NOTE_DO_NOT_LEAK",
        "PRIVATE_OP_SECRET",
        "CONFIDENTIAL_BODY_PROSE",
        "INTERNAL_BODY_PROSE_902",
        "ACME_CORP_NDA",
        "ACME Secret Project",
        "Joao Silva",
        "Maria Souza",
        "BETA_CONFIDENTIAL",
        "SECRET_NEXT_ACTION_CONFIDENTIAL",
        "operations/Clientes",
        "BIGTECH_CONFIDENTIAL",
        "BIGTECH_RAW_JOB_01",
        "Staff Data Engineer",
        "FOLLOWUP_RECRUITER_JANE",
        "STARTUP_SECRET",
        "OLD_CORP_SECRET",
        "erro_mod1_raw_topic_secret",
        "PRIVATE_STUDY_QUESTION_TEXT",
        "PRIVATE_STUDY_ANSWER_TEXT",
        "proxima_acao",
        "path_jornada",
        "artifact_folder",
        "sponsor",
        "empresa",
        "vaga",
        "cliente",
    )
    for forbidden in forbidden_substrings:
        assert forbidden not in persisted_text, f"Forbidden string leaked: {forbidden}"

    # Verify summary counts match synthetic fixtures
    summary = snapshot["summary"]
    assert summary["agent_spans_total"] == 1
    assert summary["agent_errors_total"] == 1
    assert summary["stale_pops_count"] == 2
    assert summary["consulting_at_risk_count"] == 1
    assert summary["career_followup_due_count"] == 2
    assert summary["srs_overdue_count"] == 1


def test_privacy_boundary_blocks_writing_inside_public_repo(tmp_path: Path) -> None:
    """Fail closed when output_dir resolves inside the public repository directory."""
    core_root, studies_root, public_repo, _ = _create_synthetic_core_tree(tmp_path)
    collector = CoreMultiRepoCollector(
        core_root=core_root,
        studies_root=studies_root,
        output_dir=public_repo / "data" / "core_telemetry",
        public_repo_root=public_repo,
    )
    snapshot = collector.collect_all(reference_date=date(2026, 11, 23))
    with pytest.raises(PrivacyBoundaryError):
        collector.write_snapshot(snapshot)


def test_collectors_sla_classifications_and_missing_files(tmp_path: Path) -> None:
    """Verify SLA classifications and graceful empty results on missing input files."""
    core_root, studies_root, _, _ = _create_synthetic_core_tree(tmp_path)
    ref = date(2026, 11, 23)

    pops = {r["pop_id"]: r for r in collect_pop_freshness(core_root / "docs", reference_date=ref)}
    assert pops["POP_T_901"]["status"] == "stale"
    assert pops["POP_T_902"]["status"] == "fresh"
    assert pops["POP_T_903"]["status"] == "missing_review_date"

    consulting = collect_consulting_sla(
        core_root / ".agents" / "state" / "consulting_pipeline.json",
        reference_date=ref,
    )
    assert [row["sla_status"] for row in consulting] == ["SLA_BREACHED", "CLOSED"]

    career = collect_career_followups(
        core_root / ".agents" / "state" / "career_pipeline.json",
        reference_date=ref,
    )
    assert [row["followup_status"] for row in career] == ["OVERDUE_D7", "DUE_D3", "CLOSED"]

    srs = {r["interval"]: r for r in collect_srs_due_cards(
        studies_root / ".agents" / "memory" / "srs_state.json",
        reference_date=ref,
    )}
    assert srs[3]["is_due"] is True
    assert srs[3]["days_overdue"] == 3
    assert srs[7]["is_due"] is False
    assert srs[7]["days_overdue"] == 0

    missing_dir = tmp_path / "does_not_exist"
    assert collect_agent_telemetry(missing_dir / "a.jsonl") == []
    assert collect_pop_freshness(missing_dir) == []
    assert collect_consulting_sla(missing_dir / "c.json") == []
    assert collect_career_followups(missing_dir / "ca.json") == []
    assert collect_srs_due_cards(missing_dir / "s.json") == []
