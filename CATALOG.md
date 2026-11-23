# Event & SLA Policy Catalog

> Auto-generated deterministically from `catalog/events_and_slas.yaml` (`scripts/render_catalog.py`).

## 1. Global SLO Defaults

| Parameter | Value |
| :--- | :--- |
| **Schema Version** | `1.0.0` |
| **Global SLO Target** | `99.0%` |
| **Error Budget** | `1.0%` |
| **Operational Timezone** | `America/Manaus` |
| **Business Hours Calendar** | `Mon-Fri 08:00-18:00` |
| **Burn-Rate Baseline Window** | `720h` |

## 2. Canonical Event Types (12)

| Event Type | Domain | Privacy Tier | Terminal | Required Fields | Description |
| :--- | :--- | :--- | :---: | :--- | :--- |
| `CREATED` | `FIELD_SERVICE` | `SYNTHETIC_PUBLIC` | No | `event_id`, `order_id`, `event_type`, `event_ts`, `updated_at`, `team_id`, `region_id`, `priority` | Initial creation of a field-service work order entering the SLA clock. |
| `ASSIGNED` | `FIELD_SERVICE` | `SYNTHETIC_PUBLIC` | No | `event_id`, `order_id`, `event_type`, `event_ts`, `updated_at`, `team_id`, `region_id`, `priority` | Dispatch assignment of an open order to a field crew or queue. |
| `IN_TRANSIT` | `FIELD_SERVICE` | `SYNTHETIC_PUBLIC` | No | `event_id`, `order_id`, `event_type`, `event_ts`, `updated_at`, `team_id`, `region_id`, `priority` | Field crew dispatched and en route to service site. |
| `IN_EXECUTION` | `FIELD_SERVICE` | `SYNTHETIC_PUBLIC` | No | `event_id`, `order_id`, `event_type`, `event_ts`, `updated_at`, `team_id`, `region_id`, `priority` | Active on-site field intervention underway. |
| `ON_HOLD` | `FIELD_SERVICE` | `SYNTHETIC_PUBLIC` | No | `event_id`, `order_id`, `event_type`, `event_ts`, `updated_at`, `team_id`, `region_id`, `priority` | Validated operational freeze excluded from active SLA elapsed calculation. |
| `COMPLETED` | `FIELD_SERVICE` | `SYNTHETIC_PUBLIC` | Yes | `event_id`, `order_id`, `event_type`, `event_ts`, `updated_at`, `team_id`, `region_id`, `priority` | Successful service order resolution stopping the SLA clock. |
| `SLA_BREACHED` | `FIELD_SERVICE` | `SYNTHETIC_PUBLIC` | Yes | `event_id`, `order_id`, `event_type`, `event_ts`, `updated_at`, `team_id`, `region_id`, `priority` | Explicit SLA target violation recorded for error-budget and MTBF tracking. |
| `CORE_AGENT_SPAN` | `CORE_OBSERVABILITY` | `LOCAL_ALLOWLIST_ONLY` | Yes | `timestamp_utc`, `trace_id`, `span_id`, `skill`, `model`, `provider`, `tokens_total`, `cost_usd`, `duration_ms`, `status`, `repo` | Read-only execution telemetry span from core/.logs/agent_telemetry.jsonl excluding free-text notes. |
| `CORE_POP_FRESHNESS` | `CORE_OBSERVABILITY` | `LOCAL_ALLOWLIST_ONLY` | No | `pop_id`, `relative_path`, `last_reviewed`, `age_days`, `status` | POP freshness metadata parsed from review headers without reading prose content. |
| `CORE_CONSULTING_PHASE` | `CORE_OBSERVABILITY` | `LOCAL_ALLOWLIST_ONLY` | No | `id`, `fase_atual`, `status`, `data_abertura`, `data_entrada` | Anonymized consulting phase SLA metadata (S01..S09) excluding client names, sponsors, and paths. |
| `CORE_CAREER_FOLLOWUP` | `CORE_OBSERVABILITY` | `LOCAL_ALLOWLIST_ONLY` | No | `id`, `data_aplicacao`, `status` | Anonymized career application follow-up SLA metadata (D+3/D+7) excluding company and role names. |
| `CORE_SRS_CARD_DUE` | `CORE_OBSERVABILITY` | `LOCAL_ALLOWLIST_ONLY` | No | `card_id`, `last_reviewed`, `next_review`, `interval`, `review_count`, `last_result` | Anonymized spaced-repetition review status from studies without study note content. |

## 3. SLA Policies (7)

| Policy ID | Domain | Priority | Calendar | Target (h) | At-Risk Threshold (h) | Description |
| :--- | :--- | :---: | :--- | :---: | :---: | :--- |
| `P1_EMERGENCY` | `FIELD_SERVICE` | `P1` | `24x7` | `4h` | `1h` | Critical 24x7 emergency dispatch queue with 4-hour resolution SLA. |
| `P2_HIGH` | `FIELD_SERVICE` | `P2` | `BUSINESS_HOURS` | `24h` | `6h` | High-priority field work orders measured in Mon-Fri 08:00-18:00 America/Manaus business hours. |
| `P3_STANDARD` | `FIELD_SERVICE` | `P3` | `BUSINESS_HOURS` | `72h` | `24h` | Standard scheduled field orders measured in Mon-Fri 08:00-18:00 America/Manaus business hours. |
| `CORE_CONSULTING_PHASE_SLA` | `CORE_OBSERVABILITY` | `P2` | `BUSINESS_HOURS` | `168h` | `24h` | Consulting lifecycle phase progression SLA (S01..S09) without PII. |
| `CORE_CAREER_FOLLOWUP_SLA` | `CORE_OBSERVABILITY` | `P2` | `BUSINESS_HOURS` | `72h` | `24h` | Career follow-up cadence SLA (D+3 / D+7 checkpoints). |
| `CORE_POP_FRESHNESS_SLA` | `CORE_OBSERVABILITY` | `P3` | `24x7` | `4320h` | `24h` | Standard Operating Procedure review freshness threshold (180 days max age). |
| `CORE_SRS_REVIEW_SLA` | `CORE_OBSERVABILITY` | `P2` | `24x7` | `24h` | `6h` | Daily active recall SLA for overdue spaced-repetition flashcards. |

## 4. Multi-Window Error-Budget Burn-Rate Matrix (4)

| Severity | Short Window | Long Window | Burn-Rate Threshold | Automated Action |
| :---: | :---: | :---: | :---: | :--- |
| `SEV-1` | `1h` | `6h` | `14.4x` | Critical page + auto-draft incident post-mortem (POP_T_036/POP_B_115) |
| `SEV-2` | `6h` | `24h` | `6.0x` | High-priority alert + TickTick task in 00_INBOX_AGENTES (dry-run default) |
| `SEV-3` | `24h` | `168h` | `2.0x` | Warning ticket + team workload rebalancing recommendation |
| `SEV-4` | `24h` | `24h` | `1.0x` | Preventive watchlist for open queues with remaining_sla_hours < 24h |

## 5. Sync Verification

```bash
python scripts/render_catalog.py --check
python scripts/render_catalog.py
```
