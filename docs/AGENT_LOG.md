# Agent Log & Verification Audit Trail

This document provides a chronological record of the key prompts in this reverse-engineering engagement and a comprehensive log of all code corrections made during source-verification sweeps.

---

## 1. Key Conversation Prompts

1. **Role & Constraints Setup**: Initialized reverse-engineering analyst for usage-based billing engine (read-only mode, exact file citations, plain words and simple math, no code snippets).
2. **Architecture & Stack Discovery**: Extracted exact dependency versions, local run commands, environment variables, folder topology, stub files, and hard-core file mapping.
3. **API Catalog & Entity Relation Mapping**: Mapped 224 HTTP routes, built Mermaid ER diagram proving true database Foreign Keys vs Ent ORM edges vs plain string IDs, and analyzed database indexes and unused fields.
4. **Billing Rules & Worked Calculations**: Formulated exact engine mechanics for deduplication, meter window slicing, volume vs slab tiered pricing, day/second proration coefficients, and hand-worked concrete numerical examples.
5. **End-to-End Traces**: Mapped execution Trace A (usage event to invoice line) and Trace B (15th-day upgrade proration to settlement invoice) with Mermaid sequence diagrams, checks, missing checks, and failure paths.
6. **User Journey & Test Fixture Audit**: Documented the 8-step canonical user journey (routes, files, exact field names) and cataloged all mock, demo, and test script files.
7. **Senior Engineering Review**: Evaluated 11 architectural gaps across race conditions, precision truncation, proration error masking, and product fit for an Indian AI API startup billing per 1,000 tokens in INR.
8. **Comprehensive Code Re-Verification**: Re-opened every cited file, verified line numbers and claims, tagged each as `[Confirmed]`, and cataloged all discrepancy corrections.
9. **Documentation Synthesis ("WRITE DOCS")**: Authored the complete 7-document blueprint in `docs/` for an independent rebuild agent.

---

## 2. Corrections & Verification Log

During the rigorous re-opening and cross-verification of source files in Stage 8, the following citation corrections and refinements were made:

| # | Item | Original Citation | Verified & Corrected Citation | Reason & Verification Details |
| :---: | :--- | :--- | :--- | :--- |
| **1** | **Currency Precision Path** | `internal/domain/currency/currency.go:35-55` | `internal/types/currency.go:11-50,72-76,124-141` `[Confirmed]` | `internal/domain/currency/currency.go` does not exist in the repository. Currency configurations (`CURRENCY_CONFIG`, precision maps, and `RoundToCurrencyPrecision`) are located in `internal/types/currency.go`. |
| **2** | **Window Formatters** | `internal/repository/clickhouse/meter_usage_query_builder.go:104,272-330` | `internal/repository/clickhouse/aggregators.go:180-228` `[Confirmed]` | The functions `formatWindowSize` and `formatWindowSizeWithBillingAnchor` are declared in `aggregators.go`. Query builder line 272 is merely a call site. Updated citation to the source definition. |
| **3** | **Subscription Period Boundaries** | `internal/domain/subscription/model.go:102-110` | `internal/domain/subscription/model.go:53-57` `[Confirmed]` | Lines 102–110 define `OverageFactor` and `PaymentBehavior`. The fields `CurrentPeriodStart` and `CurrentPeriodEnd` are defined on lines 53 and 57 of the same model file. |
| **4** | **Proration Charge Calculation** | `internal/domain/proration/calculator.go:95-104` | `internal/domain/proration/calculator.go:95-115,129-144` `[Confirmed]` | Lines 95–115 specifically handle outgoing plan credits (`shouldIssueCredit`). Incoming plan charges (`shouldIssueCharge`) are calculated on lines 129–144. Expanded range to cover both. |
| **5** | **Meter Aggregation Enums** | `internal/domain/meter/model.go:56-65` | `internal/types/aggregation.go:8-15` `[Confirmed]` | While `meter/model.go` references `types.AggregationType`, the concrete enum values (`COUNT`, `SUM`, `AVG`, `COUNT_UNIQUE`, `MAX`) are declared in `internal/types/aggregation.go`. |
| **6** | **Tier Inclusivity Model Annotation** | `internal/domain/price/model.go:348-352` | `internal/domain/price/model.go:348-352` `[Confirmed]` | Re-verified the exact struct comment: "Tier boundaries are INCLUSIVE ... quantity less than or equal to 1000 belongs to this tier" and matching service check at `internal/ee/service/price.go:1179`. |
| **7** | **Invoice Unique Constraints** | `ent/schema/invoice.go:285-288` | `ent/schema/invoice.go:285-292` `[Confirmed]` | Re-verified both indexes: `Idx_tenant_environment_idempotency_key_unique` (lines 285-288) and `idx_subscription_period_unique` (lines 289-292). |

