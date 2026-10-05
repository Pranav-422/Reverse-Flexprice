# Product Requirements Document (PRD): Core Billing Engine Rebuild

## 1. Problem Statement
Indian AI API startups face distinct billing challenges: they bill customers in Indian Rupees (INR) for high-frequency micro-transactions (per 1,000 LLM tokens, embedding vectors, and model inferences). Existing billing systems introduce severe failure modes:
1. Network retries cause duplicate event ingestion, double-billing end users.
2. In-place mid-month plan upgrades either fail or produce inaccurate proration calculations.
3. Micro-pricing per token suffers from premature decimal rounding, either zeroing out revenue or overcharging customers.
4. Invoices lack plain-language explanations, leading to customer disputes and churn.

This rebuild implements a focused, deterministic billing engine that ingests usage events safely, aggregates meters accurately, prices via volume and slab tiers, prorates plan changes correctly, and produces transparent settlement invoices.

---

## 2. Target User & Positioning

### Target User
Founders, product engineers, and billing platform developers at Indian AI API startups who provide LLM APIs, search embeddings, and autonomous agent services billed in Indian Rupees (INR).

### Positioning One-Liner
> **For** Indian AI API startups **who struggle with** double-charging on network retries, complex mid-month upgrade proration, and opaque token invoices, **the Core Billing Engine does** deterministic deduplication, sub-paise tiered pricing, and automated proration settlement in integer paise, **unlike** legacy platforms that silently drop events, lack token explainability, or round away micro-paise.

---

## 3. Core Workflow

1. **Meter Configuration**: Operator defines a meter capturing token consumption (`SUM` aggregation over `properties.total_tokens`).
2. **Catalog Creation**: Operator creates a Plan (e.g., "Developer Plan" or "Scale Plan") and attaches a tiered price in INR per 1,000 units.
3. **Customer Registration**: Operator registers a Customer with business details and timezone.
4. **Subscription Activation**: Operator subscribes the customer to the plan with monthly billing in advance.
5. **Usage Ingestion**: AI gateway pushes raw token usage events as API calls complete. Events are deduplicated and aggregated into the active meter.
6. **Mid-Month Plan Upgrade**: Customer upgrades from Developer to Scale plan on Day 15. The engine calculates day-based proration, issues credit for unused Developer time, and charges for remaining Scale time.
7. **Invoice Settlement**: The engine produces a single netted invoice listing distinct credit and debit items, sealing the period in integer paise.

---

## 4. Feature Scope (MoSCoW)

### Must Have (P0 - Core Engine & Killer Tests)
* **Deterministic Event Deduplication**: Ingestion guaranteed idempotent via database-level uniqueness on `(tenant_id, event_id)`.
* **Meter Aggregation**: Real-time evaluation of `COUNT`, `SUM`, and `COUNT_UNIQUE`.
* **Tiered Pricing**: Slab (graduated) and Volume pricing with inclusive boundaries (`quantity <= UpTo`).
* **Unit Transformation**: Quantity scaling (e.g., raw tokens divided by 1,000) prior to tier evaluation.
* **Mid-Period Plan Upgrade & Proration**: Second-based and day-based proration coefficient calculation with opposing credit and charge line items.
* **Invoice Generation**: Idempotent invoice production with integer-paise accounting (1 Rupee = 100 paise).

### Should Have (P1 - Production Integrity)
* **Synchronous Ingestion Acknowledgement**: HTTP response confirms durable persistence before returning 200/201.
* **Strict Timestamp Drift Validation**: Rejection of events dated in the past beyond the active billing window or into the future.
* **Injectable Time (`BILLING_NOW`)**: Ability to override server time via environment variable for instant test verification.

### Could Have (P2 - Differentiators)
* **Plain-Language AI Invoice Explainer**: Automated textual breakdown of token usage, tier brackets, and proration math (with deterministic fallback).
* **Usage-Spike Anomaly Detection**: Real-time velocity monitoring alerting on sudden runaway consumption loops.

