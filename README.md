# E-Commerce Checkout Funnel Leakage & Incident RCA
## Diagnosing a Mobile Conversion Failure in Google BigQuery

---

## 1. Executive Summary & Business Problem

Following engineering release `v4.12.0` on Friday evening (2026-11-06 18:30:00 UTC), CloudCart D2C experienced a severe decline in checkout completions over the weekend while active paid customer acquisition campaigns continued running.

By querying raw clickstream event streams and failure telemetry in Google BigQuery, this root cause analysis (RCA) revealed:

- **The Funnel Leak:** Overall checkout-to-purchase conversion dropped from a pre-incident baseline of **29.37% to 11.22%** following the deployment.
- **Device Isolation:** The failure was isolated to mobile devices. During the incident window, Desktop converted at **30.21%** and Tablet at **25.51%**, while Mobile collapsed to **1.08%** (only 40 purchases from 3,694 checkout sessions).
- **The Root Cause:** **3,091 unique mobile sessions** encountered an `apple_pay_token_failure` exception on iOS 18.1, representing **84.57% of recorded post-deployment mobile checkout errors**.
- **The Business Impact:** An estimated **1,068 lost completed customer orders**, representing approximately **$53,400 in lost Gross Merchandise Value (GMV)**, excluding additional inefficient weekend advertising spend.

---

## 2. Telemetry Architecture & Repository Layout

Clickstream data and backend exceptions are captured into Google BigQuery as append-only event streams.

| BigQuery Table | Description |
|---|---|
| `raw_events.web_sessions_stream` | Core user progression milestones (`session_start`, `view_item`, `add_to_cart`, `begin_checkout`, `purchase`). |
| `raw_events.checkout_errors_stream` | Granular payment gateway exceptions, error codes, and client device telemetry. |

### Repository Structure

```text
├── README.md                          # Executive incident post-mortem & RCA report
├── generate_data.py                   # Clickstream and error stream simulation harness
└── sql/
    ├── 01_pipeline_audit.sql           # Ingestion integrity & boundary verification
    ├── 02_funnel_macro_lag.sql         # 5-stage conversion & drop-off calculation
    ├── 03_incident_window_split.sql    # Pre- vs. post-deployment cohort comparison
    ├── 04_device_segmentation.sql      # Post-deploy conversion sliced by device
    └── 05_error_rca.sql                # Error code & OS distribution analysis
```

---

## 3. SQL Investigation & Analytical Progression

### Step 1: Ingestion Audit & Data Integrity Check

Before evaluating funnel progression, ingestion completeness, boundary timestamps, and key uniqueness were audited across the dataset.

**File:** `sql/01_pipeline_audit.sql`

```sql
SELECT 
    COUNT(*) AS total_rows,
    COUNT(DISTINCT session_id) AS total_unique_sessions,
    COUNTIF(session_id IS NULL) AS null_sessions,
    COUNTIF(event_name IS NULL) AS null_events,
    MIN(event_timestamp) AS earliest_event,
    MAX(event_timestamp) AS latest_event
FROM `cloudcart-analytics.raw_events.web_sessions_stream`;
```

#### Query Results

| total_rows | total_unique_sessions | null_sessions | null_events | earliest_event | latest_event |
|---:|---:|---:|---:|---|---|
| 122,385 | 50,000 | 0 | 0 | 2026-11-03 00:00:17 UTC | 2026-11-10 00:00:25 UTC |

**Validation:** The telemetry pipeline passed the initial integrity checks. All 50,000 unique sessions were represented across the seven-day observation window, with zero null session identifiers or event names.

---

### Step 2: Funnel Drop-off Quantification (Window Functions)

To identify where prospective customers dropped out of the purchasing flow, step-by-step conversion and abandonment rates were calculated across all five chronological milestones using the `LAG()` window function.

**Step Conversion Rate:**

`Step Conversion Rate (%) = (Current Step Sessions / Previous Step Sessions) * 100`

**Drop-off Rate:**

`Drop-off Rate (%) = 100 - Step Conversion Rate (%)`

**File:** `sql/02_funnel_macro_lag.sql`

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
    ) AS conversion_rate_pct,
    ROUND(
        100.0 * (
            LAG(total_sessions) OVER (ORDER BY step_order) - total_sessions
        ) / LAG(total_sessions) OVER (ORDER BY step_order),
        2
    ) AS dropoff_pct