---

## 3. Rebuild Deviation Log (clean-room build from `docs/` only)

This section is appended by the rebuild agent. Nothing above it was modified.
Every entry records a point where `docs/` was ambiguous or self-conflicting,
the simplest reading that was chosen, and why.

| # | Deviation: what | Why |
| :---: | :--- | :--- |
| **D1** | **Day-based proration coefficient drops the `+1`.** Implemented as `remaining_days / total_days` using floor-day differences, not `(daysBetween + 1)`. See `app/core/time.py:whole_days_between`. | OBSERVATIONS.md section 2D gives `totalDays = daysBetween(start, end) + 1`, which for the Killer Test 2 cycle yields 16/31, but PRD.md section 5 Killer Test 2 requires **exactly** `total days = 30`, `remaining = 15`, `coefficient = 15/30 = 0.5000`. The PRD.md section 6 case 3 edge cases agree with the no-`+1` form (Day 1 -> 30/30 = 1.0; Day 30 -> 1/30). Acceptance criteria are authoritative over the narrative formula, so the `+1` is not applied. |
| **D2** | **`round_type = 'none'` rounds half-up to the nearest whole unit** instead of keeping a fraction. See `app/services/meter.py:to_billable_units`. | OBSERVATIONS.md section 2C defines `BilledUnits = round(RawUnits / DivideBy)` with `none` as a valid mode, but DATA_MODEL.md section 3 types both `invoice_line_items.quantity` and the usage response `quantity` as `INTEGER`. A non-integer billable quantity is therefore unrepresentable. Every documented scenario divides exactly (50,000/1,000 and 1,500,000/1,000), so this only affects undocumented non-exact inputs. |
| **D3** | **Tenant id comes from an optional `X-Tenant-ID` header, defaulting to the `TENANT_ID` env setting.** See `app/api/deps.py`. | DATA_MODEL.md makes `tenant_id` `NOT NULL` on every table, but API.md defines no authentication, no tenant field in any request body, and no tenant header. The simplest reading that satisfies both is an optional header with an env-configured default, so every documented request body works verbatim. `TENANT_ID` was added to `.env.example` since ARCHITECTURE.md section 7 does not list it. |
| **D4** | **Create endpoints accept an optional client-supplied `id`.** | API.md shows server-generated ids (`"id": "string"`), but PRD.md section 7 scenarios reference fixed ids (`cust_ai_01`, `meter_tokens`, `sub_01`, `plan_pro`) in later requests, and DATA_MODEL.md gives human-readable id examples (`cust_101`, `plan_starter`). Accepting an optional `id` makes the documented scenarios runnable verbatim; omitting it still auto-generates. |
| **D5** | **Timestamps accept either epoch seconds or an ISO-8601 string.** See `app/core/time.py:parse_ts`. | API.md section 4 shows `"timestamp": 1727776800` (epoch int) while PRD.md section 7 scenario 1 shows `"timestamp": "2026-10-01T10:00:00Z"` (ISO). Both documented forms are supported rather than picking one and breaking the other document's examples. |
| **D6** | **Application code lives at the repository root (`app/`, `tests/`, `scripts/`) rather than under a `rebuild/` directory.** | ARCHITECTURE.md section 6 draws the tree rooted at `rebuild/`. This repository *is* the rebuild, and the required deliverables (`README.md`, `SUBMISSION.md`, `.env.example`, `deck.pdf`) live at the repo root, so an extra nesting level would split the project across two roots. The internal structure under `app/` follows ARCHITECTURE.md section 6 exactly. |
| **D7** | **The token value property is whatever the meter's `value_property` declares; the Killer Test uses `total_tokens`.** | PRD.md section 5 Killer Test 1 specifies `properties.total_tokens`, while PRD.md section 7 scenario 1 shows `properties: {"tokens": 50000}`. DATA_MODEL.md section 3 makes `value_property` a configurable meter column, so the meter configuration resolves the conflict rather than a hardcoded key. The automated test follows the acceptance criterion (`total_tokens`). |
| **D8** | **`UNIQUE(subscription_id, period_start, period_end)` is a partial index covering only `subscription_cycle` invoices.** See `app/db/models.py:CYCLE_UNIQUE_INDEX`. | DATA_MODEL.md section 7 lists the constraint unqualified, but it is unsatisfiable for `one_off` proration invoices: those carry the same `subscription_id` and an instantaneous period (`period_start == period_end == effective_date`), so two upgrades on one subscription would collide. Restricting it to cycle invoices preserves the documented intent (one invoice per subscription per billing cycle) while leaving proration settlements possible. `one_off` invoices remain idempotent via `UNIQUE(tenant_id, idempotency_key)`. |
| **D9** | **Event timestamp drift validation is implemented as part of the core `POST /v1/events` contract, not as a scored improvement.** See `app/services/ingestion.py:validate_timestamp`. | API.md section 4 lists the `400 Bad Request` for timestamps older than `MAX_PAST_DRIFT_DAYS` or more than `MAX_FUTURE_DRIFT_MINUTES` ahead as a documented response of the endpoint, and ARCHITECTURE.md section 7 defines both env vars. A rebuild that omitted it would not conform to API.md. It is therefore contract conformance; the two **scored** improvements remain exactly the two specified in GAPS.md section 2. |
| **D10** | **Negative numeric event property values are rejected with `400`, not coerced to zero.** See `app/services/ingestion.py:_extract_quantity`. | `usage_events.quantity` carries no documented negative semantics and DATA_MODEL.md defines no signed-delta or usage-credit flow (PRD.md section 4 lists credit notes as out of scope). Rejecting is the simplest representable behaviour. Logged for transparency because it coincides with GAPS.md gap 4; it is ordinary input validation and is **not** counted as one of the two improvements. |
| **D11** | **`prices.display_name` column added (nullable).** | API.md section 2 output and PRD.md section 7 scenario 3 both show an invoice line `display_name` / `description` such as `"LLM Inference Tokens (Tiered Slab)"` that is not derivable from the DATA_MODEL.md `prices` columns alone. A nullable column carries it; when unset the description falls back to the meter or plan name. |
| **D12** | **Dependency versions are floors (`>=`), not exact pins, and `pydantic`/`fastapi` resolve to current releases.** | docs/ specifies Python 3.12 + FastAPI + SQLite but no version lock file. The build host runs Python 3.14, for which the originally chosen pins had no `pydantic-core` wheel. Floors keep the documented stack while remaining installable on current interpreters. |
| **D13** | **A cycle invoice's explanation also reports the proration settlement recorded for the same subscription inside that period.** See `app/services/explainer.py:_proration_context`. | GAPS.md Improvement 2 specifies a **single** template string containing tier-1 maths, tier-2 maths, *and* `{proration_credit}` / `{proration_charge}`, and its acceptance criterion describes "an invoice generated with 1,500,000 tokens under Slab pricing **and** an upgrade proration netting Rs 30.00". But DATA_MODEL.md section 7 and API.md section 5 put proration on a separate `one_off` settlement invoice, so no single stored invoice carries both. The simplest reading that makes the documented template coherent is for the cycle invoice's explanation to look up the settlement for the same subscription within the billed period. A `one_off` invoice's own explanation uses its own lines. |
| **D14** | **The spike baseline window excludes the current hour.** `V_current` reads `[now - 1h, now)`; `V_7day` reads `[now - 7d, now - 1h)` divided by 167 hours. See `app/services/spike_detector.py`. | GAPS.md says "current 1-hour consumption vs 7-day average" without stating whether the windows overlap. Overlapping them was implemented first and proved wrong on two counts: a runaway burst inflates its own baseline (diluting the very signal being measured), and a brand-new customer's first hour of traffic manufactures a baseline out of that same traffic and flags itself. Disjoint windows make "no history" genuinely zero. |
| **D15** | **With no 7-day baseline, `spike_detected` is `false` and `reason` is `insufficient_baseline`.** | GAPS.md defines the rule as `V_current > 3.0 x V_7day_average` but says nothing about a zero average, where the comparison is undefined (and any non-zero usage would trivially exceed it). Reporting "no baseline yet" is the simplest reading and avoids a false alarm on every customer's first hour. The endpoint still returns the raw velocity numbers so a caller can apply its own policy. |
| **D16** | **The AI explainer never computes money.** Every figure is derived locally from the sealed invoice; the LLM only rewrites those facts as prose, and the response carries both `explanation` and `template_explanation`. | GAPS.md asks for an AI narrative with a deterministic fallback but does not say which component owns the arithmetic. Letting a model restate pre-computed integers is the only reading under which the AI path and the fallback path can be guaranteed to agree on the amount due — so an outage, a bad key, or a missing package changes the wording and never the money. `AI_MODEL` (default `claude-opus-5-5`) was added to `.env.example`; ARCHITECTURE.md section 7 names only `AI_PROVIDER_API_KEY`. |

