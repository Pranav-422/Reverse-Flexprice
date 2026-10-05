# REST API Specification: Core Billing Engine

All routes accept and return JSON. All monetary values in requests and responses are denominated strictly in **integer paise** ($1\text{ INR} = 100\text{ paise}$), and micro-unit rates are denominated in **integer micro-paise** ($1\text{ paise} = 10,000\text{ micro-paise}$).

**Timestamp format**: every field that carries a time (`timestamp`, `start_date`, `effective_date`, `period_start`, `period_end`) accepts **either** epoch seconds as an integer (e.g. `1727776800`, used in the examples below) **or** an ISO-8601 UTC string (e.g. `"2026-10-01T10:00:00Z"`, used in the PRD.md section 7 scenarios). Both forms are valid everywhere; responses return epoch seconds plus an `_iso` companion field.

**Tenancy**: an optional `X-Tenant-ID` request header selects the tenant partition. When it is absent, the server falls back to the `TENANT_ID` environment setting.

---

## 1. Meter Management

### Create Meter
* **Method & Path**: `POST /v1/meters`
* **Caller**: Operator
* **Input**:
  ```json
  {
    "name": "string (required)",
    "event_name": "string (required)",
    "aggregation_type": "string (enum: COUNT, SUM, COUNT_UNIQUE; required)",
    "value_property": "string (optional; e.g. 'tokens')",
    "divide_by": "integer (optional; default: 1; e.g. 1000 for per-1k tokens)",
    "round_type": "string (optional; enum: up, down, none; default: 'none')"
  }
  ```
* **Output** (`201 Created`):
  ```json
  {
    "id": "string",
    "tenant_id": "string",
    "name": "string",
    "event_name": "string",
    "aggregation_type": "string",
    "value_property": "string",
    "divide_by": 1000,
    "round_type": "none"
  }
  ```
* **Errors**:
  * `400 Bad Request`: Missing `name` or `event_name`; invalid `aggregation_type`; `divide_by < 1`.

---

## 2. Plan & Tiered Price Catalog

### Create Plan
* **Method & Path**: `POST /v1/plans`
* **Caller**: Operator
* **Input**:
  ```json
  {
    "name": "string (required)",
    "lookup_key": "string (required)"
  }
  ```
* **Output** (`201 Created`):
  ```json
  {
    "id": "string",
    "name": "string",
    "lookup_key": "string"
  }
  ```
* **Errors**:
  * `400 Bad Request`: Missing `name` or `lookup_key`.
  * `409 Conflict`: `lookup_key` already exists for this tenant.

### Create Price & Tiers
* **Method & Path**: `POST /v1/prices`
* **Caller**: Operator
* **Input**:
  ```json
  {
    "plan_id": "string (required)",
    "type": "string (enum: fixed, usage; required)",
    "meter_id": "string (optional; required if type is usage)",
    "tier_mode": "string (optional; enum: volume, slab; required if type is usage)",
    "fixed_amount_paise": "integer (optional; recurring fee in paise)",
    "tiers": [
      {
        "tier_index": 0,
        "up_to_units": 1000,
        "unit_amount_micro_paise": 100000,
        "flat_amount_paise": 500
      },
      {
        "tier_index": 1,
        "up_to_units": null,
        "unit_amount_micro_paise": 50000,
        "flat_amount_paise": 1000
      }
    ]
  }
  ```
* **Output** (`201 Created`):
  ```json
  {
    "id": "string",
    "plan_id": "string",
    "meter_id": "string",
    "type": "usage",
    "tier_mode": "slab",
    "fixed_amount_paise": 0,
    "tiers": [...]
  }
  ```
* **Errors**:
  * `400 Bad Request`: Invalid `tier_mode`; non-sequential `tier_index`; final tier `up_to_units` is not null; negative paise values.
  * `404 Not Found`: Referenced `plan_id` or `meter_id` does not exist.

---

## 3. Customer & Subscription Management

### Create Customer
* **Method & Path**: `POST /v1/customers`
* **Caller**: Operator
* **Input**:
  ```json
  {
    "external_id": "string (required)",
    "name": "string (required)",
    "email": "string (optional)",
    "timezone": "string (optional; default: 'UTC')"
  }
  ```