FROM funnel_stages
ORDER BY step_order;
```

#### Query Results

| step_order | event_name | total_sessions | prev_step_sessions | conversion_rate_pct | dropoff_pct | Operational Status |
|---:|---|---:|---:|---:|---:|---|
| 1 | session_start | 50,000 | — | — | — | Entry Stage |
| 2 | view_item | 41,028 | 50,000 | 82.06% | 17.94% | Nominal |
| 3 | add_to_cart | 16,430 | 41,028 | 40.05% | 59.95% | Nominal |
| 4 | begin_checkout | 12,347 | 16,430 | 75.15% | 24.85% | Nominal |
| 5 | purchase | 2,580 | 12,347 | **20.90%** | **79.10%** | **CRITICAL FUNNEL LEAK** |

**Diagnosis:** Top and mid-funnel transition steps performed consistently with the observed traffic pattern. The most significant funnel leakage occurred between `begin_checkout` and `purchase`, indicating a potential checkout completion issue requiring further investigation.

---

### Step 3: Timeline Partitioning (Pre- vs. Post-Deployment)

To establish whether the checkout drop-off was a persistent trend or associated with release `v4.12.0`, sessions were partitioned before and after the deployment cut-off timestamp (`2026-11-06 18:30:00 UTC`).

**File:** `sql/03_incident_window_split.sql`

```sql
WITH cohort_funnel AS (
    SELECT 
        CASE 
            WHEN event_timestamp < '2026-11-06 18:30:00 UTC'
                THEN '1_Pre_Incident_Baseline'
            ELSE '2_Post_Deploy_Incident'
        END AS incident_window,
        COUNT(DISTINCT CASE 
            WHEN event_name = 'begin_checkout' THEN session_id 
        END) AS checkout_sessions,
        COUNT(DISTINCT CASE 
            WHEN event_name = 'purchase' THEN session_id 
        END) AS purchase_sessions
    FROM `cloudcart-analytics.raw_events.web_sessions_stream`
    GROUP BY incident_window
)
SELECT 
    incident_window,
    checkout_sessions,
    purchase_sessions,
    ROUND(
        100.0 * purchase_sessions / NULLIF(checkout_sessions, 0),
        2
    ) AS checkout_to_purchase_rate_pct
FROM cohort_funnel
ORDER BY incident_window;
```

#### Query Results

| incident_window | checkout_sessions | purchase_sessions | checkout_to_purchase_rate_pct | Performance Delta |
|---|---:|---:|---:|---|
| 1_Pre_Incident_Baseline | 6,582 | 1,933 | **29.37%** | Expected Production Baseline |
| 2_Post_Deploy_Incident | 5,765 | 647 | **11.22%** | **-61.8% Efficiency Collapse** |

**Diagnosis:** Before Friday evening, checkout conversion stood at 29.37%. Following the 18:30 UTC deployment, overall checkout-to-purchase performance declined to 11.22%, representing a 61.8% relative reduction from baseline.

This temporal relationship provided a strong reason to investigate the newly deployed checkout service.

---

### Step 4: Device Segmentation During Incident Window

To evaluate platform variance during the degraded period, post-deployment checkouts were segmented across device types using defensive zero-handling with `NULLIF()`.

**File:** `sql/04_device_segmentation.sql`

```sql
SELECT 
    device_category,
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
    ) AS checkout_to_purchase_rate_pct