---

## 4. Documentation Corrections (post-rebuild, pre-freeze)

Section 3 records readings the rebuild agent chose where `docs/` was ambiguous. The
corrections below were then applied **to the documents themselves**, at the project
owner's explicit instruction, so that an independent agent reading only `docs/`
reaches the same answer without needing section 3. Every correction is a
clarification or a formatting repair; **no billing rule, formula, acceptance
criterion or expected value was changed**, and the rebuilt engine is unaffected
(full suite still green).

| # | Document | Correction | Why |
| :---: | :--- | :--- | :--- |
| **C1** | `OBSERVATIONS.md`, `GAPS.md`, `AGENT_LOG.md` | All **83** evidence citations converted from markdown links wrapping a local Windows `file://` URL (`d:/Reverse-Flexprice/flexprice/...`) into the required inline `` `path:line` `` form. | The links pointed at a local Windows filesystem path. They resolve for nobody on GitHub, and the specified evidence format is `path:line` [Confirmed]. Link text was already the `path:line` string, so the citation content is unchanged — only the dead URL wrapper was removed. |
| **C2** | `OBSERVATIONS.md` section 2D | Added an explicit line to the day-based coefficient: **"Original uses +1; our rebuild uses no +1, per PRD."** | This was the single most consequential conflict in `docs/` (section 3, D1). The upstream `+1` yields `16/31` for the Killer Test 2 cycle while `PRD.md` section 5 demands `15/30 = 0.5000`. The document now states both the upstream behaviour and the rule the rebuild follows, so a stranger agent cannot implement the wrong one by accident. |
| **C3** | `PRD.md` section 7 scenario 1 | `properties: {"tokens": 50000}` corrected to `properties: {"total_tokens": 50000}` (both requests), plus a note that `timestamp` accepts ISO-8601 **or** epoch seconds. | The scenario contradicted its own acceptance criteria in section 5, which specify `properties.total_tokens`. See section 3, D7. |
| **C4** | `API.md` preamble | Documented that every time-bearing field accepts **either** epoch seconds or an ISO-8601 string, and that tenancy comes from an optional `X-Tenant-ID` header defaulting to the `TENANT_ID` setting. | `API.md` showed only epoch while `PRD.md` section 7 showed only ISO, and the tenant partition required by `DATA_MODEL.md` had no documented transport at all. See section 3, D3 and D5. |
| **C5** | `API.md` sections 7 and 8 | Added the routes the engine serves that the spec omitted: `GET /v1/meters/{id}/usage`, `GET /v1/subscriptions/{id}`, `GET /v1/events?event_id=`, `GET /health`, and — required by `GAPS.md` Improvement 2 — `GET /v1/invoices/{id}/explanation` and `GET /v1/customers/{id}/spike-status`. | `GAPS.md` section 2 mandates the two differentiator endpoints but `API.md` never specified their request or response shape. The read routes are needed to verify the Killer Tests (`/v1/events?event_id=` is the storage-level proof that deduplication held). |
| **C6** | `GAPS.md` gap 8 | Evidence tag changed from `[Confirmed]` to `[Likely]`, with a note that a negative finding cannot be pinned to a `path:line`. | "No velocity monitoring exists anywhere in the repository" is an absence-of-evidence claim. A whole-repository audit cannot be line-cited, so `[Confirmed]` overstated it. |

