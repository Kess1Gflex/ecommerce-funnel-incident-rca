# E-Commerce Checkout Funnel Leakage & Incident RCA: Diagnosing a Mobile Conversion Failure in Google BigQuery

---

## 1. Executive Summary & Business Problem

Following engineering release `v4.12.0` on Friday evening (2026-11-06 18:30:00 UTC), CloudCart D2C experienced a severe, unexplained drop in checkout completions over the weekend, occurring while active paid customer acquisition campaigns were running.

By querying raw clickstream event streams and failure telemetry in Google BigQuery, this root cause analysis (RCA) revealed:

* **The Funnel Leak:** While top-of-funnel conversion remained standard, checkout-to-purchase completion dropped from a pre-incident baseline of **29.37% down to 11.22%**.
* **Device Isolation:** Slicing performance across hardware confirmed the failure was isolated to mobile devices. During the incident window, **Desktop converted at 30.21%** and **Tablet at 25.51%**, while **Mobile collapsed to 1.08%** (only 40 purchases from 3,694 checkout sessions).
* **The Root Cause:** **3,091 unique mobile sessions** encountered an `apple_pay_token_failure` exception on iOS 18.1, representing **84.57%** of all post-deploy mobile checkout errors.
* **The Business Impact:** A net loss of **1,068 completed customer orders**, representing **~$53,400 in lost Gross Merchandise Value (GMV)** and wasted weekend ad spend.

---

## 2. Telemetry Architecture & Repository Layout

Clickstream data and backend exceptions are captured into Google BigQuery as append-only event streams:

| BigQuery Table | Description |
| --- | --- |
| `raw_events.web_sessions_stream` | Core user progression milestones (`session_start`, `view_item`, `add_to_cart`, `begin_checkout`, `purchase`). |
| `raw_events.checkout_errors_stream` | Granular payment gateway exceptions, error codes, and client device telemetry. |

### Repository Structure

```text
├── README.md                          # Executive incident post-mortem & RCA report
├── generate_data.py                   # Clickstream and error stream simulation harness
└── sql/
    ├── 01_pipeline_audit.sql          # Ingestion integrity & boundary verification
    ├── 02_funnel_macro_lag.sql        # 5-stage conversion & drop-off calculation
    ├── 03_incident_window_split.sql   # Pre- vs. post-deployment cohort comparison
    ├── 04_device_segmentation.sql     # Post-deploy conversion sliced by device
    └── 05_error_rca.sql               # Error code & OS distribution analysis

```

---

## 3. SQL Investigation & Analytical Progression

### Step 1: Ingestion Audit & Data Integrity Check

Before evaluating funnel progression, ingestion completeness, boundary timestamps, and key uniqueness were audited across the dataset.

*File: `sql/01_pipeline_audit.sql*`

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

#### Query Results:

| total_rows | total_unique_sessions | null_sessions | null_events | earliest_event | latest_event |
| --- | --- | --- | --- | --- | --- |
| 122,385 | 50,000 | 0 | 0 | 2026-11-03 00:00:17 UTC | 2026-11-10 00:00:25 UTC |

*Validation: Telemetry pipeline is healthy; all 50,000 unique sessions loaded across the 7-day observation window with zero null foreign keys.*

---

### Step 2: Funnel Drop-off Quantification (Window Functions)

To identify where prospective customers dropped out of the purchasing flow, step-by-step conversion and abandonment rates were calculated across all 5 chronological milestones using `LAG()`.

$$\text{Step Conversion Rate} = \frac{\text{Current Step Sessions}}{\text{Previous Step Sessions}} \times 100$$

$$\text{Drop-off Rate} = 100\% - \text{Step Conversion Rate}$$

*File: `sql/02_funnel_macro_lag.sql*`

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
    ROUND(100.0 * total_sessions / LAG(total_sessions) OVER (ORDER BY step_order), 2) AS conversion_rate_pct,
    ROUND(100.0 * (LAG(total_sessions) OVER (ORDER BY step_order) - total_sessions) / 
          LAG(total_sessions) OVER (ORDER BY step_order), 2) AS dropoff_pct
FROM funnel_stages
ORDER BY step_order;

```

#### Query Results:

| step_order | event_name | total_sessions | prev_step_sessions | conversion_rate_pct | dropoff_pct | Operational Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | session_start | 50,000 | — | — | — | Entry Stage |
| 2 | view_item | 41,028 | 50,000 | 82.06% | 17.94% | Nominal |
| 3 | add_to_cart | 16,430 | 41,028 | 40.05% | 59.95% | Nominal |
| 4 | begin_checkout | 12,347 | 16,430 | 75.15% | 24.85% | Nominal |
| 5 | purchase | 2,580 | 12,347 | **20.90%** | **79.10%** | **CRITICAL FUNNEL LEAK** |

*Diagnosis: Top and mid-funnel transition steps performed within standard retail benchmarks. The severe funnel leakage is isolated specifically between `begin_checkout` and `purchase`.*

---

### Step 3: Timeline Partitioning (Pre- vs. Post-Deployment)

To establish whether the checkout drop-off was a persistent trend or caused by release `v4.12.0`, sessions were partitioned before and after the deployment cut-off timestamp (`2026-11-06 18:30:00 UTC`).

*File: `sql/03_incident_window_split.sql*`

```sql
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