### Won't Have (Out of Scope)
* Multi-currency foreign exchange conversions (INR only).
* Third-party payment gateway integration (Razorpay/Stripe redirect checkouts).
* Multi-jurisdiction tax calculation engines (GST/VAT).
* Complex SAML/SSO enterprise authentication.
* Credit notes and chargeback management workflows.

---

## 5. Acceptance Criteria

### Killer Test 1: Duplicate Usage Ingestion
* **Given** an active customer subscription and a meter tracking `llm_tokens` with unit packaging (`divide_by = 1000`).
* **When** an event with `event_id = "evt_dedup_001"` and `properties.total_tokens = 50000` is ingested at 10:00:00 UTC, and a second request with the identical `event_id = "evt_dedup_001"` and `properties.total_tokens = 50000` arrives 5 seconds later.
* **Then**:
  1. The first request returns HTTP 201 Created and persists the event.
  2. The second request returns HTTP 200 OK (idempotent duplicate acknowledged) or HTTP 409 Conflict.
  3. The aggregated meter usage is exactly **50,000 tokens** (50 billable units).
  4. The generated invoice lines show a quantity of **50 units** and never 100 units.

### Killer Test 2: Mid-Month Plan Upgrade
* **Given** a 30-day calendar month (April 1, 00:00:00 UTC to May 1, 00:00:00 UTC), a customer on "Starter Plan" billed at ₹3,000/month (300,000 paise) in advance, and a target "Pro Plan" priced at ₹9,000/month (900,000 paise).
* **When** the customer upgrades on Day 15 (April 16, 00:00:00 UTC) with day-based proration enabled.
* **Then**:
  1. Total days in cycle = 30; remaining days from April 16 to May 1 = 15.
  2. Proration coefficient is exactly $15 / 30 = 0.5000$.
  3. Unused Starter credit is $-(\text{₹}3,000 \times 0.50) = -\text{₹}1,500.00$ ($-150,000$ paise).
  4. Remaining Pro charge is $+(\text{₹}9,000 \times 0.50) = +\text{₹}4,500.00$ ($+450,000$ paise).
  5. The generated settlement invoice has exactly two opposing line items:
     * Line 1: `Unused time on Starter Plan (15 days)` with amount $-150,000$ paise.
     * Line 2: `Remaining time on Pro Plan (15 days)` with amount $+450,000$ paise.
  6. The net amount due on the invoice is exactly **₹3,000.00** (**300,000 paise**).

### Killer Test 3: Tiered Pricing Calculation
* **Given** an LLM token meter with unit packaging (`divide_by = 1000`) and a price configured with:
  * Tier 1: `UpTo = 1000` units (1,000,000 tokens), Unit Amount = ₹0.10 (10 paise), Flat Amount = ₹5.00 (500 paise).
  * Tier 2: `UpTo = nil` (>1,000 units), Unit Amount = ₹0.05 (5 paise), Flat Amount = ₹10.00 (1,000 paise).
* **When** 1,500,000 raw tokens (1,500 billable units) are consumed:
  * **Case A (Slab Mode)**:
    1. Tier 1 slice (1,000 units): $(1,000 \times \text{₹}0.10) + \text{₹}5.00 = \text{₹}100.00 + \text{₹}5.00 = \text{₹}105.00$ (10,500 paise).
    2. Tier 2 slice (500 units): $(500 \times \text{₹}0.05) + \text{₹}10.00 = \text{₹}25.00 + \text{₹}10.00 = \text{₹}35.00$ (3,500 paise).
    3. Total billed invoice amount is exactly **₹140.00** (**14,000 paise**).
  * **Case B (Volume Mode)**:
    1. Total units = 1,500. Since $1500 > 1000$, all units match Tier 2.
    2. Total cost: $(1,500 \times \text{₹}0.05) + \text{₹}10.00 = \text{₹}75.00 + \text{₹}10.00 = \text{₹}85.00$ (8,500 paise).
    3. Total billed invoice amount is exactly **₹85.00** (**8,500 paise**).

