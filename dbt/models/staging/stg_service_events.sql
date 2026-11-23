{{
  config(
    materialized='incremental',
    unique_key='event_id',
    incremental_strategy='delete+insert'
  )
}}

WITH raw_events AS (
    SELECT
        CAST(ingest_seq AS BIGINT) AS ingest_seq,
        CAST(event_id AS VARCHAR) AS event_id,
        CAST(order_id AS VARCHAR) AS order_id,
        CAST(event_type AS VARCHAR) AS event_type,
        CAST(event_ts AS TIMESTAMPTZ) AS event_ts,
        CAST(updated_at AS TIMESTAMPTZ) AS updated_at,
        CAST(team_id AS VARCHAR) AS team_id,
        CAST(region_id AS VARCHAR) AS region_id,
        CAST(priority AS VARCHAR) AS priority,
        CAST(hold_reason AS VARCHAR) AS hold_reason,
        CAST(resolution_code AS VARCHAR) AS resolution_code,
        CAST(source_name AS VARCHAR) AS source_name,
        CAST(batch_id AS VARCHAR) AS batch_id,
        CAST(ingested_at AS TIMESTAMPTZ) AS ingested_at
    FROM read_parquet('{{ env_var("OST_SILVER_GLOB", "data/silver/service_events_*.parquet") }}')
    {% if is_incremental() %}
    WHERE CAST(ingest_seq AS BIGINT) > (
        SELECT COALESCE(MAX(ingest_seq), 0) FROM {{ this }}
    )
    {% endif %}
)

SELECT
    ingest_seq,
    event_id,
    order_id,
    event_type,
    event_ts,
    updated_at,
    team_id,
    region_id,
    priority,
    hold_reason,
    resolution_code,
    source_name,
    batch_id,
    ingested_at
FROM raw_events
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY event_id
    ORDER BY updated_at DESC, ingest_seq DESC
) = 1
