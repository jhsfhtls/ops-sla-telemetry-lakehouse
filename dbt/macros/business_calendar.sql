{% macro manaus_day(ts_expr) -%}
(DATE '1970-01-01' + CAST(FLOOR((EPOCH({{ ts_expr }}) - 14400.0) / 86400.0) AS INTEGER))
{%- endmacro %}

{% macro manaus_tod_minutes(ts_expr) -%}
((((CAST(FLOOR(EPOCH({{ ts_expr }}) - 14400.0) AS BIGINT) % 86400) + 86400) % 86400) + (EPOCH({{ ts_expr }}) - FLOOR(EPOCH({{ ts_expr }})))) / 60.0
{%- endmacro %}

{% macro business_calendar_ctes(min_date_sql, max_date_sql) -%}
holidays_ref AS (
    SELECT CAST(date_iso AS DATE) AS holiday_date
    FROM read_csv(
        '{{ env_var("OST_HOLIDAYS_CSV", "data/reference/br_national_holidays.csv") }}',
        header = true,
        columns = {'date_iso': 'DATE', 'holiday_name': 'VARCHAR'}
    )
),
raw_calendar_days AS (
    SELECT
        CAST(d AS DATE) AS day_date,
        (EXTRACT('isodow' FROM CAST(d AS DATE)) <= 5 AND h.holiday_date IS NULL) AS is_business_day
    FROM generate_series(
        {{ min_date_sql }},
        {{ max_date_sql }},
        INTERVAL '1 day'
    ) AS gs(d)
    LEFT JOIN holidays_ref AS h
        ON CAST(d AS DATE) = h.holiday_date
),
calendar_days AS (
    SELECT
        day_date,
        is_business_day,
        COALESCE(
            SUM(CASE WHEN is_business_day THEN 600.0 ELSE 0.0 END) OVER (
                ORDER BY day_date
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ),
            0.0
        ) AS cum_biz_minutes_before
    FROM raw_calendar_days
)
{%- endmacro %}
