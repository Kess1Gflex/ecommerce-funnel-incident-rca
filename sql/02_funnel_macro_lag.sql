WITH funnel_stages AS (
    SELECT 
        event_name,
        CASE 
            WHEN event_name = 'session_start'  THEN 1
            WHEN event_name = 'view_item'      THEN 2
            WHEN event_name = 'add_to_cart'    THEN 3
            WHEN event_name = 'begin_checkout' THEN 4
            WHEN event_name = 'purchase'       THEN 5
        END AS step_order,
        COUNT(DISTINCT session_id) AS total_sessions
    FROM `cloudcart-analytics.raw_events.web_sessions_stream`
    GROUP BY event_name, step_order
)
SELECT 
    step_order,
    event_name,
    total_sessions,
    LAG(total_sessions) OVER (ORDER BY step_order) AS prev_step_sessions,
    ROUND(100.0 * total_sessions / LAG(total_sessions) OVER (ORDER BY step_order), 2) AS conversion_rate_pct,
    ROUND(100.0 * (LAG(total_sessions) OVER (ORDER BY step_order) - total_sessions) / 
          LAG(total_sessions) OVER (ORDER BY step_order), 2) AS dropoff_pct
FROM funnel_stages
ORDER BY step_order;