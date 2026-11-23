{{
  config(
    materialized='incremental',
    unique_key='order_id',
    incremental_strategy='delete+insert'
  )
}}

WITH
{% if is_incremental() %}
touched_orders AS (
    SELECT DISTINCT order_id
    FROM {{ ref('stg_service_events') }}
    WHERE ingest_seq > (
        SELECT COALESCE(MAX(last_ingest_seq), 0) FROM {{ this }}
    )
),
ordered_events AS (
    SELECT s.*
    FROM {{ ref('stg_service_events') }} AS s
    INNER JOIN touched_orders AS t
        ON s.order_id = t.order_id
),
{% else %}
ordered_events AS (
    SELECT *
    FROM {{ ref('stg_service_events') }}
),
{% endif %}
{{ business_calendar_ctes(
    "(SELECT LEAST(COALESCE(MIN(" ~ manaus_day('event_ts') ~ "), DATE '2025-01-01'), DATE '2025-01-01') FROM ordered_events)",
    "(SELECT GREATEST(COALESCE(MAX(" ~ manaus_day('event_ts') ~ "), DATE '2027-12-31'), DATE '2027-12-31') FROM ordered_events)"
) }},
event_transitions AS (
    SELECT
        *,
        LEAD(event_ts) OVER (
            PARTITION BY order_id
            ORDER BY event_ts ASC, updated_at ASC, ingest_seq ASC
        ) AS next_event_ts,
        ROW_NUMBER() OVER (
            PARTITION BY order_id
            ORDER BY event_ts DESC, updated_at DESC, ingest_seq DESC
        ) AS rev_seq
    FROM ordered_events
),
hold_durations AS (
    SELECT
        et.order_id,
        et.priority,
        GREATEST(0.0, (EPOCH(et.next_event_ts) - EPOCH(et.event_ts)) / 60.0) AS hold_wall_min,
        GREATEST(
            0.0,
            (
                cal_end.cum_biz_minutes_before
                + CASE
                    WHEN cal_end.is_business_day
                    THEN LEAST(GREATEST({{ manaus_tod_minutes('et.next_event_ts') }} - 480.0, 0.0), 600.0)
                    ELSE 0.0
                  END
            ) - (
                cal_start.cum_biz_minutes_before
                + CASE
                    WHEN cal_start.is_business_day
                    THEN LEAST(GREATEST({{ manaus_tod_minutes('et.event_ts') }} - 480.0, 0.0), 600.0)
                    ELSE 0.0
                  END
            )
        ) AS hold_biz_min
    FROM event_transitions AS et
    LEFT JOIN calendar_days AS cal_start
        ON {{ manaus_day('et.event_ts') }} = cal_start.day_date
    LEFT JOIN calendar_days AS cal_end
        ON {{ manaus_day('et.next_event_ts') }} = cal_end.day_date
    WHERE et.event_type = 'ON_HOLD'
      AND et.next_event_ts IS NOT NULL
),
hold_agg AS (
    SELECT
        order_id,
        COUNT(*) AS hold_count,
        SUM(hold_wall_min) AS on_hold_wall_minutes,
        SUM(CASE WHEN priority = 'P1' THEN hold_wall_min ELSE hold_biz_min END) AS on_hold_minutes
    FROM hold_durations
    GROUP BY order_id
)

SELECT
    e.order_id,
    MAX(CASE WHEN e.rev_seq = 1 THEN e.team_id END) AS team_id,
    MAX(CASE WHEN e.rev_seq = 1 THEN e.region_id END) AS region_id,
    MAX(CASE WHEN e.rev_seq = 1 THEN e.priority END) AS priority,
    CASE
        WHEN MAX(CASE WHEN e.rev_seq = 1 THEN e.priority END) = 'P1' THEN '24x7'
        ELSE 'BUSINESS_HOURS'
    END AS calendar_mode,
    CASE
        WHEN MAX(CASE WHEN e.rev_seq = 1 THEN e.priority END) = 'P1' THEN 240
        WHEN MAX(CASE WHEN e.rev_seq = 1 THEN e.priority END) = 'P2' THEN 1440
        ELSE 4320
    END AS sla_target_minutes,
    COALESCE(
        MIN(CASE WHEN e.event_type = 'CREATED' THEN e.event_ts END),
        MIN(e.event_ts)
    ) AS created_ts,
    MIN(CASE WHEN e.event_type = 'ASSIGNED' THEN e.event_ts END) AS assigned_ts,
    MIN(CASE WHEN e.event_type = 'IN_TRANSIT' THEN e.event_ts END) AS in_transit_ts,
    MIN(CASE WHEN e.event_type = 'IN_EXECUTION' THEN e.event_ts END) AS in_execution_ts,
    MAX(CASE WHEN e.event_type IN ('COMPLETED', 'SLA_BREACHED') THEN e.event_ts END) AS terminal_ts,
    MAX(CASE WHEN e.rev_seq = 1 AND e.event_type IN ('COMPLETED', 'SLA_BREACHED') THEN e.event_type END)
        AS terminal_event_type,
    MAX(CASE WHEN e.rev_seq = 1 THEN e.event_type END) AS current_state,
    COUNT(*) AS event_count,
    COALESCE(MAX(h.hold_count), 0) AS hold_count,
    COALESCE(MAX(h.on_hold_wall_minutes), 0.0) AS on_hold_wall_minutes,
    COALESCE(MAX(h.on_hold_minutes), 0.0) AS on_hold_minutes,
    GREATEST(
        0.0,
        (
            EPOCH(
                COALESCE(
                    MAX(CASE WHEN e.event_type IN ('COMPLETED', 'SLA_BREACHED') THEN e.event_ts END),
                    MAX(e.event_ts)
                )
            ) - EPOCH(
                COALESCE(
                    MIN(CASE WHEN e.event_type = 'CREATED' THEN e.event_ts END),
                    MIN(e.event_ts)
                )
            )
        ) / 60.0
    ) AS gross_wall_minutes,
    MAX(e.ingest_seq) AS last_ingest_seq
FROM event_transitions AS e
LEFT JOIN hold_agg AS h
    ON e.order_id = h.order_id
GROUP BY e.order_id
