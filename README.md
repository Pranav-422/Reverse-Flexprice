# Paise-Perfect

**Usage billing for Indian AI APIs that never double-bills and explains every rupee.**

_Core Billing Engine for the Usage-Based Billing card._

A usage-based billing engine for Indian AI API startups — **clean-room rebuilt from
`docs/` alone**, with no access to the original implementation.

It ingests usage events idempotently, aggregates them through meters, prices them
across volume and slab tiers, prorates mid-cycle plan upgrades, and seals the result
into an invoice. **Every amount is an integer**: paise for money (1 INR = 100 paise),
micro-paise for unit rates (1 paise = 10,000 micro-paise). No floating-point value
ever touches a monetary figure.

* Python 3 + FastAPI + SQLite. **Zero external infrastructure** — no Postgres,
  ClickHouse, Redis, Kafka or Temporal.
* **Injectable clock.** Nothing calls `datetime.now()`; `BILLING_NOW` in `.env` drives
  server time, so a 30-day cycle and a Day-15 upgrade are simulated in milliseconds.
  The whole test suite runs in about a second.
* **Optional AI.** The invoice explainer uses Claude when `AI_PROVIDER_API_KEY` is set
  and falls back to a deterministic template when it is not. The app never crashes
  without a key.

---

## Quick start

```bash
git clone https://github.com/Pranav-422/Reverse-Flexprice.git
cd Reverse-Flexprice

python3 -m venv venv
./venv/bin/pip install -r requirements.txt

cp .env.example .env          # no secrets needed; defaults work as-is
```

**On Windows** (PowerShell or cmd) the same steps are:

```bat
py -3 -m venv venv
venv\Scripts\pip install -r requirements.txt
copy .env.example .env
venv\Scripts\python -m pytest -q
scripts\demo
scripts\ui
```

`scripts\demo` and `scripts\ui` are `.cmd` launchers that use `venv\Scripts\python`.
Everywhere below, read `./venv/bin/` as `venv\Scripts\` and `./scripts/x` as `scripts\x`.

### Run the tests

```bash
./venv/bin/python -m pytest -q
```

Expected: **41 passed** in about two seconds.

### Run the demo

```bash
./scripts/demo
```

Boots a real `uvicorn` server on a throwaway SQLite file and drives it over HTTP,
printing every request alongside the **expected** value quoted from `docs/PRD.md` and
the **actual** response, with a PASS/FAIL verdict per check. Expected: **67 checks,
all PASS**. It walks the three Killer Tests and then both improvements.

### Open the dashboard

```bash
./scripts/ui          # Windows: scripts\ui
```

Seeds a demo tenant into a throwaway database, freezes the clock at
2026-05-01 00:30 UTC and opens `http://127.0.0.1:8000/app` — six screens, every
number fetched live from the API:

| Screen | What you can do on it |
|---|---|
| Overview | A five-step “try it” guide, billed total, stored event rows (1 per `event_id`), spike alerts, 7-day token chart |
| Usage Events | **Killer Test 1** live: “Send twice concurrently” fires two posts of one `event_id` 2 s apart → one `201`, one `200 duplicate_skipped`, 1 row stored |
| Customers | **Killer Test 2** live: preview and execute a Day-15 upgrade of `c_live` (0.5000 → −₹30 + ₹60 = ₹30), then generate the period's invoice; a customer that already changed plan shows what was actually settled |
| Pricing | **Killer Test 3** live: slider over tokens, slab vs volume from the engine's own rating code (1.5M tokens → ₹140 vs ₹85); the 1,500.5k preset shows exact sub-unit rating (₹140.03, Improvement 1) |
| Invoices | Plain-language explanation + line items with tier and proration math (Improvement 2) |
| Spike Monitor | Red **"Spike detected · 10× normal"** banner for `c_runaway` next to a calm `c_steady`; “Simulate a runaway loop” posts a burst for `c_steady` and the alert fires live (Improvement 2) |

The page is plain HTML + JavaScript (no build step, no framework) over the `/v1`
API and works on a phone as well as a laptop. The read-only views it needs (`GET /v1/customers`, `/v1/plans`, `/v1/invoices`,
`/v1/usage/recent`, `/v1/usage/hourly`, `/v1/overview`, and the two previews) live in
`app/api/routes/dashboard.py` and never write. The original single-page explainer is
still at `/ui`.

### Run the API server

```bash
./venv/bin/uvicorn app.main:app --reload
# http://127.0.0.1:8000/health
# http://127.0.0.1:8000/docs      (interactive OpenAPI)
```

