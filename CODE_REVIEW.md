# HACKBACK code review · DBG-136 · Usage-based Billing Engine
- Reviewed at: 2026-10-06T12:44:17Z (2026-10-06T18:14:17+05:30 IST)
- Judged commit: db93098e43c7cfb62becbc825795a06ed29b77c9 (2026-10-06T11:24:40+05:30) · the last commit before the code freeze
- Reviewer: AI agent run by a HACKBACK judge

### DBG-136 · Usage-based Billing Engine
Commit: db93098e43c7cfb62becbc825795a06ed29b77c9 · 2026-10-06T11:24:40+05:30 · Clean-room: OK

| Section | Score | Why (path:line) |
|---|---|---|
| A. Core flow | 23/30 | Works end to end: single-event ingest with unique ID (app/api/routes/events.py:12, app/services/ingestion.py:105), SUM/COUNT meters over half-open windows (app/services/meter.py:24-52), slab/volume tiers in integer micro-paise per 1,000 tokens (app/services/pricing.py:167-226), idempotent cycle invoice (app/services/invoice.py:172-209). Gaps: no batch ingest (not found in app/api/routes/events.py or docs/API.md); a mid-period upgrade prorates only fixed fees (app/services/subscription.py:120-134) and usage is never split across old/new plan, because build_cycle_lines prices the whole period on the current plan (app/services/invoice.py:117-124, 141-163). |
| B. Killer Tests | 26/30 | See below. 44 tests run and pass (pytest: 44 passed in 2.95s). |
| C. Two improvements | 20/20 | Both named in docs/GAPS.md:48-80 and fully built and tested. |
| D. Built from their docs | 9/10 | Tables, constraints and routes match DATA_MODEL/API/PRD (app/db/models.py:1-143, PRD.md:64-95 vs tests). Deviations (the day "+1", optional tenant header) are logged openly (app/core/time.py:76-85, app/api/deps.py:7-14). Not matched: the PRD says cycle invoices follow upgrades, but no test covers a post-upgrade cycle invoice. |
| E. Engineering | 6/10 | Good: ingest validates customer (ingestion.py:96-99), numeric/negative/fractional values (ingestion.py:31-49), timestamp drift window (ingestion.py:54-81), 409 for a sealed period (ingestion.py:84-102, 135-142), dedup before the seal gate (ingestion.py:121-132), .env.example only (.gitignore, .env.example), README run steps work (tests ran). Missing: no authentication or permission check on any route; tenant is a client-supplied header (app/api/deps.py:7-14); ingest does not check that the event_name has a meter, so an unknown event is stored with quantity 0 (ingestion.py:28-30); catalog `int(body.get("divide_by") or 1)` raises a 500 on non-numeric input (app/api/routes/catalog.py:31); `upgrade` writes invoice, line items and plan in separate statements with no transaction (app/services/subscription.py:158-181). |
| Total | 84/100 | |

Killer Tests:
1. READY · 10/10 · `CREATE UNIQUE INDEX ... (tenant_id, event_id)` at app/db/models.py:100-101. The real arbiter is a single INSERT that catches IntegrityError and returns 200 `duplicate_skipped` (app/services/ingestion.py:144-169). The earlier SELECT is only a fast path. Proven by tests/test_killer_1_dedup.py:76,132,152,169 (concurrent), tests/test_fix_dedup_micropaise.py:110,133 (mutated timestamp, replay after 24h).
2. PARTIAL · 6/10 · Fixed-fee proration is correct: day coefficient 15/30 (app/services/proration.py:42-60), credit -150,000 and charge +450,000 paise (proration.py:63-74), tested with fixed dates and a frozen clock (tests/test_killer_2_proration.py:58,106,126). The hole is that usage before the switch is not priced on the old plan and usage after it on the new plan. The next cycle invoice rates all usage on the current plan (app/services/invoice.py:117-124). No test generates a cycle invoice after an upgrade (not found in tests/test_killer_2_proration.py), so the full-period fixed charge on the new plan next to the settlement invoice is unverified.
3. READY · 10/10 · Slab and volume are both documented (docs/PRD.md:86-95) and implemented the same way (app/services/pricing.py:167-226). Money is integer micro-paise, half-up rounded once (app/core/money.py:21-30, 54-64). `up_to` is inclusive (pricing.py:191-194, 206-210). Tests: slab 14,000 and volume 8,500 paise, boundary, and a many-event case (tests/test_killer_3_tiered.py:92,115,131,147,158).

Improvements:
1. Deterministic dedup + sub-paise micro-token rating · 10/10 · Matches docs/GAPS.md:48-65. Unique index (models.py:100-101), integer micro-paise rates (models.py:44-51), exact rating with one rounding step (pricing.py:167-226), and the 500-paise concurrent acceptance test (tests/test_fix_dedup_micropaise.py:56,155,180,196).
2. Invoice explainer + usage-spike alert · 10/10 · Matches docs/GAPS.md:67-80. Routes are wired in (app/api/routes/insights.py:13-33). Spike detection uses disjoint 1h and 7d windows (app/services/spike_detector.py:36-60). The explainer returns 200 with a template fallback when there is no AI key (app/services/explainer.py; tests/test_differentiator_explainer_spike.py:101,170,189,264,293,330).

Flags:
- Clean-room: none. First commit 2026-10-05T19:13+05:30 is docs/ (277ada2), source follows at 19:37 (ec09fdf), no pushed commit after 2026-10-06T13:30 IST except the judge commit. Layout and language (Python/SQLite vs Go/ClickHouse) do not resemble Flexprice. The gitignored `flexprice/` clone is not in the repo (.gitignore:1-4).
- Note (not a violation, GMs decide): a local-only branch `local-gemini` (3d3baf9, 2026-10-06T14:27+05:30, "LOCAL ONLY (not pushed, after code freeze)") exists on the machine I reviewed. It is not on origin and was not judged.
- Committed secrets: none found (searched the judged commit for key patterns).
- Fake: none found. The hosted Vercel demo is labelled as a seeded preview (app/main.py:17-24).

3 questions for the judges to ask:
1. A customer upgrades on day 15 and used 400k tokens before and 600k after. Which plan prices each part, and what does the cycle invoice show? Walk through app/services/invoice.py:117-124.
2. After the day-15 upgrade, `build_cycle_lines` charges the new plan's full fixed price for the whole period while the settlement invoice already charged the prorated amount. Is the customer billed twice? Why is there no test for it?
3. There is no authentication and the tenant comes from a header the caller controls. What stops tenant A from reading tenant B's invoices, and why does ingest accept an event_name that no meter measures (quantity 0)? Also, why is there no batch endpoint for a startup sending millions of events a day?

SCORE core=23 kt=26 imp=20 docs=9 eng=6 total=84
