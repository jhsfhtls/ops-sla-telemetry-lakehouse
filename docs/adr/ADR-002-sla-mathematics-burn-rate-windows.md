# ADR-002: Dual-Calendar SLA Mathematics, Multi-Window Error-Budget Burn Rate, and MTTR/MTBF

- **Status:** Accepted
- **Date:** 2026-11-22
- **Governance & Standards:** `99.0% Global SLO`, `Google SRE Multi-Window Burn Rate`, `Dual SLA Calendar (24x7 vs Business Hours)`, `MTTR/MTBF Analytics`

## Context

Field-service operations (benchmarked on large-scale utility and infrastructure field teams managing 50,000+ work orders per year, 99.0% SLA attainment, and +15% team productivity via queue orchestration) and C.O.R.E. multi-repo SRE workflows require formal, auditable SLA mathematics.

Two recurring analytical pitfalls invalidate naive SLA reporting:
1. **Clock distortion:** Measuring raw wall-clock difference (`completed_at - created_at`) penalizes standard maintenance orders during off-shift nights, weekends, and national holidays, as well as valid external freezes (`ON_HOLD` waiting on customer access or utility shutoff permits).
2. **Threshold blindness:** Alerting only after monthly SLA compliance drops below `99.0%` detects failures far too late to protect the operational error budget, whereas alerting on tiny short-term noise causes alert fatigue.

## Decision

We standardize all SLA, error-budget burn-rate, and reliability calculations in the lakehouse and `dbt` marts under the following deterministic rules:

1. **Dual SLA Calendar Engine (`24x7` vs. Business Hours):**
   - All event timestamps are ingested and stored in `UTC` and mapped deterministically onto the operational timezone (`America/Manaus`, `UTC-04:00`) when evaluating business hours.
   - **Critical / Emergency Queue (`P1` / `EMERGENCY`):** Uses a continuous **`24x7` calendar** with a **`4h` (`240 min`)** active SLA target.
   - **High Priority Queue (`P2` / `HIGH`):** Uses a **Business Hours calendar** (`Monday–Friday, 08:00–18:00 America/Manaus`, 10 active hours/day, excluding Brazilian national holidays) with a **`24 business hours` (`1,440 min`)** active SLA target.
   - **Standard Queue (`P3` / `STANDARD`):** Uses the same **Business Hours calendar** with a **`72 business hours` (`4,320 min`)** active SLA target.
   - **C.O.R.E. Multi-Repo Operational Queues:** Consulting phase gates (`S01..S09`), career follow-ups (`D+3/D+7`), overdue SRS decks, POP freshness (`max_age_days=180`), and hook failures evaluate against explicit per-domain deadlines and `24h` imminent-risk thresholds.

2. **Active Elapsed Time & `ON_HOLD` Exclusion:**
   - Total active SLA consumption spans from `CREATED` to terminal completion (`COMPLETED` or `SLA_BREACHED`), accumulating active time across `CREATED`, `ASSIGNED`, `IN_TRANSIT`, and `IN_EXECUTION`.
   - Validated `ON_HOLD` intervals are excluded using calendar intersection:
     $$\text{active\_elapsed\_minutes} = \text{calendar\_minutes}(\text{created\_ts}, \text{terminal\_ts}) - \sum_{i} \text{calendar\_minutes}(\text{hold\_start}_i, \text{hold\_end}_i)$$
   - Status classification per order:
     - `SLA_MET`: Closed with `active_elapsed_minutes <= sla_target_minutes` (and no explicit `SLA_BREACHED` event).
     - `SLA_BREACHED`: Closed or open with `active_elapsed_minutes > sla_target_minutes` or terminal `SLA_BREACHED` event.
     - `AT_RISK`: Open order within `< 24h` remaining SLA budget (or `< 25%` remaining budget for `24x7` `P1` queues).

3. **99.0% Global SLO & Error Budget:**
   - Global service-level objective is **`SLO = 0.99` (`99.0%`)**, yielding a monthly/rolling error budget of **`1 - SLO = 0.01` (`1.0%`)**.
   - Error budget remaining for an evaluation cohort is:
     $$\text{budget\_remaining\_pct} = \max\left(0, 1 - \frac{\text{breached\_orders}}{\max(\text{evaluated\_orders} \times (1 - \text{SLO}), 1)}\right) \times 100$$

4. **Multi-Window Burn-Rate Alerting & Severity Mapping (`SEV-1..SEV-4`):**
   - For any rolling window $W \in \{1\text{h}, 6\text{h}, 24\text{h}, 7\text{d}\}$, the burn rate relative to the `99.0%` SLO is:
     $$\text{burn\_rate}(W) = \frac{\text{breached\_orders}(W) / \max(\text{evaluated\_orders}(W), 1)}{1 - \text{SLO}}$$
   - Multi-window gating pairs a long window and a short confirmation window to fire high-precision alerts mapped directly to incident severity:
     - **`SEV-1` (Critical / Page):** `burn_rate(1h) >= 14.4` **AND** `burn_rate(6h) >= 14.4` (consumes 100% of 30-day budget in ~2 days if sustained).
     - **`SEV-2` (High Alert):** `burn_rate(6h) >= 6.0` **AND** `burn_rate(24h) >= 6.0` (consumes 100% of 30-day budget in ~5 days).
     - **`SEV-3` (Warning Ticket):** `burn_rate(24h) >= 2.0` **AND** `burn_rate(7d) >= 2.0` (sustained budget erosion).
     - **`SEV-4` (Informational / Preventive):** Open queue approaching breach (`remaining_sla_hours < 24`) or C.O.R.E. freshness/follow-up warning without sustained burn-rate breach.

5. **MTTR, MTBF, and Team Productivity KPIs:**
   - **MTTR (Mean Time to Resolution):** Mean `active_elapsed_minutes / 60.0` across resolved orders/incidents grouped by `(region_id, team_id, priority)`.
   - **MTBF (Mean Time Between Failures/Breaches):** Mean operational interval between consecutive `SLA_BREACHED` occurrences per `(region_id, team_id)` over the evaluation window (`window_hours / breach_count`, or `NULL` / censored upper bound when `breach_count == 0`).
   - **Team Capacity & Productivity:** Completed orders per active team-day, hold ratio (`on_hold_minutes / total_span_minutes`), and at-risk backlog ratio to guide dynamic dispatch rebalancing.

## Consequences

- **Positive:**
  - Fair, operationally realistic SLA accounting across emergency `24x7` and standard business-hour workflows.
  - Multi-window burn-rate pairs eliminate alert flapping while surfacing rapid error-budget depletion hours before a monthly SLA breach occurs.
  - Unified severity model (`SEV-1..SEV-4`) cleanly drives automated runbook drafting and idempotent dispatching.
- **Trade-offs:**
  - Computing exact business-hour intersections excluding Brazilian national holidays requires maintaining a deterministic holiday reference calendar table in `data/reference/` and matching SQL/Python logic across simulator and dbt layers.

## Verification Criteria

- Unit tests assert exact `calendar_minutes` behavior across weekends, after-hours boundaries (`18:00` to `08:00`), national holidays, and `ON_HOLD` interval subtraction for both `24x7` and business-hours queues.
- Multi-window burn-rate calculations (`1h/6h`, `6h/24h`, `24h/7d`) match expected `SEV-1..SEV-4` assignments on deterministic boundary fixtures.
