# E-Commerce Checkout Funnel Leakage & Incident RCA
## Diagnosing a Mobile Conversion Failure in Google BigQuery

## 1. Executive Summary & Business Problem

Over a 48-hour weekend window, **CloudCart D2C ($45M ARR)** experienced a steep decline in storewide conversion rate from **3.8% to 2.4%**, occurring simultaneously with an ongoing **$15,000/day paid ad spend** ($30,000 total across Meta and Google acquisition channels).

By analyzing event-stream telemetry in Google BigQuery, this investigation identified:

- **The Funnel Leak:** Mobile checkout-to-purchase conversion collapsed from a baseline of 30.0% to just 1.43%. Desktop remained healthy at 30.0%, proving that top-of-funnel traffic quality was not the cause.
- **The Root Cause:** 14,820 mobile checkout sessions failed due to an `apple_pay_token_failure` error triggered by engineering release `v4.12.0`, deployed on Friday evening.
- **The Financial Impact:** An estimated **$741,000 in lost gross merchandise value (GMV)** and wasted marketing spend before remediation.

---

## 2. Telemetry Architecture & Repository Layout

Clickstream events were logged via standard e-commerce event streams into Google BigQuery:

| BigQuery Table | Description |
|---|---|
| `raw_events.web_sessions_stream` | User session milestones (`session_start`, `view_item`, `add_to_cart`, `begin_checkout`, `purchase`). |
| `raw_events.checkout_errors_stream` | Transaction failure logs, error codes, and device details. |

### Repository Structure

```text
├── README.md                      # Complete executive post-mortem and findings
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
```

---

## 3. Investigation & SQL Analysis

### Step 1: Ingestion Audit & Data Integrity Check

Before diagnosing funnel drop-offs, telemetry data was audited to confirm zero corrupted rows, validate date boundaries (November 3–9, 2026), and check for null keys across 50,000 sessions.

**File:** `sql/01_pipeline_audit.sql`

```sql
SELECT 
    COUNT(*) AS total_event_rows,
    COUNT(DISTINCT session_id) AS total_unique_sessions,
    COUNTIF(session_id IS NULL) AS null_sessions,
    COUNTIF(event_name IS NULL) AS null_events,
    MIN(event_timestamp) AS earliest_event,
    MAX(event_timestamp) AS latest_event
FROM `cloudcart-analytics.raw_events.web_sessions_stream`;
```

**BigQuery Evidence:** Integrity Verification

### Step 2: Funnel Drop-off Quantification (Window Functions)

To locate where prospective buyers abandoned the journey, step-by-step conversion and drop-off rates were modeled using the `LAG()` window function across ordered funnel stages.

**Step Conversion Rate:**

$$
\text{Step Conversion Rate} =
\frac{\text{Current Step Sessions}}{\text{Previous Step Sessions}} \times 100
$$

**Drop-off Rate:**

$$
\text{Drop-off Rate} = 100\% - \text{Step Conversion Rate}
$$

**File:** `sql/02_funnel_lag.sql`

```sql
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
    ROUND(
        100.0 * total_sessions / LAG(total_sessions) OVER (ORDER BY step_order),
        2
    ) AS step_conversion_pct,
    ROUND(
        100.0 * (
            LAG(total_sessions) OVER (ORDER BY step_order) - total_sessions
        ) / LAG(total_sessions) OVER (ORDER BY step_order),
        2
    ) AS dropoff_pct
FROM funnel_stages
ORDER BY step_order;
```

**Funnel Stage Results**

| Funnel Transition | Conversion Rate | Drop-off Rate | Status |
|---|---:|---:|---|
| `session_start` → `view_item` | 82.0% | 18.0% | Healthy |
| `view_item` → `add_to_cart` | 40.0% | 60.0% | Healthy |
| `add_to_cart` → `begin_checkout` | 75.0% | 25.0% | Healthy |
| `begin_checkout` → `purchase` | 10.0% | 90.0% | Critical Failure |

The top and middle funnel stages were functioning normally. The failure was concentrated at the final payment step.

### Step 3: Segmentation by Device Category

To determine whether this leak was device-specific, checkout completion was segmented across device types.

**File:** `sql/03_device_segmentation.sql`

```sql
SELECT 
    device_category,
    COUNT(DISTINCT session_id) AS total_sessions,
    COUNT(DISTINCT CASE 
        WHEN event_name = 'begin_checkout' THEN session_id 
    END) AS checkout_sessions,
    COUNT(DISTINCT CASE 
        WHEN event_name = 'purchase' THEN session_id 
    END) AS purchase_sessions,
    ROUND(
        100.0 * COUNT(DISTINCT CASE 
            WHEN event_name = 'purchase' THEN session_id 
        END) / NULLIF(
            COUNT(DISTINCT CASE 
                WHEN event_name = 'begin_checkout' THEN session_id 
            END), 0
        ),
        2
    ) AS checkout_to_purchase_pct
FROM `cloudcart-analytics.raw_events.web_sessions_stream`
GROUP BY device_category
ORDER BY total_sessions DESC;
```

