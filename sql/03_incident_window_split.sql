WITH cohort_funnel AS (
    SELECT 
        CASE 
            WHEN event_timestamp < '2026-11-06 18:30:00 UTC' THEN '1_Pre_Incident_Baseline'
            ELSE '2_Post_Deploy_Incident'
        END AS incident_window,
        COUNT(DISTINCT CASE WHEN event_name = 'begin_checkout' THEN session_id END) AS checkout_sessions,
        COUNT(DISTINCT CASE WHEN event_name = 'purchase' THEN session_id END) AS purchase_sessions
    FROM `cloudcart-analytics.raw_events.web_sessions_stream`
    GROUP BY incident_window
)
SELECT 
    incident_window,
    checkout_sessions,
    purchase_sessions,
    ROUND(100.0 * purchase_sessions / NULLIF(checkout_sessions, 0), 2) AS checkout_to_purchase_rate_pct
FROM cohort_funnel
ORDER BY incident_window;