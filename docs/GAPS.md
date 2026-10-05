# Upstream Gaps & Targeted Improvements

## 1. Upstream Engineering Gaps (Flexprice Core)

| # | Type | What's Wrong | Evidence `path:line` | Who It Hurts | Fix | Severity |
| :---: | :--- | :--- | :--- | :--- | :--- | :---: |
| **1** | **Deduplication Flaw** | ClickHouse `ReplacingMergeTree` includes `timestamp` in its sorting key. If a duplicate event arrives with an altered client timestamp, both rows persist and both are counted even with `FINAL`. | [migrations/clickhouse/000001_create_events_table.up.sql:17-20](file:///d:/Reverse-Flexprice/flexprice/migrations/clickhouse/000001_create_events_table.up.sql#L17-L20) [Confirmed], [migrations/clickhouse/000002_create_meter_usage_table.up.sql:19-22](file:///d:/Reverse-Flexprice/flexprice/migrations/clickhouse/000002_create_meter_usage_table.up.sql#L19-L22) [Confirmed] | Customers (double-billed on retried network requests). | Use `(tenant_id, event_id)` as the unique primary constraint. | **High** |
| **2** | **Unreliable Ingest Ack** | `eventService.CreateEvent` logs Kafka publish failures but returns nil, giving callers a false HTTP 202 Accepted while data is lost. | [internal/ee/service/event.go:75-81](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/event.go#L75-L81) [Confirmed], [internal/api/v1/events.go:81](file:///d:/Reverse-Flexprice/flexprice/internal/api/v1/events.go#L81) [Confirmed] | Startup (silent unbilled revenue loss during broker degradation). | Acknowledge ingestion only after durable storage. | **High** |
| **3** | **Unvalidated Timestamps** | Event ingestion has zero bounds checking on `timestamp`. Events dated months in the past land in sealed, immutable invoice periods and are never billed. | [internal/api/dto/events.go:27-35](file:///d:/Reverse-Flexprice/flexprice/internal/api/dto/events.go#L27-L35) [Confirmed], [internal/ee/service/event.go:69](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/event.go#L69) [Confirmed] | Startup (free usage exploit by backdating events into closed periods). | Reject timestamps outside current billing drift window. | **High** |
| **4** | **Silent Negative Coercion** | Negative usage values are silently reset to zero (`qty = decimal.Zero`) with an info log, hiding malformed input and preventing usage credits. | [internal/ee/service/meter_usage_tracking.go:493-499](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/meter_usage_tracking.go#L493-L499) [Confirmed] | Operators (cannot issue usage corrections; data errors hidden). | Reject negative inputs or support signed deltas. | **Medium** |
| **5** | **Proration Error Masking** | When reading past billed amounts for proration credits fails, the error is ignored and the system falls back to list price. | [internal/ee/service/line_item_proration.go:456-464](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/line_item_proration.go#L456-L464) [Confirmed] | Startup (over-refunds customers who received discounts). | Fail closed and abort transaction on DB read errors. | **High** |
| **6** | **Rigid Upgrade Validation** | Plan change V2 forbids in-place upgrades between different billing intervals (e.g. Monthly to Annual), returning 400 Bad Request. | [internal/ee/service/subscription_change_v2.go:91-100](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/subscription_change_v2.go#L91-L100) [Confirmed] | Startup (cannot transition paying monthly users to annual contracts). | Carry unused monthly credit into annual opening invoice. | **Medium** |
| **7** | **Micro-Pricing Truncation** | INR precision is hardcoded to 2 decimals (paise). Sub-paise rates per token (e.g. ₹0.002 per token) round prematurely to 0 or suffer 50% rounding errors. | [internal/types/currency.go:18,138-140](file:///d:/Reverse-Flexprice/flexprice/internal/types/currency.go#L18) [Confirmed], [internal/ee/service/billing_meter_usage.go:303](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/billing_meter_usage.go#L303) [Confirmed] | Both (startup loses revenue; customers face erratic rounding). | Store micro-paise rates; round only final invoice lines. | **High** |
| **8** | **Missing Spike Detection** | Entire codebase lacks velocity monitoring or anomaly checks for runaway API loops. | Whole repository audit [Confirmed] | Customers (runaway LLM loops exhaust balance or rack up unpayable bills). | Add sliding-window velocity alerts. | **High** |
| **9** | **Coarse Spend Alerts** | Spend alerts deliberately dropped line-item and group scopes, alerting only on aggregate subscription totals. | [internal/ee/service/alert_evaluation.go:19-22](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/alert_evaluation.go#L19-L22) [Confirmed] | Customers (cannot set isolated budget alerts on expensive models). | Re-enable per-meter alert filters. | **Medium** |
| **10** | **Opaque Bill Lines** | Invoices lack explanation metadata showing how raw tokens were packaged, tiered, or prorated. | [internal/domain/invoice/model.go:25-50](file:///d:/Reverse-Flexprice/flexprice/internal/domain/invoice/model.go#L25-L50) [Confirmed], [internal/ee/service/invoice.go:616-629](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/invoice.go#L616-L629) [Confirmed] | Customers & Ops (high dispute rates over unexplained charges). | Attach structured calculation audits to line items. | **Medium** |
| **11** | **Dedup Cache TTL Window** | Redis lock expires after 24 hours (`eventDeduplicationLockTTL = 24 * time.Hour`). Replayed batches after 24h bypass cache dedup. | [internal/ee/service/meter_usage_tracking.go:434-438](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/meter_usage_tracking.go#L434-L438) [Confirmed] | Customers (re-imported historical events duplicate usage). | Enforce persistent DB-level uniqueness. | **Medium** |

---

## 2. Two Concrete Rebuild Improvements

### Improvement 1 (Fix): Deterministic Deduplication with Sub-Paise Micro-Token Rating
* **Why It Matters for the Brief**:
  An Indian AI API startup processes millions of token events per day. Network hiccups inevitably cause API gateways to retry event postings. In the original repo, if retries arrive with updated timestamps or after 24 hours, customers get double-billed. Furthermore, individual LLM tokens cost fractions of a paise (e.g. ₹0.0015 per token); premature rounding to 2 decimals either erases revenue or causes 50% overcharges.
* **Specification**:
  * **Data Model**: `usage_events` table enforces `UNIQUE(tenant_id, event_id)` at the SQLite engine level. Unit prices use `unit_amount_micro_paise` ($1\text{ paise} = 10,000\text{ micro-paise}$).
  * **Behavior**:
    1. Ingestion performs atomic insert. If `event_id` exists, it immediately catches the constraint and returns HTTP 200 OK (`{"status": "duplicate_skipped"}`).
    2. Rating multiplies integer tokens by `unit_amount_micro_paise` without intermediate decimal conversions.
    3. Half-up rounding occurs only when summing line items into integer paise on the final invoice:
       $$\text{AmountPaise} = \left\lfloor \frac{\text{TotalMicroPaise} + 5000}{10000} \right\rfloor$$
  * **Fallback**: 100% deterministic local math (no external API calls required).
  * **Acceptance Criterion (Given/When/Then)**:
    * **Given** an AI API endpoint with price ₹0.10 per 1,000 tokens (100,000 micro-paise per 1k units).
    * **When** two concurrent requests submit event `evt_retry_10` with 50,000 tokens at 12:00:00 and 12:00:02.
    * **Then** exactly one row is stored, total tokens recorded equals 50,000, and billed amount equals exactly ₹5.00 (500 paise), with zero duplicate leakage.

---

### Improvement 2 (Differentiator): Plain-Language Invoice Explainer & Runaway Spike Alert
* **Why It Matters for the Brief**:
  AI startups deal with autonomous agents that occasionally enter infinite prompt loops, burning thousands of rupees in minutes. Furthermore, end-users frequently dispute token bills because invoices only show abstract numbers without showing which models or tiers were applied.
* **Specification**:
  * **Endpoints**:
    * `GET /v1/invoices/{id}/explanation`: Returns human-readable narrative explaining token math, tier breakdowns, and proration credits.
    * `GET /v1/customers/{id}/spike-status`: Returns real-time velocity metrics (current 1-hour consumption vs 7-day average).
  * **Spike Detection Logic**:
    * Maintains hourly token velocity $V_{\text{current}}$.
    * If $V_{\text{current}} > 3.0 \times V_{\text{7-day average}}$, flags `spike_detected: true` and logs an alert.
  * **Deterministic Fallback (No AI Key Required)**:
    * If `AI_PROVIDER_API_KEY` is set, formats prompt to LLM to summarize billing narrative.
    * If `AI_PROVIDER_API_KEY` is missing/empty, executes deterministic template:
      ```
      "Billing Summary for {customer_name}: You used {raw_tokens} tokens ({billed_units} units). Tier 1: {tier_1_units} units at {tier_1_rate} paise = {tier_1_cost} paise. Tier 2: {tier_2_units} units at {tier_2_rate} paise = {tier_2_cost} paise. Proration: {proration_credit} paise credited, {proration_charge} paise charged. Total Due: ₹{total_rupees} ({total_paise} paise)."
      ```
  * **Acceptance Criterion (Given/When/Then)**:
    * **Given** an invoice generated with 1,500,000 tokens under Slab pricing and an upgrade proration netting ₹30.00.
    * **When** a user calls `GET /v1/invoices/{id}/explanation` with no AI key configured in `.env`.
    * **Then** the response returns HTTP 200 OK with a coherent, mathematically verified paragraph explaining that 1,000 units were billed at Tier 1, 500 units at Tier 2, and details the exact 15-day proration credit and charge.
