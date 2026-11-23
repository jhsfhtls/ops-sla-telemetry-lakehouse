{{
  config(
    materialized='incremental',
    unique_key='order_id',
    incremental_strategy='delete+insert'
  )
}}

WITH reference_anchor AS (
    SELECT COALESCE(MAX(event_ts), CURRENT_TIMESTAMP) AS reference_ts
    FROM {{ ref('stg_service_events') }}
),
scoped_spans AS (
    SELECT s.*
    FROM {{ ref('int_order_lifecycle_spans') }} AS s
    {% if is_incremental() %}
    WHERE s.last_ingest_seq > (
        SELECT COALESCE(MAX(last_ingest_seq), 0) FROM {{ this }}
    )
    {% endif %}
),
{{ business_calendar_ctes(
    "(SELECT LEAST(COALESCE(MIN(" ~ manaus_day('created_ts') ~ "), DATE '2025-01-01'), DATE '2025-01-01') FROM scoped_spans)",
    "(SELECT GREATEST(COALESCE(MAX(" ~ manaus_day('COALESCE(terminal_ts, created_ts)') ~ "), DATE '2027-12-31'), DATE '2027-12-31') FROM scoped_spans)"
) }},
evaluated AS (
    SELECT
        s.order_id,
        s.team_id,
        s.region_id,
        s.priority,
        s.calendar_mode,
        s.sla_target_minutes,
        s.created_ts,
        s.terminal_ts,
        s.terminal_event_type,
        s.current_state,
        {{ manaus_day('s.created_ts') }} AS order_date,
        s.gross_wall_minutes,
        s.on_hold_minutes,
        (s.terminal_ts IS NOT NULL) AS is_closed,
        CASE
            WHEN s.calendar_mode = '24x7' THEN
                GREATEST(
                    0.0,
                    (EPOCH(COALESCE(s.terminal_ts, r.reference_ts)) - EPOCH(s.created_ts)) / 60.0
                )
            ELSE
                GREATEST(
                    0.0,
                    (
                        cal_end.cum_biz_minutes_before
                        + CASE
                            WHEN cal_end.is_business_day
                            THEN LEAST(GREATEST({{ manaus_tod_minutes('COALESCE(s.terminal_ts, r.reference_ts)') }} - 480.0, 0.0), 600.0)
                            ELSE 0.0
                          END
                    ) - (
                        cal_start.cum_biz_minutes_before
                        + CASE
                            WHEN cal_start.is_business_day
                            THEN LEAST(GREATEST({{ manaus_tod_minutes('s.created_ts') }} - 480.0, 0.0), 600.0)
                            ELSE 0.0
                          END
                    )
                )
        END AS gross_calendar_minutes,
        s.last_ingest_seq
    FROM scoped_spans AS s
    CROSS JOIN reference_anchor AS r
    LEFT JOIN calendar_days AS cal_start
        ON {{ manaus_day('s.created_ts') }} = cal_start.day_date
    LEFT JOIN calendar_days AS cal_end
        ON {{ manaus_day('COALESCE(s.terminal_ts, r.reference_ts)') }} = cal_end.day_date
)

SELECT
    order_id,
    team_id,
    region_id,
    priority,
    calendar_mode,
    sla_target_minutes,
    created_ts,
    terminal_ts,
    terminal_event_type,
    current_state,
    order_date,
    gross_wall_minutes,
    on_hold_minutes,
    GREATEST(0.0, gross_calendar_minutes - on_hold_minutes) AS active_elapsed_minutes,
    is_closed,
    CASE
        WHEN is_closed AND (
            terminal_event_type = 'SLA_BREACHED'
            OR GREATEST(0.0, gross_calendar_minutes - on_hold_minutes) > sla_target_minutes
        ) THEN 'SLA_BREACHED'
        WHEN is_closed THEN 'SLA_MET'
        WHEN GREATEST(0.0, gross_calendar_minutes - on_hold_minutes) > sla_target_minutes
            THEN 'SLA_BREACHED'
        WHEN ((sla_target_minutes - GREATEST(0.0, gross_calendar_minutes - on_hold_minutes)) / 60.0) < 24.0
            THEN 'AT_RISK'
        ELSE 'ON_TRACK'
    END AS sla_status,
    CASE
        WHEN is_closed THEN 0.0
        ELSE ROUND((sla_target_minutes - GREATEST(0.0, gross_calendar_minutes - on_hold_minutes)) / 60.0, 4)
    END AS remaining_sla_hours,
    CASE
        WHEN is_closed THEN ROUND(GREATEST(0.0, gross_calendar_minutes - on_hold_minutes) / 60.0, 4)
        ELSE NULL
    END AS resolution_hours,
    last_ingest_seq
FROM evaluated
