{{
  config(
    materialized='table'
  )
}}

WITH anchor AS (
    SELECT COALESCE(MAX(COALESCE(terminal_ts, created_ts)), CURRENT_TIMESTAMP) AS reference_ts
    FROM {{ ref('fct_service_order_sla') }}
),
cohort_base AS (
    SELECT
        f.*,
        a.reference_ts,
        COALESCE(f.terminal_ts, f.created_ts) AS eval_ts
    FROM {{ ref('fct_service_order_sla') }} AS f
    CROSS JOIN anchor AS a
),
regional_cohorts AS (
    SELECT
        region_id || '|' || priority AS cohort_key,
        region_id,
        priority,
        COUNT(*) FILTER (WHERE is_closed AND eval_ts >= reference_ts - INTERVAL '30 days') AS evaluated_30d,
        COUNT(*) FILTER (WHERE sla_status = 'SLA_BREACHED' AND is_closed AND eval_ts >= reference_ts - INTERVAL '30 days') AS breached_30d,
        COUNT(*) FILTER (WHERE is_closed AND eval_ts >= reference_ts - INTERVAL '1 hour') AS evaluated_1h,
        COUNT(*) FILTER (WHERE sla_status = 'SLA_BREACHED' AND is_closed AND eval_ts >= reference_ts - INTERVAL '1 hour') AS breached_1h,
        COUNT(*) FILTER (WHERE is_closed AND eval_ts >= reference_ts - INTERVAL '6 hours') AS evaluated_6h,
        COUNT(*) FILTER (WHERE sla_status = 'SLA_BREACHED' AND is_closed AND eval_ts >= reference_ts - INTERVAL '6 hours') AS breached_6h,
        COUNT(*) FILTER (WHERE is_closed AND eval_ts >= reference_ts - INTERVAL '24 hours') AS evaluated_24h,
        COUNT(*) FILTER (WHERE sla_status = 'SLA_BREACHED' AND is_closed AND eval_ts >= reference_ts - INTERVAL '24 hours') AS breached_24h,
        COUNT(*) FILTER (WHERE is_closed AND eval_ts >= reference_ts - INTERVAL '7 days') AS evaluated_7d,
        COUNT(*) FILTER (WHERE sla_status = 'SLA_BREACHED' AND is_closed AND eval_ts >= reference_ts - INTERVAL '7 days') AS breached_7d,
        COUNT(*) FILTER (WHERE sla_status = 'AT_RISK') AS at_risk_queue_count,
        ROUND(AVG(resolution_hours) FILTER (WHERE is_closed AND eval_ts >= reference_ts - INTERVAL '30 days'), 4) AS mttr_hours_30d
    FROM cohort_base
    GROUP BY region_id, priority

    UNION ALL

    SELECT
        'ALL|ALL' AS cohort_key,
        'ALL' AS region_id,
        'ALL' AS priority,
        COUNT(*) FILTER (WHERE is_closed AND eval_ts >= reference_ts - INTERVAL '30 days') AS evaluated_30d,
        COUNT(*) FILTER (WHERE sla_status = 'SLA_BREACHED' AND is_closed AND eval_ts >= reference_ts - INTERVAL '30 days') AS breached_30d,
        COUNT(*) FILTER (WHERE is_closed AND eval_ts >= reference_ts - INTERVAL '1 hour') AS evaluated_1h,
        COUNT(*) FILTER (WHERE sla_status = 'SLA_BREACHED' AND is_closed AND eval_ts >= reference_ts - INTERVAL '1 hour') AS breached_1h,
        COUNT(*) FILTER (WHERE is_closed AND eval_ts >= reference_ts - INTERVAL '6 hours') AS evaluated_6h,
        COUNT(*) FILTER (WHERE sla_status = 'SLA_BREACHED' AND is_closed AND eval_ts >= reference_ts - INTERVAL '6 hours') AS breached_6h,
        COUNT(*) FILTER (WHERE is_closed AND eval_ts >= reference_ts - INTERVAL '24 hours') AS evaluated_24h,
        COUNT(*) FILTER (WHERE sla_status = 'SLA_BREACHED' AND is_closed AND eval_ts >= reference_ts - INTERVAL '24 hours') AS breached_24h,
        COUNT(*) FILTER (WHERE is_closed AND eval_ts >= reference_ts - INTERVAL '7 days') AS evaluated_7d,
        COUNT(*) FILTER (WHERE sla_status = 'SLA_BREACHED' AND is_closed AND eval_ts >= reference_ts - INTERVAL '7 days') AS breached_7d,
        COUNT(*) FILTER (WHERE sla_status = 'AT_RISK') AS at_risk_queue_count,
        ROUND(AVG(resolution_hours) FILTER (WHERE is_closed AND eval_ts >= reference_ts - INTERVAL '30 days'), 4) AS mttr_hours_30d
    FROM cohort_base
),
burn_rates AS (
    SELECT
        cohort_key,
        region_id,
        priority,
        evaluated_30d,
        breached_30d,
        CASE
            WHEN evaluated_30d > 0 THEN ROUND(100.0 * (evaluated_30d - breached_30d) / evaluated_30d, 4)
            ELSE 100.0
        END AS sla_compliance_pct_30d,
        ROUND(
            GREATEST(0.0, (1.0 - (breached_30d / GREATEST(evaluated_30d * 0.01, 1.0))) * 100.0),
            4
        ) AS error_budget_remaining_pct,
        ROUND((breached_1h / GREATEST(evaluated_1h, 1.0)) / 0.01, 4) AS burn_rate_1h,
        ROUND((breached_6h / GREATEST(evaluated_6h, 1.0)) / 0.01, 4) AS burn_rate_6h,
        ROUND((breached_24h / GREATEST(evaluated_24h, 1.0)) / 0.01, 4) AS burn_rate_24h,
        ROUND((breached_7d / GREATEST(evaluated_7d, 1.0)) / 0.01, 4) AS burn_rate_7d,
        mttr_hours_30d,
        CASE
            WHEN breached_30d > 0 THEN ROUND(720.0 / breached_30d, 4)
            ELSE NULL
        END AS mtbf_hours_30d,
        at_risk_queue_count
    FROM regional_cohorts
)

SELECT
    cohort_key,
    region_id,
    priority,
    evaluated_30d,
    breached_30d,
    sla_compliance_pct_30d,
    error_budget_remaining_pct,
    burn_rate_1h,
    burn_rate_6h,
    burn_rate_24h,
    burn_rate_7d,
    mttr_hours_30d,
    mtbf_hours_30d,
    at_risk_queue_count,
    CASE
        WHEN burn_rate_1h >= 14.4 AND burn_rate_6h >= 14.4 THEN 'SEV-1'
        WHEN burn_rate_6h >= 6.0 AND burn_rate_24h >= 6.0 THEN 'SEV-2'
        WHEN burn_rate_24h >= 2.0 AND burn_rate_7d >= 2.0 THEN 'SEV-3'
        WHEN at_risk_queue_count > 0 OR sla_compliance_pct_30d < 99.0 THEN 'SEV-4'
        ELSE 'OK'
    END AS alert_severity
FROM burn_rates