```

#### Query Results:

| incident_window | checkout_sessions | purchase_sessions | checkout_to_purchase_rate_pct | Performance Delta |
| --- | --- | --- | --- | --- |
| 1_Pre_Incident_Baseline | 6,582 | 1,933 | **29.37%** | Expected Production Baseline |
| 2_Post_Deploy_Incident | 5,765 | 647 | **11.22%** | **-61.8% Efficiency Collapse** |

*Diagnosis: The checkout baseline before Friday evening sat at 29.37%. Performance degraded immediately following the 18:30:00 UTC deployment.*

---

### Step 4: Device Segmentation During Incident Window

To evaluate platform variance during the degraded period, post-deployment checkouts were segmented across device types using defensive zero-handling (`NULLIF`).

*File: `sql/04_device_segmentation.sql*`

```sql
SELECT 
    device_category,
    COUNT(DISTINCT CASE WHEN event_name = 'begin_checkout' THEN session_id END) AS checkout_sessions,
    COUNT(DISTINCT CASE WHEN event_name = 'purchase' THEN session_id END) AS purchase_sessions,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN event_name = 'purchase' THEN session_id END) / 
          NULLIF(COUNT(DISTINCT CASE WHEN event_name = 'begin_checkout' THEN session_id END), 0), 2) AS checkout_to_purchase_rate_pct
FROM `cloudcart-analytics.raw_events.web_sessions_stream`
WHERE event_timestamp >= '2026-11-06 18:30:00 UTC'
GROUP BY device_category
ORDER BY checkout_sessions DESC;

```

#### Query Results:

| device_category | checkout_sessions | purchase_sessions | checkout_to_purchase_rate_pct | Operational Status |
| --- | --- | --- | --- | --- |
| **desktop** | 1,675 | 506 | **30.21%** | Nominal (Healthy) |
| **tablet** | 396 | 101 | **25.51%** | Nominal (Healthy) |
| **mobile** | 3,694 | 40 | **1.08%** | **CRITICAL COLLAPSE (-96.4%)** |

*Diagnosis: Desktop and tablet checkout pipelines operated nominally throughout the weekend. The conversion failure was concentrated entirely on mobile sessions.*

---

### Step 5: Root Cause Identification via Error Logs

Querying `raw_events.checkout_errors_stream` for the mobile cohort during the active outage isolated the technical defect.

*File: `sql/05_error_rca.sql*`

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

#### Query Results:

| error_type | os_version | impacted_sessions | % of Total Mobile Errors |
| --- | --- | --- | --- |
| **apple_pay_token_failure** | **iOS 18.1** | **3,091** | **84.57%** |
| card_declined | iOS 18.0 | 308 | 8.43% |
| insufficient_funds | iOS 18.0 | 154 | 4.21% |
| gateway_timeout | iOS 18.0 | 68 | 1.86% |
| ui_script_error | iOS 18.0 | 34 | 0.93% |

* **Technical Defect:** Friday's `v4.12.0` release broke the decryption handshake handling Apple Pay authorization tokens on iOS 18.1 devices.
* **Volume Alignment:** Total recorded mobile errors during the window ($3,091 + 308 + 154 + 68 + 34 = 3,655$) align directly with the volume of uncompleted mobile checkouts ($3,694 - 40 = 3,654$).

---

## 4. Financial & Business Impact Analysis

Financial impact was derived by calculating lost conversion volume against the store's baseline performance and average order value (AOV):

1. **Expected Mobile Order Volume (at ~30.0% Baseline):**

$$3,694 \times 30.0\% = 1,108\text{ orders}$$


2. **Actual Mobile Orders Completed:**

$$40\text{ orders}$$


3. **Net Lost Orders:**

$$1,108 - 40 = \mathbf{1,068\text{ lost orders}}$$


4. **Estimated Lost Gross Merchandise Value (at $50.00 AOV):**

$$1,068 \times \$50.00 = \mathbf{\$53,400}$$


5. **Customer Acquisition Inefficiency:**
Mobile-targeted ad spend across Meta and Google directed high-intent shoppers into a non-functional checkout modal, temporarily inflating customer acquisition costs (CAC).

---

## 5. Remediation & Operational Measures Taken

1. **P0 Service Rollback:** Rolled back the checkout microservice to previous stable release `v4.11.9`, restoring mobile checkout completion back to the 30% baseline.
2. **Automated Conversion Circuit Breakers:** Created an automated alerting check in Google Cloud Monitoring / BigQuery:
* **Rule:** If mobile checkout-to-purchase conversion drops below 20% over any rolling 2-hour window, trigger an automated P1 incident alert and trigger a webhook to pause active mobile ad spend.


3. **Customer Recovery Pipeline:** Exported the 3,091 session IDs affected by `apple_pay_token_failure` into the CRM for an automated recovery campaign offering an alternative one-click payment method (Credit Card / PayPal).

---

## 6. Strategic Takeaways

* **Granular Cohort Monitoring:** Aggregate conversion metrics can obscure critical segment-level outages. Device, browser, and payment gateway health must be monitored via dedicated operational views.
* **Pre-Deployment Gateway Validation:** Release pipelines touching checkout or payment SDKs require end-to-end sandbox authorization tests across all supported operating systems before general deployment.
* **Marketing & Product Integration:** Real-time feedback loops between conversion analytics and ad spend prevent ongoing budget allocation to degraded platform paths.

---

## 7. Technology Stack

| Technology | Usage |
| --- | --- |
| **Google BigQuery** | Cloud data warehouse, event-stream querying, and analytics |
| **GoogleSQL** | Window functions (`LAG`), conditional aggregations (`COUNTIF`), defensive math (`NULLIF`), and cohort CTEs |
| **Python** | Synthetic clickstream generation and deterministic error modeling |
| **GitHub** | Version control and analytical documentation |
