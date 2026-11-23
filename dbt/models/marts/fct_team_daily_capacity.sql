{{
  config(
    materialized='incremental',
    unique_key='team_day_key',
    incremental_strategy='delete+insert'
  )
}}

WITH
{% if is_incremental() %}
touched_buckets AS (
    SELECT DISTINCT order_date, region_id, team_id
    FROM {{ ref('fct_service_order_sla') }}
    WHERE last_ingest_seq > (
        SELECT COALESCE(MAX(last_ingest_seq), 0) FROM {{ this }}
    )
),
scoped_orders AS (
    SELECT f.*
    FROM {{ ref('fct_service_order_sla') }} AS f
    INNER JOIN touched_buckets AS b
        ON f.order_date = b.order_date
       AND f.region_id = b.region_id
       AND f.team_id = b.team_id
)
{% else %}
scoped_orders AS (
    SELECT *
    FROM {{ ref('fct_service_order_sla') }}
)
{% endif %}

SELECT
    CAST(order_date AS VARCHAR) || '|' || region_id || '|' || team_id AS team_day_key,
    order_date,
    region_id,
    team_id,
    COUNT(*) AS orders_created,
    COUNT(*) FILTER (WHERE sla_status = 'SLA_MET') AS orders_completed,
    COUNT(*) FILTER (WHERE sla_status = 'SLA_BREACHED') AS orders_breached,
    COUNT(*) FILTER (WHERE sla_status = 'AT_RISK') AS orders_at_risk,
    CASE
        WHEN COUNT(*) FILTER (WHERE is_closed) > 0 THEN
            ROUND(
                100.0 * (COUNT(*) FILTER (WHERE sla_status = 'SLA_MET'))
                / (COUNT(*) FILTER (WHERE is_closed)),
                4
            )
        ELSE 100.0
    END AS sla_compliance_pct,
    ROUND(AVG(resolution_hours) FILTER (WHERE is_closed), 4) AS mttr_hours,
    CASE
        WHEN COUNT(*) FILTER (WHERE sla_status = 'SLA_BREACHED') > 0 THEN
            ROUND(24.0 / (COUNT(*) FILTER (WHERE sla_status = 'SLA_BREACHED')), 4)
        ELSE NULL
    END AS mtbf_hours,
    ROUND(AVG(on_hold_minutes), 4) AS avg_hold_minutes,
    MAX(last_ingest_seq) AS last_ingest_seq
FROM scoped_orders
GROUP BY order_date, region_id, team_id