* **Output** (`201 Created`):
  ```json
  {
    "id": "string",
    "external_id": "string",
    "name": "string",
    "email": "string",
    "timezone": "UTC",
    "created_at": 1727740800
  }
  ```
* **Errors**:
  * `409 Conflict`: `external_id` already registered.

### Create Subscription
* **Method & Path**: `POST /v1/subscriptions`
* **Caller**: Operator
* **Input**:
  ```json
  {
    "customer_id": "string (required)",
    "plan_id": "string (required)",
    "start_date": 1727740800
  }
  ```
* **Output** (`201 Created`):
  ```json
  {
    "id": "string",
    "customer_id": "string",
    "plan_id": "string",
    "status": "active",
    "billing_anchor": 1727740800,
    "current_period_start": 1727740800,
    "current_period_end": 1730332800
  }
  ```
* **Errors**:
  * `404 Not Found`: `customer_id` or `plan_id` does not exist.

---

## 4. Usage Event Ingestion (Synchronous & Deduplicated)

### Ingest Usage Event
* **Method & Path**: `POST /v1/events`
* **Caller**: AI Gateway / Ingestion Client
* **Input**:
  ```json
  {
    "event_id": "string (required; client unique idempotency key)",
    "event_name": "string (required)",
    "customer_id": "string (required)",
    "timestamp": 1727776800,
    "properties": {
      "total_tokens": 50000,
      "model": "gpt-4o"
    }
  }
  ```
* **Output**:
  * **First Ingestion** (`201 Created`):
    ```json
    {
      "status": "created",
      "event_id": "evt_token_001",
      "recorded_quantity": 50000
    }
    ```
  * **Duplicate Submission** (`200 OK` - Idempotent Ack):
    ```json
    {
      "status": "duplicate_skipped",
      "event_id": "evt_token_001",
      "message": "Event previously processed; duplicate ignored"
    }
    ```
* **Errors**:
  * `400 Bad Request`: Missing required fields; `timestamp` is older than `MAX_PAST_DRIFT_DAYS` (30 days) or more than `MAX_FUTURE_DRIFT_MINUTES` (5 mins) ahead of current time.
  * `404 Not Found`: `customer_id` does not exist.

---

## 5. Mid-Period Plan Upgrade & Proration

### Upgrade Subscription Plan
* **Method & Path**: `POST /v1/subscriptions/{id}/upgrade`
* **Caller**: Operator / Customer Portal
* **Input**:
  ```json
  {
    "target_plan_id": "string (required)",
    "proration_behavior": "string (enum: create_prorations, none; required)",
    "effective_date": "integer (optional; epoch seconds; defaults to BILLING_NOW or system time)"
  }
  ```
* **Output** (`200 OK`):
  ```json
  {
    "subscription_id": "sub_01",
    "previous_plan_id": "plan_starter",
    "target_plan_id": "plan_pro",
    "effective_date": 1729036800,
    "settlement_invoice": {
      "id": "inv_prorate_101",
      "invoice_type": "one_off",
      "status": "finalized",
      "amount_due_paise": 300000,
      "line_items": [
        {
          "description": "Unused time on Starter Plan (15 days)",
          "quantity": 1,
          "amount_paise": -150000
        },
        {
          "description": "Remaining time on Pro Plan (15 days)",
          "quantity": 1,
          "amount_paise": 450000
        }
      ]
    }
  }
  ```
* **Errors**:
  * `400 Bad Request`: Subscription is already `cancelled` or `paused`; target plan is identical to current plan.
  * `404 Not Found`: Subscription or target plan does not exist.

---

## 6. Invoicing & Billing Cycle Finalization

### Generate / Compute Cycle Invoice
* **Method & Path**: `POST /v1/invoices/generate`
* **Caller**: Operator / Billing Cron
* **Input**:
  ```json
  {
    "subscription_id": "string (required)",
    "period_start": 1727740800,
    "period_end": 1730332800,
    "idempotency_key": "string (optional; derived deterministically if omitted)"
  }
  ```
