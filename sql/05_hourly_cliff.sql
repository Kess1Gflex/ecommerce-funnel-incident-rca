SELECT 
    TIMESTAMP_TRUNC(event_timestamp, HOUR) AS event_hour,
    COUNT(DISTINCT CASE WHEN event_name = 'begin_checkout' THEN session_id END) AS checkout_sessions,
    COUNT(DISTINCT CASE WHEN event_name = 'purchase' THEN session_id END) AS purchase_sessions,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN event_name = 'purchase' THEN session_id END) / 
          NULLIF(COUNT(DISTINCT CASE WHEN event_name = 'begin_checkout' THEN session_id END), 0), 2) AS checkout_to_purchase_rate_pct
FROM `cloudcart-analytics.raw_events.web_sessions_stream`
WHERE device_category = 'mobile'
  AND event_timestamp BETWEEN '2026-11-06 12:00:00 UTC' AND '2026-11-07 02:00:00 UTC'
GROUP BY event_hour
ORDER BY event_hour;