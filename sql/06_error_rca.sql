SELECT 
    error_type,
    os_version,
    COUNT(DISTINCT session_id) AS impacted_sessions
FROM `cloudcart-analytics.raw_events.checkout_errors_stream`
WHERE device_category = 'mobile'
  AND error_timestamp >= '2026-11-06 18:30:00 UTC'
GROUP BY error_type, os_version
ORDER BY impacted_sessions DESC;