* **Output** (`200 OK` or `201 Created`):
  ```json
  {
    "id": "inv_cycle_201",
    "subscription_id": "sub_01",
    "customer_id": "cust_01",
    "invoice_type": "subscription_cycle",
    "status": "finalized",
    "period_start": 1727740800,
    "period_end": 1730332800,
    "subtotal_paise": 14000,
    "total_paise": 14000,
    "amount_due_paise": 14000,
    "line_items": [
      {
        "description": "LLM Tokens (Tiered Slab)",
        "quantity": 1500,
        "unit_amount_paise": 9,
        "amount_paise": 14000
      }
    ],
    "explanation": "You consumed 1,500,000 raw tokens (1,500 billed units at 1,000 tokens/unit). Under Slab pricing: the first 1,000 units were billed at ₹0.10/unit + ₹5.00 flat (₹105.00), and the remaining 500 units were billed at ₹0.05/unit + ₹10.00 flat (₹35.00), resulting in a total of ₹140.00."
  }
  ```
* **Errors**:
  * `400 Bad Request`: Invalid interval dates (`period_end <= period_start`).
  * `409 Conflict`: Invoice for this subscription cycle already exists and is finalized.

### Get Invoice Details
* **Method & Path**: `GET /v1/invoices/{id}`
* **Caller**: Operator / Customer
* **Output** (`200 OK`): Full invoice object including line items and plain-language explanation.
* **Errors**:
  * `404 Not Found`: Invoice ID does not exist.

---

## 7. Read & Inspection Routes

### Get Meter Usage
* **Method & Path**: `GET /v1/meters/{id}/usage`
* **Caller**: Operator / Billing UI
* **Query Parameters**:
  * `customer_id` (string, required)
  * `period_start`, `period_end` (optional; epoch seconds or ISO-8601). When omitted, the customer's active subscription period is used.
* **Output** (`200 OK`):
  ```json
  {
    "meter_id": "meter_tokens",
    "customer_id": "cust_ai_01",
    "aggregation_type": "SUM",
    "raw_units": 50000,
    "quantity": 50,
    "divide_by": 1000,
    "period_start": 1790812800,
    "period_end": 1793491200
  }
  ```
  `raw_units` is the aggregated raw property total; `quantity` is `raw_units` after unit packaging (`divide_by` + `round_type`), i.e. the billable units handed to the pricing engine.
* **Errors**:
  * `400 Bad Request`: `period_end <= period_start`; unparseable period.
  * `404 Not Found`: meter or `customer_id` does not exist.

### Get Subscription
* **Method & Path**: `GET /v1/subscriptions/{id}`
* **Caller**: Operator
* **Output** (`200 OK`): The subscription object as returned by `POST /v1/subscriptions`, including `plan_id`, `status`, `current_period_start` / `current_period_end` and their `_iso` companions. Use it to confirm a plan change took effect.
* **Errors**:
  * `404 Not Found`: subscription does not exist.

### Count Stored Events (deduplication inspection)
* **Method & Path**: `GET /v1/events?event_id={event_id}`
* **Caller**: Operator / test harness
* **Purpose**: Returns how many rows are physically stored for a client-supplied `event_id`. Because `usage_events` enforces `UNIQUE(tenant_id, event_id)`, this count is **always 0 or 1** — it is the storage-level proof that deduplication held, independent of what the ingestion endpoint reported.
* **Output** (`200 OK`):
  ```json
  {
    "event_id": "evt_dedup_001",
    "count": 1,
    "rows": [
      {
        "id": "ue_7f3a...",
        "event_id": "evt_dedup_001",
        "customer_id": "cust_ai_01",
        "event_name": "token_usage",
        "timestamp": 1790848800,
        "quantity": 50000,
        "ingested_at": 1790848810
      }
    ]
  }
  ```

### Health
* **Method & Path**: `GET /health`
* **Output** (`200 OK`):
  ```json
  {
    "status": "ok",
    "now": 1790812800,
    "now_iso": "2026-10-01T00:00:00Z",
    "billing_now_injected": true,
    "billing_period_minutes": 0,
    "ai_explainer": "template_fallback"
  }
  ```
  `ai_explainer` is `live` when `AI_PROVIDER_API_KEY` is configured and `template_fallback` otherwise.

---

## 8. Invoice Explanation & Spike Detection (Differentiator)