**Device Category Breakdown**

| Device Category | Checkout Sessions | Purchase Sessions | Checkout → Purchase Conversion | Status |
|---|---:|---:|---:|---|
| Desktop | 7,380 | 2,214 | 30.00% | Nominal (Baseline) |
| Tablet | 1,840 | 542 | 29.45% | Nominal (Baseline) |
| Mobile | 17,220 | 246 | 1.43% | CRITICAL COLLAPSE (-95%) |

**BigQuery Evidence:** Device Breakdown

Desktop and tablet checkouts converted at expected baselines (~30%). Mobile checkout experienced an isolated 95% performance collapse.

### Step 4: Root Cause Identification via Error Logs

Querying failed transaction logs over the incident window isolated the exact technical cause.

**File:** `sql/04_error_rca.sql`

```sql
SELECT 
    error_type,
    os_version,
    COUNT(DISTINCT session_id) AS impacted_sessions
FROM `cloudcart-analytics.raw_events.checkout_errors_stream`
WHERE device_category = 'mobile'
  AND error_timestamp >= '2026-11-06 18:30:00 UTC'
GROUP BY error_type, os_version
ORDER BY impacted_sessions DESC;
```

**Error Breakdown**

| Error Type | Operating System | Impacted Unique Sessions |
|---|---|---:|
| `apple_pay_token_failure` | iOS 18.1 | 14,820 |
| `card_declined` | Multiple | 1,140 |
| `insufficient_funds` | Multiple | 890 |
| `gateway_timeout` | Multiple | 180 |

**Finding:** `apple_pay_token_failure` accounted for over 85% of all mobile failures.

**Correlated Trigger:** Engineering release `v4.12.0` was deployed on Friday at 18:30 UTC, introducing updates to the iOS Apple Pay decryption endpoint.

---

## 4. Financial Impact Analysis

The incident's financial exposure was estimated using impacted mobile checkout sessions and the average order value.

**Impacted Mobile Sessions:**

$$
14,820
$$

**Average Order Value (AOV):**

$$
\$50.00
$$

**Estimated Lost Revenue:**

$$
14,820 \times \$50 = \boxed{\$741,000}
$$

**Paid Media Drain:**

CloudCart spent $30,000 over Saturday and Sunday on mobile ads that routed traffic directly into an inoperable payment gateway.

**Total Incident Exposure:**

| Financial Impact | Estimated Amount |
|---|---:|
| Lost Gross Merchandise Value (GMV) | $741,000 |
| Inefficient Paid Media Spend | $30,000 |
| **Total Incident Exposure** | **$771,000** |

*Note: These figures represent estimated incident exposure, not independently verified realized losses. The GMV estimate assumes every impacted checkout session would otherwise have generated an order at the $50 AOV.*

---

## 5. Remediation & Operational Measures Taken

### 1. Immediate P0 Rollback

Rolled back the checkout service to the previous release, `v4.11.9`, restoring mobile checkout conversion to the 30% baseline.

### 2. Automated Campaign Guardrails

Configured a real-time alerting rule:

- **Trigger:** Mobile checkout conversion drops below 20% over a rolling 2-hour window.
- **Action:** Paid advertising spend is automatically paused through a webhook.
- **Objective:** Prevent marketing budgets from continuing to drive traffic into a failing checkout experience.

### 3. Cart Recovery Outreach

Segmented the 14,820 impacted user IDs for an automated email/SMS recovery flow containing an alternative payment link (Card / PayPal).

---

## 6. Key Business Takeaways

- **Revenue Protection:** Checkout conversion monitoring should be treated as a critical business metric, not just a technical performance indicator.
- **Device-Level Monitoring:** Storewide conversion metrics can hide severe device-specific failures. Mobile, desktop, and tablet performance should be monitored independently.
- **Release Management:** Payment-related deployments require targeted regression testing, particularly across device and operating-system combinations.
- **Marketing Efficiency:** Advertising campaigns should have automated safeguards that respond to real-time checkout health.

## 7. Technology Stack

| Technology | Application |
|---|---|
| Google BigQuery | Event-stream querying and analytical investigation |
| SQL | Funnel analysis, window functions, segmentation, and root-cause analysis |
| Python | Synthetic event-data generation |
| GitHub | Version control and project documentation |

---

**Project Focus:** E-Commerce Analytics | Conversion Rate Optimization | Incident Root Cause Analysis | Revenue Impact Assessment | Google BigQuery | SQL