---

## 6. Extra Edge Cases

1. **Exact Tier Boundary Match**:
   * *Given* Tier 1 `UpTo = 1000` units and Slab pricing.
   * *When* usage is exactly 1,000 units (1,000,000 tokens).
   * *Then* the entire usage is billed under Tier 1 ($(1,000 \times 10) + 500 = 10,500$ paise); Tier 2 is not triggered because boundary is strictly inclusive (`quantity <= UpTo`).
2. **Concurrent Duplicate Submissions**:
   * *Given* two concurrent worker threads submitting the same payload with `event_id = "evt_race_99"`.
   * *When* both threads hit the database simultaneously.
   * *Then* the database unique constraint permits exactly 1 write, the second request fails gracefully or returns existing state, and total recorded usage is 1 event.
3. **Upgrade on Day 1 vs Last Day**:
   * *Day 1 Upgrade*: Full cycle credit issued for old plan, full cycle charge for new plan; net amount equals full price difference.
   * *Last Day Upgrade (Day 30)*: Remaining days = 1; coefficient = $1 / 30 = 0.0333...$; Starter credit = $-10,000$ paise; Pro charge = $+30,000$ paise; net due = $+20,000$ paise (₹200.00).
4. **Sub-Paise Token Rounding**:
   * *Given* an AI prompt priced at ₹0.002 per token (0.2 paise).
   * *When* individual requests arrive.
   * *Then* fractional paise accumulate unrounded; rounding to integer paise occurs only when the final invoice line item is computed.

---

## 7. Runnable Test Scenarios

### Scenario 1: Verify Idempotent Deduplication
```json
// Request 1: POST /v1/events
{
  "event_id": "test_evt_001",
  "event_name": "token_usage",
  "customer_id": "cust_ai_01",
  "timestamp": "2026-10-01T10:00:00Z",
  "properties": {"tokens": 50000}
}
// Expected Response: HTTP 201 Created

// Request 2: POST /v1/events (Duplicate)
{
  "event_id": "test_evt_001",
  "event_name": "token_usage",
  "customer_id": "cust_ai_01",
  "timestamp": "2026-10-01T10:00:00Z",
  "properties": {"tokens": 50000}
}
// Expected Response: HTTP 200 OK (idempotent duplicate skipped)

// Query: GET /v1/meters/meter_tokens/usage?customer_id=cust_ai_01
// Expected Output: {"quantity": 50, "raw_units": 50000}
```

### Scenario 2: Verify Mid-Month Plan Upgrade
```json
// 1. Create Starter sub on April 1 (Paid 300,000 paise)
// 2. Set BILLING_NOW="2026-04-16T00:00:00Z"
// 3. POST /v1/subscriptions/sub_01/upgrade
{
  "target_plan_id": "plan_pro",
  "proration_behavior": "create_prorations"
}
// Expected Invoice Output:
{
  "invoice_type": "one_off",
  "amount_due_paise": 300000,
  "line_items": [
    {
      "description": "Unused time on Starter Plan (15 days)",
      "amount_paise": -150000,
      "quantity": 1
    },
    {
      "description": "Remaining time on Pro Plan (15 days)",
      "amount_paise": 450000,
      "quantity": 1
    }
  ]
}
```

### Scenario 3: Verify Tiered Slab Calculation
```json
// Ingest 1,500,000 tokens (1,500 units)
// Trigger Cycle Invoice Generation: POST /v1/invoices/generate
// Expected Invoice Line Item:
{
  "display_name": "LLM Inference Tokens (Tiered Slab)",
  "quantity": 1500,
  "amount_paise": 14000,
  "metadata": {
    "tier_1_units": 1000,
    "tier_1_cost_paise": 10500,
    "tier_2_units": 500,
    "tier_2_cost_paise": 3500
  }
}
```
