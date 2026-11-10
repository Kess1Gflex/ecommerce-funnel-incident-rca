E-Commerce Checkout Funnel Leakage & Incident RCA: Diagnosing a Mobile Conversion Failure in Google BigQuery1. Executive Summary & Business ProblemOver a 48-hour weekend window, CloudCart D2C ($45M ARR) experienced a steep decline in storewide conversion rate from 3.8% to 2.4%, occurring simultaneously with an ongoing $15,000/day paid ad spend ($30,000 total across Meta and Google acquisition channels).By analyzing event-stream telemetry in Google BigQuery, this investigation identified:The Funnel Leak: Mobile checkout-to-purchase conversion collapsed from a baseline of 30.0% down to 1.43%. Desktop remained healthy at 30.0%, proving that top-of-funnel traffic quality was not the cause.The Root Cause: 14,820 mobile checkout sessions failed due to an apple_pay_token_failure error triggered by engineering release v4.12.0 deployed on Friday evening.The Financial Impact: An estimated ~$741,000 in lost gross merchandise value (GMV) and wasted marketing spend before remediation.2. Telemetry Architecture & Repository LayoutClickstream events were logged via standard e-commerce event streams into Google BigQuery:raw_events.web_sessions_stream: User session milestones (session_start, view_item, add_to_cart, begin_checkout, purchase).raw_events.checkout_errors_stream: Transaction failure logs, error codes, and device details.├── README.md                      # Complete executive post-mortem and findings
├── generate_data.py               # Python event generator harness
├── sql/
│   ├── 01_pipeline_audit.sql      # Ingestion integrity & null checks
│   ├── 02_funnel_lag.sql          # Step-by-step conversion & drop-off rates
│   ├── 03_device_segmentation.sql # Slicing checkout conversion by device
│   └── 04_error_rca.sql           # Querying root-cause error codes
└── assets/
    ├── 01_pipeline_integrity_audit.png
    ├── 02_device_funnel_leak.png
    └── 03_error_root_cause.png
3. Investigation & SQL AnalysisStep 1: Ingestion Audit & Data Integrity CheckBefore diagnosing funnel drop-offs, telemetry data was audited to confirm zero corrupted rows, validate date boundaries (Nov 3 – Nov 9, 2026), and check for null keys across 50,000 sessions.File: sql/01_pipeline_audit.sqlSELECT 
    COUNT(*) AS total_event_rows,
    COUNT(DISTINCT session_id) AS total_unique_sessions,
    COUNTIF(session_id IS NULL) AS null_sessions,
    COUNTIF(event_name IS NULL) AS null_events,
    MIN(event_timestamp) AS earliest_event,
    MAX(event_timestamp) AS latest_event
FROM `cloudcart-analytics.raw_events.web_sessions_stream`;
BigQuery Evidence (Integrity Verification):Step 2: Funnel Drop-off Quantification (Window Functions)To locate where prospective buyers abandoned the journey, step-by-step conversion and drop-off rates were modeled using the LAG() window function across ordered funnel stages.$$\text{Step Conversion Rate} = \frac{\text{Current Step Sessions}}{\text{LAG}(\text{Current Step Sessions})}$$$$\text{Drop-off Rate} = 100\% - \text{Step Conversion Rate}$$File: sql/02_funnel_lag.sqlWITH funnel_stages AS (
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
    ROUND(100.0 * total_sessions / LAG(total_sessions) OVER (ORDER BY step_order), 2) AS step_conversion_pct,
    ROUND(100.0 * (LAG(total_sessions) OVER (ORDER BY step_order) - total_sessions) / 
          LAG(total_sessions) OVER (ORDER BY step_order), 2) AS dropoff_pct
FROM funnel_stages
ORDER BY step_order;
Funnel Stage Results:session_start $\rightarrow$ view_item: 82.0% conversion (18.0% drop-off) — Healthyview_item $\rightarrow$ add_to_cart: 40.0% conversion (60.0% drop-off) — Healthyadd_to_cart $\rightarrow$ begin_checkout: 75.0% conversion (25.0% drop-off) — Healthybegin_checkout $\rightarrow$ purchase: 10.0% conversion (90.0% drop-off) — Critical FailureThe top and middle funnel stages were completely normal; the failure was concentrated at the final payment step.Step 3: Segmentation by Device CategoryTo determine whether this leak was device-specific, checkout completion was segmented across device types.File: sql/03_device_segmentation.sqlSELECT 
    device_category,
    COUNT(DISTINCT session_id) AS total_sessions,
    COUNT(DISTINCT CASE WHEN event_name = 'begin_checkout' THEN session_id END) AS checkout_sessions,
    COUNT(DISTINCT CASE WHEN event_name = 'purchase' THEN session_id END) AS purchase_sessions,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN event_name = 'purchase' THEN session_id END) / 
          NULLIF(COUNT(DISTINCT CASE WHEN event_name = 'begin_checkout' THEN session_id END), 0), 2) AS checkout_to_purchase_pct
FROM `cloudcart-analytics.raw_events.web_sessions_stream`
GROUP BY device_category
ORDER BY total_sessions DESC;
Device CategoryCheckout SessionsPurchase SessionsCheckout → Purchase ConversionStatusDesktop7,3802,21430.00%Nominal (Baseline)Tablet1,84054229.45%Nominal (Baseline)Mobile17,2202461.43%CRITICAL COLLAPSE (-95%)BigQuery Evidence (Device Breakdown):Desktop and tablet checkouts converted at expected baselines (~30%). Mobile checkout experienced an isolated 95% performance collapse.Step 4: Root Cause Identification via Error LogsQuerying failed transaction logs over the incident window isolated the exact technical cause.File: sql/04_error_rca.sqlSELECT 
    error_type,
    os_version,
    COUNT(DISTINCT session_id) AS impacted_sessions
FROM `cloudcart-analytics.raw_events.checkout_errors_stream`
WHERE device_category = 'mobile'
  AND error_timestamp >= '2026-11-06 18:30:00 UTC'
GROUP BY error_type, os_version
ORDER BY impacted_sessions DESC;
Error TypeOperating SystemImpacted Unique Sessionsapple_pay_token_failureiOS 18.114,820card_declinedMultiple1,140insufficient_fundsMultiple890gateway_timeoutMultiple180BigQuery Evidence (Error Breakdown):Finding: apple_pay_token_failure accounted for 85%+ of all mobile failures.Correlated Trigger: Engineering release v4.12.0 was deployed on Friday at 18:30 UTC with updates to the iOS Apple Pay decryption endpoint.4. Financial Impact Analysis$$\text{Impacted Mobile Sessions} = 14,820$$$$\text{Average Order Value (AOV)} = \$50.00$$$$\text{Lost Revenue} = 14,820 \times \$50.00 = \mathbf{\$741,000}$$Paid Media Drain: CloudCart spent $30,000 over Saturday and Sunday on mobile ads that routed traffic directly into an inoperable payment gateway.Total Incident Exposure: Approximately $771,000 in lost revenue and inefficient ad spend.5. Remediation & Operational Measures TakenImmediate P0 Rollback: Rolled back the checkout service to previous release v4.11.9, restoring mobile checkout conversion back to the 30% baseline.Automated Campaign Guardrails: Configured a real-time alerting rule: if mobile checkout conversion drops below 20% over a 2-hour window, paid ad spend is paused automatically via webhook.Cart Recovery Outreach: Segmented the 14,820 impacted user IDs for an automated email/SMS recovery flow containing an alternative payment link (Card / PayPal).