### Regenerate the deck

```bash
./venv/bin/python scripts/make_deck.py     # writes deck.pdf (5 slides)
```

---

## What was built

### The three Killer Tests

Written and committed **red, before any engine code existed**
(commit `ec09fdf`), using the exact numbers in `docs/PRD.md` section 5. The engine
was then built in the `docs/ARCHITECTURE.md` section 5 order until they passed.

| # | Test | Guarantee | File |
|---|---|---|---|
| 1 | Duplicate usage event | 50,000 raw tokens / `divide_by` 1,000 = **50 billable units**. A retry — even with a mutated timestamp — returns `200 duplicate_skipped`. Eight concurrent threads on one `event_id` write **exactly one row**. | `tests/test_killer_1_dedup.py` |
| 2 | Mid-month plan upgrade | 30-day April cycle, upgrade on Day 15. Coefficient `15/30 = 0.5000`, credit `-150,000` paise, charge `+450,000` paise, **net due exactly 300,000 paise (₹3,000.00)**. | `tests/test_killer_2_proration.py` |
| 3 | Tiered pricing | 1,500,000 tokens = 1,500 units. **Slab: 14,000 paise (₹140.00)**, **Volume: 8,500 paise (₹85.00)**. Boundary of exactly 1,000 units stays in Tier 1. | `tests/test_killer_3_tiered.py` |

The guarantee behind Test 1 is a database constraint, not application logic:

```sql
CREATE UNIQUE INDEX idx_usage_events_tenant_event_unique
    ON usage_events (tenant_id, event_id);
```

Ingestion performs a single atomic `INSERT` and catches the violation. There is no
`SELECT`-then-`INSERT` race window, no cache TTL to outlive, and no dependence on the
client-supplied timestamp.

### The two improvements

Both are specified in `docs/GAPS.md` section 2. **No other gaps were touched.**

**1. Fix — deterministic dedup + sub-paise micro-token rating**
(`tests/test_fix_dedup_micropaise.py`, closes gaps 1, 7, 11)

Deduplication lives in the storage engine, so a retry with a changed timestamp or a
batch replayed a week later still dedupes. Unit rates are stored as integer
micro-paise and consumption is rated in raw-token space over an exact integer
numerator, with the `DATA_MODEL.md` half-up rule applied **exactly once**, at the
invoice line.

This caught a real defect: billable units were being rounded through `divide_by`
*before* any money was computed.

```
1,500,500 raw tokens at divide_by = 1000, against the Killer Test 3 tier table
  before : units pre-rounded to 1,501  ->  14,005 paise   (overcharge)
  after  : exact 1,500.5 units         ->  14,002.5 -> 14,003 paise
```

It also means a rate of ₹0.002 per token (0.2 paise) bills **2 paise** across ten
single-token requests rather than rounding each to zero.

**2. Differentiator — invoice explainer + runaway-spike alert**
(`tests/test_differentiator_explainer_spike.py`, closes gaps 8, 10)

| Endpoint | What it does |
|---|---|
| `GET /v1/invoices/{id}/explanation` | A plain-language paragraph: how raw tokens became billable units, which tiers applied, and the exact proration credit and charge. |
| `GET /v1/customers/{id}/spike-status` | Trailing-hour token velocity against the 7-day hourly average. Above `SPIKE_THRESHOLD_FACTOR` (default 3.0) it sets `spike_detected` and logs an alert — catching an agent stuck in a prompt loop. |

**The AI is optional and cannot break the app.** Every figure in an explanation is
computed locally from the sealed invoice; the model only rewrites those facts as
prose, so an AI failure changes the wording and never the money. Four fallback rungs
all return `200 OK` with the deterministic template from `docs/GAPS.md`:

1. `AI_PROVIDER_API_KEY` empty — a supported mode, not an error
2. the optional `anthropic` package is not installed
3. the API call fails (bad key, network, rate limit, server error)
4. the model declines or returns nothing

The response always carries `source` (`ai` or `template_fallback`) and `ai_error`, so
a fallback is visible rather than silent. `anthropic` is deliberately left commented
out in `requirements.txt`, which means **the default install exercises the fallback
path** — every test in the suite runs without an AI key.

---

## Project layout