FROM `cloudcart-analytics.raw_events.web_sessions_stream`
WHERE event_timestamp >= '2026-11-06 18:30:00 UTC'
GROUP BY device_category
ORDER BY checkout_sessions DESC;
```

#### Query Results

| device_category | checkout_sessions | purchase_sessions | checkout_to_purchase_rate_pct | Operational Status |
|---|---:|---:|---:|---|
| **desktop** | 1,675 | 506 | **30.21%** | Nominal (Healthy) |
| **tablet** | 396 | 101 | **25.51%** | Nominal (Healthy) |
| **mobile** | 3,694 | 40 | **1.08%** | **CRITICAL COLLAPSE (-96.4%)** |

**Diagnosis:** Desktop and tablet checkout pipelines continued operating at relatively healthy conversion levels throughout the incident window. Mobile checkout conversion, however, collapsed to just 1.08%, representing a 96.4% relative decline against the approximately 30% baseline.

This isolated the primary performance failure to the mobile checkout experience.

---

### Step 5: Root Cause Identification via Error Logs

Querying `raw_events.checkout_errors_stream` for the mobile cohort during the active outage isolated the technical defect.

**File:** `sql/05_error_rca.sql`

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

#### Query Results

| error_type | os_version | impacted_sessions | % of Total Mobile Errors |
|---|---|---:|---:|
| **apple_pay_token_failure** | **iOS 18.1** | **3,091** | **84.57%** |
| card_declined | iOS 18.0 | 308 | 8.43% |
| insufficient_funds | iOS 18.0 | 154 | 4.21% |
| gateway_timeout | iOS 18.0 | 68 | 1.86% |
| ui_script_error | iOS 18.0 | 34 | 0.93% |

**Technical Defect:** Friday's `v4.12.0` release introduced a defect in the decryption handshake handling Apple Pay authorization tokens on iOS 18.1 devices.

**Volume Alignment:** The recorded mobile error counts total 3,655, closely matching the 3,654 mobile checkout sessions that did not generate a purchase event.

- Total recorded mobile errors: 3,655
- Mobile checkout sessions without a purchase: 3,654
- Difference: 1 session

This close alignment further supports the conclusion that the payment failure was responsible for the majority of the observed mobile checkout disruption.

---

## 4. Financial & Business Impact Analysis

Financial impact was estimated by comparing expected mobile order volume at the pre-incident conversion baseline against actual completed orders.

### 1. Expected Mobile Order Volume

`3,694 checkout sessions × 30.0% baseline conversion = 1,108 expected orders`

### 2. Actual Mobile Orders Completed

`40 actual orders`

### 3. Net Lost Orders

`1,108 expected orders - 40 actual orders = 1,068 lost orders`

### 4. Estimated Lost Gross Merchandise Value

`1,068 lost orders × $50.00 AOV = $53,400`

### 5. Customer Acquisition Inefficiency

Mobile-targeted advertising across Meta and Google continued directing high-intent shoppers into a malfunctioning checkout experience.

This created additional customer acquisition inefficiency, as advertising expenditure continued generating traffic that could not reliably complete purchases.

**Financial Impact Summary**

| Impact Category | Estimated Value |
|---|---:|
| Expected Mobile Orders | 1,108 |
| Actual Mobile Orders | 40 |
| Estimated Lost Orders | 1,068 |
| Average Order Value | $50.00 |
| **Estimated Lost GMV** | **$53,400** |
| Additional Inefficient Ad Spend | Not separately quantified |

*Note: The $53,400 figure is an estimated gross merchandise value opportunity loss, not independently verified realized revenue loss. It assumes the affected checkout sessions would otherwise have converted at the 30% baseline and generated orders at the $50 average order value. Additional advertising inefficiency is acknowledged but excluded from the quantified total because the mobile-specific wasted spend was not separately established.*

---

## 5. Remediation & Operational Measures Taken

### 1. P0 Service Rollback

Rolled back the checkout microservice to the previous stable release, `v4.11.9`, to reverse the defective Apple Pay decryption changes and restore the mobile checkout experience.

### 2. Automated Conversion Circuit Breakers

Established an automated monitoring and alerting rule using Google Cloud Monitoring and BigQuery.

- **Monitoring Rule:** If mobile checkout-to-purchase conversion drops below 20% over a rolling two-hour window, trigger an automated P1 incident alert.
- **Automated Response:** Trigger a webhook to pause active mobile advertising campaigns.
- **Business Objective:** Prevent continued marketing expenditure from driving high-intent traffic into a degraded payment experience.

### 3. Customer Recovery Pipeline

Exported the 3,091 session IDs associated with `apple_pay_token_failure` into the CRM for a targeted recovery campaign.

The proposed recovery flow offers affected customers an alternative payment method, including Credit Card or PayPal, to encourage successful order completion.

---

## 6. Strategic Takeaways

- **Granular Cohort Monitoring:** Aggregate conversion metrics can obscure critical segment-level outages. Device, browser, operating system, and payment gateway health should be monitored through dedicated operational views.
- **Pre-Deployment Gateway Validation:** Release pipelines involving checkout services or payment SDKs require end-to-end sandbox authorization tests across all supported operating systems before general deployment.
- **Marketing & Product Integration:** Real-time feedback loops between conversion analytics and advertising systems can prevent continued budget allocation to degraded customer journeys.
- **Revenue Protection:** Checkout conversion should be treated as a critical business health metric, with automated monitoring and incident escalation procedures.

---

## 7. Technology Stack

| Technology | Usage |
|---|---|
| **Google BigQuery** | Cloud data warehouse, event-stream querying, and analytics |
| **GoogleSQL** | Window functions (`LAG`), conditional aggregations (`COUNTIF`), defensive calculations (`NULLIF`), and cohort CTEs |
| **Python** | Synthetic clickstream generation and deterministic error modeling |
| **GitHub** | Version control and analytical documentation |

---

**Project Focus:** E-Commerce Analytics | Conversion Rate Optimization | Incident Root Cause Analysis | Revenue Impact Assessment | Google BigQuery | SQL