These two routes implement GAPS.md section 2, Improvement 2.

### Get Invoice Explanation
* **Method & Path**: `GET /v1/invoices/{id}/explanation`
* **Caller**: Customer portal / support tooling
* **Purpose**: Returns a plain-language narrative of how the invoice was calculated — raw tokens to billable units, which tier brackets applied, and any mid-cycle proration credit and charge.
* **AI behaviour**: If `AI_PROVIDER_API_KEY` is set, an LLM rewrites the computed facts as prose. If it is missing, the SDK is absent, the API call fails, or the model declines, the endpoint still returns `200 OK` using the deterministic template defined in GAPS.md section 2. **Every monetary figure is computed locally from the sealed invoice; the model never performs arithmetic**, so the `facts` object is identical on both paths.
* **Output** (`200 OK`):
  ```json
  {
    "invoice_id": "inv_cycle_201",
    "source": "template_fallback",
    "ai_enabled": false,
    "ai_model": null,
    "ai_error": null,
    "explanation": "Billing Summary for Acme AI: You used 1,500,000 tokens (1,500 units). Tier 1: 1,000 units at 10 paise = 10,500 paise. Tier 2: 500 units at 5 paise = 3,500 paise. Proration: 3,000 paise credited, 6,000 paise charged. Total Due: Rs 260.00 (26,000 paise).",
    "template_explanation": "Billing Summary for Acme AI: ...",
    "facts": {
      "customer_name": "Acme AI",
      "raw_tokens": 1500000,
      "billed_units": 1500,
      "divide_by": 1000,
      "tier_mode": "slab",
      "tier_1_units": 1000,
      "tier_1_rate_paise": 10,
      "tier_1_cost_paise": 10500,
      "tier_2_units": 500,
      "tier_2_rate_paise": 5,
      "tier_2_cost_paise": 3500,
      "fixed_charges_paise": 12000,
      "usage_charges_paise": 14000,
      "proration_credit_paise": -3000,
      "proration_charge_paise": 6000,
      "proration_days": 15,
      "total_paise": 26000,
      "total_rupees": "260.00"
    }
  }
  ```
  * `source` is `ai` or `template_fallback`; `ai_error` carries the reason whenever a configured AI path fell back, so a degraded explainer is visible rather than silent.
  * For a `subscription_cycle` invoice, the proration figures are drawn from the `one_off` settlement recorded for the same subscription inside the billed period, so one narrative covers both tier maths and the plan change.
* **Errors**:
  * `404 Not Found`: invoice does not exist.

### Get Customer Spike Status
* **Method & Path**: `GET /v1/customers/{id}/spike-status`
* **Caller**: Operator / monitoring
* **Purpose**: Real-time runaway-usage detection. Compares the customer's trailing 1-hour token velocity against their 7-day hourly average and flags a spike above `SPIKE_THRESHOLD_FACTOR` (default `3.0`), logging an alert when it fires.
* **Windows**: `current` covers `[now - 1h, now)`; the baseline covers `[now - 7d, now - 1h)` over 167 hours. The two windows are **disjoint**, so a burst cannot inflate its own baseline and a customer's first hour of traffic cannot flag itself.
* **Output** (`200 OK`):
  ```json
  {
    "customer_id": "cust_ai_01",
    "evaluated_at": 1791210600,
    "evaluated_at_iso": "2026-04-08T00:30:00Z",
    "window": "1h vs 7d hourly average",
    "current_hour_tokens": 10000,
    "seven_day_tokens": 167000,
    "baseline_hourly_tokens": 1000,
    "threshold_factor": 3.0,
    "threshold_tokens": 3000.0,
    "ratio": 10.0,
    "spike_detected": true,
    "reason": "above_threshold",
    "message": "Usage spike detected: 10,000 tokens in the last hour is 10.00x the 7-day average of 1,000 tokens/hour (alert threshold 3.0x). Check for a runaway agent loop."
  }
  ```
  When no 7-day history exists, `baseline_hourly_tokens` is `0`, `ratio` is `null`, `spike_detected` is `false` and `reason` is `insufficient_baseline` — a missing baseline is reported, never treated as a spike.
* **Errors**:
  * `404 Not Found`: customer does not exist.