```
app/
  api/routes/   events, catalog, subscriptions, invoices, insights
  core/         config (env), time (BILLING_NOW), money (integer paise math)
  db/           database (SQLite/WAL), models (DDL)
  services/     ingestion, meter, pricing, proration, invoice,
                subscription, explainer, spike_detector
  static/       app.html (the /app dashboard), explainer.html (/ui)
  main.py       FastAPI app + /health + /app + /ui
tests/          3 Killer Tests + 1 Fix + 1 Differentiator + dashboard views  (41 cases)
scripts/
  demo          live step-by-step walkthrough (expected vs actual)
  ui            seeds a demo tenant and opens the /app dashboard
  *.cmd         Windows launchers for demo and ui
  make_deck.py  generates deck.pdf
docs/           the ONLY source of truth for this rebuild
```

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/meters` | Define aggregation (`COUNT`/`SUM`/`COUNT_UNIQUE`) and `divide_by` packaging |
| `POST` | `/v1/plans` | Create a plan |
| `POST` | `/v1/prices` | Attach a `fixed` fee or a `usage` price with `volume`/`slab` tiers |
| `POST` | `/v1/customers` | Register a customer |
| `POST` | `/v1/subscriptions` | Subscribe a customer to a plan |
| `GET` | `/v1/subscriptions/{id}` | Read a subscription |
| `POST` | `/v1/subscriptions/{id}/upgrade` | Mid-cycle plan change with proration settlement |
| `POST` | `/v1/events` | Ingest usage — `201` created, `200` duplicate_skipped |
| `GET` | `/v1/events?event_id=` | Storage-level row count (used by the dedup tests) |
| `GET` | `/v1/meters/{id}/usage` | Aggregated `raw_units` and billable `quantity` |
| `POST` | `/v1/invoices/generate` | Idempotent cycle invoice |
| `GET` | `/v1/invoices/{id}` | Full invoice with line items |
| `GET` | `/v1/invoices/{id}/explanation` | Plain-language breakdown (differentiator) |
| `GET` | `/v1/customers/{id}/spike-status` | Usage velocity + spike flag (differentiator) |
| `GET` | `/health` | Status, injected clock, and explainer mode |
| `GET` | `/app` | Browser dashboard (six screens, live data) |
| `GET` | `/ui?invoice=&customers=` | Single-page view of the explainer + spike alert |
| `GET` | `/v1/customers`, `/v1/plans`, `/v1/invoices`, `/v1/overview` | Read-only lists for the dashboard |
| `GET` | `/v1/usage/recent`, `/v1/usage/hourly?customer_id=` | Recent events; hourly token series |
| `GET` | `/v1/prices/{id}/preview?raw_units=` | Slab **and** volume rating of a usage price (no write) |
| `GET` | `/v1/subscriptions/{id}/upgrade-preview?target_plan_id=&effective_date=` | Proration credit/charge/net (no write) |

Timestamps accept **either** epoch seconds or ISO-8601, so every example in
`docs/API.md` and `docs/PRD.md` runs verbatim. The tenant comes from an optional
`X-Tenant-ID` header, defaulting to `TENANT_ID` in `.env`.

## Configuration

Copy `.env.example` to `.env`. Every setting has a working default; **no secrets are
required to run anything in this repository.**

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLite connection string |
| `BILLING_NOW` | Injected ISO-8601 server time; empty = real clock |
| `BILLING_PERIOD_MINUTES` | `0` = calendar months; `>0` = minute-long cycles for demos |
| `AI_PROVIDER_API_KEY` | **Optional.** Empty = deterministic template fallback |
| `AI_MODEL` | Model used only when a key is set (`claude-opus-5-5`) |
| `SPIKE_THRESHOLD_FACTOR` | Spike multiple over the 7-day hourly average (`3.0`) |
| `MAX_PAST_DRIFT_DAYS` / `MAX_FUTURE_DRIFT_MINUTES` | Event timestamp drift bounds |
| `TENANT_ID` | Default tenant when no `X-Tenant-ID` header is sent |

`.env`, `venv/`, `node_modules/` and `*.db` are gitignored. `.env.example` contains
names and comments only.

## Notes on reading `docs/`

`docs/` was the only input to this build. Where it was ambiguous or self-conflicting,
the simplest reading was chosen and recorded — with the reasoning — in
**`docs/AGENT_LOG.md` section 3** (16 entries). Nothing above that section was
modified. The most consequential one: `OBSERVATIONS.md` section 2D gives the
day-based proration coefficient as `daysBetween + 1`, which would yield `16/31` for
the Killer Test 2 cycle, while `PRD.md` section 5 requires exactly `15/30 = 0.5000`
and its own edge cases agree with the no-`+1` form. The acceptance criteria won.