**Effect on section 3:** D1, D3, D5 and D7 were readings forced by ambiguity that the
documents now resolve directly. They are retained for provenance — they record what
the rebuild agent decided and why, before the documents were corrected — and the
engine's behaviour matches both the old reading and the corrected documents.

---

## 5. Reviewer-Feedback Corrections (6 Oct 2026)

Applied after the jury's pre-freeze review. Each `flexprice` claim was re-checked
against the commit studied, `31421e9ff62d00a9f4cdded11d0aad5d32a22f4a`.

| # | Item | Was | Now | Verification |
| :---: | :--- | :--- | :--- | :--- |
| **R1** | Commit studied | `277ada2` (this repository's own first commit) | `31421e9ff62d00a9f4cdded11d0aad5d32a22f4a` | `git -C flexprice rev-parse HEAD` on the clone the docs were written from; now stated in `SUBMISSION.md` and at the top of `OBSERVATIONS.md`. |
| **R2** | Tech stack (`OBSERVATIONS.md` section 1) | Go 1.24.0, Gin v1.10.0, Ent v0.14.1, lib/pq v1.10.9, clickhouse-go v2.30.0, go-redis v9.7.0, Watermill Kafka v3.0.6, Temporal v1.33.0, with `go.mod` lines that did not match | Go 1.27.1, Gin v1.12.0, Ent v0.14.6, lib/pq v1.12.3, clickhouse-go v2.48.0, go-redis v9.22.0, watermill-kafka/v2 v2.5.0, Temporal v1.49.0, Shopspring Decimal v1.4.0 | Each version re-read from `go.mod` at the studied commit, with its line number. Makefile targets, `docker-compose.yml` range and environment variable names were corrected the same way (for example `FLEXPRICE_POSTGRES_DBNAME`, not `_DB`; Redis comes from `config.yaml`, not `.env.local`). |
| **R3** | `idx_subscription_period_unique` | Listed as a PostgreSQL *unique* index | A plain, non-unique index despite its name | `ent/schema/invoice.go:289-291` has no `.Unique()`; the baseline migration emits `CREATE INDEX`, not `CREATE UNIQUE INDEX`. Our rebuild's index is genuinely `UNIQUE`. |
| **R4** | Gap 6 wording | "Plan change V2 forbids in-place upgrades between billing intervals" | v2 does not support interval or currency changes and its error hint points callers to the v1 endpoint, which does | `subscription_change_v2.go:89-106` (hint text) and `router.go:373-378` (v1 and v2 routes side by side). |

Non-documentation changes made in the same pass: Windows run commands and `.cmd`
launchers, a `pytest.ini` so a local `flexprice/` clone is never collected as tests,
and a browser dashboard (`/app`) over new read-only views. None of these change
billing behaviour, and all existing tests pass unchanged.
