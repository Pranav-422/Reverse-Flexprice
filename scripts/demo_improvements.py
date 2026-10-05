"""Demo of the two improvements from docs/GAPS.md section 2.

Imported and run by scripts/demo.py after the three Killer Tests.
"""
import os
import threading

import httpx

from app.core.money import format_rupees

HEADERS = {"X-Tenant-ID": "tenant_demo"}

TIERS = [{"tier_index": 0, "up_to_units": 1000,
          "unit_amount_micro_paise": 100_000, "flat_amount_paise": 500},
         {"tier_index": 1, "up_to_units": None,
          "unit_amount_micro_paise": 50_000, "flat_amount_paise": 1_000}]


def run_improvements(client, banner, step, note, call, check, set_now):
    _fix(client, banner, step, note, call, check, set_now)
    _differentiator(client, banner, step, note, call, check, set_now)


# --------------------------------------------------------------------------- #
# IMPROVEMENT 1 (FIX)
# --------------------------------------------------------------------------- #
def _fix(client, banner, step, note, call, check, set_now):
    banner("IMPROVEMENT 1 (FIX)  --  Deterministic dedup + sub-paise rating\n"
           "docs/GAPS.md section 2, Improvement 1.\n"
           "Acceptance: two concurrent posts of evt_retry_10 (50,000 tokens)\n"
           "            -> 1 row, 50,000 tokens, billed exactly Rs 5.00.")

    set_now(client, "2026-10-05T12:00:10Z")
    step("SEED: Rs 0.10 per 1,000 tokens = 100,000 micro-paise per unit")
    call(client, "POST", "/v1/meters", json={
        "id": "m_fix", "name": "LLM Inference Tokens", "event_name": "fix_tokens",
        "aggregation_type": "SUM", "value_property": "total_tokens",
        "divide_by": 1000, "round_type": "none"})
    call(client, "POST", "/v1/plans",
         json={"id": "pl_fix", "name": "Fix Plan", "lookup_key": "fix"})
    call(client, "POST", "/v1/prices", json={
        "id": "prc_fix", "plan_id": "pl_fix", "meter_id": "m_fix",
        "type": "usage", "tier_mode": "slab",
        "tiers": [{"tier_index": 0, "up_to_units": None,
                   "unit_amount_micro_paise": 100_000, "flat_amount_paise": 0}]})
    call(client, "POST", "/v1/customers",
         json={"id": "c_fix", "external_id": "ext_fix", "name": "Retry Co"})
    call(client, "POST", "/v1/subscriptions", json={
        "id": "s_fix", "customer_id": "c_fix", "plan_id": "pl_fix",
        "start_date": "2026-10-01T00:00:00Z"})

    step("Two CONCURRENT posts of evt_retry_10 at 12:00:00 and 12:00:02")
    payload = {"event_id": "evt_retry_10", "event_name": "fix_tokens",
               "customer_id": "c_fix", "properties": {"total_tokens": 50000}}
    statuses = []
    barrier = threading.Barrier(2)

    def worker(ts):
        def run():
            with httpx.Client(base_url=client.base_url, timeout=30) as c:
                barrier.wait()
                statuses.append(c.post("/v1/events", json=dict(payload, timestamp=ts),
                                       headers=HEADERS).json()["status"])
        return run

    threads = [threading.Thread(target=worker("2026-10-05T12:00:00Z")),
               threading.Thread(target=worker("2026-10-05T12:00:02Z"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("one created, one skipped", ["created", "duplicate_skipped"],
          sorted(statuses))
    check("rows stored", 1, call(client, "GET", "/v1/events",
                                 params={"event_id": "evt_retry_10"}).json()["count"])

    step("Invoice it")
    inv = call(client, "POST", "/v1/invoices/generate", json={
        "subscription_id": "s_fix", "period_start": "2026-10-01T00:00:00Z",
        "period_end": "2026-11-01T00:00:00Z"}).json()
    line = next(li for li in inv["line_items"] if li["price_id"] == "prc_fix")
    check("exact micro-paise (50 x 100,000)", 5_000_000,
          line["metadata"]["amount_micro_paise"])
    check("billed amount_paise (Rs 5.00)", 500, line["amount_paise"])

    step("THE ACTUAL FIX: sub-paise rating with a non-exact token count")
    note("1,500,500 raw tokens at divide_by=1000 is 1,500.5 billable units.")
    note("BEFORE (pre-rounding units to 1,501): 14,005 paise -- overcharge.")
    note("AFTER  (exact, round once at the line): 14,002.5 -> 14,003 paise.")
    call(client, "POST", "/v1/plans",
         json={"id": "pl_frac", "name": "Frac Plan", "lookup_key": "frac"})
    call(client, "POST", "/v1/prices", json={
        "id": "prc_frac", "plan_id": "pl_frac", "meter_id": "m_fix",
        "type": "usage", "tier_mode": "slab", "tiers": TIERS,
        "display_name": "LLM Inference Tokens"})
    call(client, "POST", "/v1/customers",
         json={"id": "c_frac", "external_id": "ext_frac", "name": "Fraction Co"})
    call(client, "POST", "/v1/subscriptions", json={
        "id": "s_frac", "customer_id": "c_frac", "plan_id": "pl_frac",
        "start_date": "2026-10-01T00:00:00Z"})
    call(client, "POST", "/v1/events", json={
        "event_id": "evt_frac", "event_name": "fix_tokens", "customer_id": "c_frac",
        "timestamp": "2026-10-05T11:00:00Z",
        "properties": {"total_tokens": 1_500_500}})
    inv2 = call(client, "POST", "/v1/invoices/generate", json={
        "subscription_id": "s_frac", "period_start": "2026-10-01T00:00:00Z",
        "period_end": "2026-11-01T00:00:00Z"}).json()
    lf = next(li for li in inv2["line_items"] if li["price_id"] == "prc_frac")

    # Show the old, pre-rounded answer side by side with the exact one.
    from app.services.invoice import load_tiers
    from app.services.pricing import calculate_cost
    naive = calculate_cost(1501, load_tiers("prc_frac"), "slab").amount_paise
    print(f"      pre-rounded (upstream behaviour) : {naive:,} paise")
    print(f"      exact (this engine)              : {lf['amount_paise']:,} paise")
    check("exact micro-paise total", 140_025_000,
          lf["metadata"]["amount_micro_paise"])
    check("billed amount_paise", 14_003, lf["amount_paise"])
    check("not the pre-rounded answer", True, lf["amount_paise"] != naive)

    step("Rs 0.002 per TOKEN (0.2 paise) over 10 single-token requests")
    note("Upstream rounds each charge to 2 INR decimals -> 0 paise, revenue gone.")
    call(client, "POST", "/v1/meters", json={
        "id": "m_micro", "name": "Prompt Tokens", "event_name": "micro_tokens",
        "aggregation_type": "SUM", "value_property": "total_tokens",
        "divide_by": 1, "round_type": "none"})
    call(client, "POST", "/v1/plans",
         json={"id": "pl_micro", "name": "Micro Plan", "lookup_key": "micro"})
    call(client, "POST", "/v1/prices", json={
        "id": "prc_micro", "plan_id": "pl_micro", "meter_id": "m_micro",
        "type": "usage", "tier_mode": "slab",
        "tiers": [{"tier_index": 0, "up_to_units": None,
                   "unit_amount_micro_paise": 2_000, "flat_amount_paise": 0}]})
    call(client, "POST", "/v1/customers",
         json={"id": "c_micro", "external_id": "ext_micro", "name": "Micro Co"})
    call(client, "POST", "/v1/subscriptions", json={
        "id": "s_micro", "customer_id": "c_micro", "plan_id": "pl_micro",
        "start_date": "2026-10-01T00:00:00Z"})
    for i in range(10):
        client.post("/v1/events", headers=HEADERS, json={
            "event_id": f"evt_micro_{i}", "event_name": "micro_tokens",
            "customer_id": "c_micro", "timestamp": "2026-10-05T11:59:00Z",
            "properties": {"total_tokens": 1}})
    print("   \033[2m10 x POST /v1/events (1 token each)\033[0m")
    inv3 = call(client, "POST", "/v1/invoices/generate", json={
        "subscription_id": "s_micro", "period_start": "2026-10-01T00:00:00Z",
        "period_end": "2026-11-01T00:00:00Z"}).json()
    lm = next(li for li in inv3["line_items"] if li["price_id"] == "prc_micro")
    check("exact micro-paise (10 x 2,000)", 20_000,
          lm["metadata"]["amount_micro_paise"])
    check("billed paise (NOT 0)", 2, lm["amount_paise"])


# --------------------------------------------------------------------------- #
# IMPROVEMENT 2 (DIFFERENTIATOR)
# --------------------------------------------------------------------------- #
def _differentiator(client, banner, step, note, call, check, set_now):
    banner("IMPROVEMENT 2 (DIFFERENTIATOR)  --  Invoice explainer + spike alert\n"
           "docs/GAPS.md section 2, Improvement 2.\n"
           "GET /v1/invoices/{id}/explanation   GET /v1/customers/{id}/spike-status\n"
           "AI is OPTIONAL: no key -> deterministic template, still HTTP 200.")

    key_set = bool(os.environ.get("AI_PROVIDER_API_KEY", "").strip())
    note(f"AI_PROVIDER_API_KEY is {'SET' if key_set else 'EMPTY'} -> "
         f"{'AI narration with template fallback' if key_set else 'template fallback'}")
    h = call(client, "GET", "/health").json()
    check("health reports explainer mode", "live" if key_set else "template_fallback",
          h["ai_explainer"])

    step("SEED: 1,500,000 tokens on slab pricing + a Day-15 upgrade netting Rs 30.00")
    set_now(client, "2026-04-01T00:00:00Z")
    call(client, "POST", "/v1/meters", json={
        "id": "m_x", "name": "LLM Inference Tokens", "event_name": "x_tokens",
        "aggregation_type": "SUM", "value_property": "total_tokens",
        "divide_by": 1000, "round_type": "none"})
    call(client, "POST", "/v1/customers",
         json={"id": "c_x", "external_id": "ext_x", "name": "Acme AI"})
    for pid, name, key, paise, price in (
            ("pl_basic", "Basic Plan", "basic", 6_000, "prc_basic_fixed"),
            ("pl_growth", "Growth Plan", "growth", 12_000, "prc_growth_fixed")):
        call(client, "POST", "/v1/plans",
             json={"id": pid, "name": name, "lookup_key": key})
        call(client, "POST", "/v1/prices", json={
            "id": price, "plan_id": pid, "type": "fixed",
            "fixed_amount_paise": paise})
        call(client, "POST", "/v1/prices", json={
            "id": f"prc_tok_{key}", "plan_id": pid, "meter_id": "m_x",
            "type": "usage", "tier_mode": "slab", "tiers": TIERS,
            "display_name": "LLM Inference Tokens"})
    call(client, "POST", "/v1/subscriptions", json={
        "id": "s_x", "customer_id": "c_x", "plan_id": "pl_basic",
        "start_date": "2026-04-01T00:00:00Z"})
    set_now(client, "2026-04-10T00:00:00Z")
    call(client, "POST", "/v1/events", json={
        "event_id": "evt_x", "event_name": "x_tokens", "customer_id": "c_x",
        "timestamp": "2026-04-09T12:00:00Z",
        "properties": {"total_tokens": 1_500_000}})
    set_now(client, "2026-04-16T00:00:00Z")
    up = call(client, "POST", "/v1/subscriptions/s_x/upgrade", json={
        "target_plan_id": "pl_growth",
        "proration_behavior": "create_prorations"}).json()
    check("proration net (Rs 30.00)", 3_000,
          up["settlement_invoice"]["amount_due_paise"])
    set_now(client, "2026-05-01T00:00:00Z")
    inv = call(client, "POST", "/v1/invoices/generate", json={
        "subscription_id": "s_x", "period_start": "2026-04-01T00:00:00Z",
        "period_end": "2026-05-01T00:00:00Z"}).json()
    print(f"   \033[2minvoice total: Rs {format_rupees(inv['amount_due_paise'])}"
          f"\033[0m")

    step("GET the plain-language explanation")
    body = call(client, "GET", f"/v1/invoices/{inv['id']}/explanation").json()
    f = body["facts"]
    check("HTTP 200 with a narrative", True, bool(body["explanation"]))
    check("source", "ai" if key_set and not body["ai_error"] else "template_fallback",
          body["source"])
    check("tier 1 units", 1000, f["tier_1_units"])
    check("tier 1 cost_paise", 10_500, f["tier_1_cost_paise"])
    check("tier 2 units", 500, f["tier_2_units"])
    check("tier 2 cost_paise", 3_500, f["tier_2_cost_paise"])
    check("proration credit_paise", -3_000, f["proration_credit_paise"])
    check("proration charge_paise", 6_000, f["proration_charge_paise"])
    check("proration days", 15, f["proration_days"])
    print("\n   \033[1mEXPLANATION:\033[0m")
    for chunk in _wrap(body["explanation"], 72):
        print(f"     {chunk}")
    if body.get("ai_error"):
        print(f"\n   \033[2mAI unavailable ({body['ai_error'][:60]}...) -- "
              f"fell back to the template, response still 200.\033[0m")

    step("Prove the app survives with NO AI key at all")
    saved = os.environ.get("AI_PROVIDER_API_KEY", "")
    os.environ["AI_PROVIDER_API_KEY"] = ""
    nk = call(client, "GET", f"/v1/invoices/{inv['id']}/explanation").json()
    check("still HTTP 200 without a key", "template_fallback", nk["source"])
    check("ai_enabled flag", False, nk["ai_enabled"])
    check("same money as the AI path", f["total_paise"], nk["facts"]["total_paise"])
    os.environ["AI_PROVIDER_API_KEY"] = saved

    step("SPIKE DETECTOR: steady 1,000 tokens/hour for 7 days, then a runaway loop")
    note("docs/GAPS.md: flag when the trailing hour exceeds 3.0x the 7-day average")
    call(client, "POST", "/v1/customers",
         json={"id": "c_spike", "external_id": "ext_spike", "name": "Runaway Co"})
    set_now(client, "2026-04-08T00:00:00Z")
    for hh in range(168):
        day, hour = divmod(hh, 24)
        client.post("/v1/events", headers=HEADERS, json={
            "event_id": f"evt_sp_{hh:03d}", "event_name": "x_tokens",
            "customer_id": "c_spike",
            "timestamp": f"2026-04-{1 + day:02d}T{hour:02d}:00:00Z",
            "properties": {"total_tokens": 1_000}})
    print("   \033[2m168 x POST /v1/events (1,000 tokens/hour baseline)\033[0m")

    set_now(client, "2026-04-08T00:30:00Z")
    quiet = call(client, "GET", "/v1/customers/c_spike/spike-status").json()
    check("baseline tokens/hour", 1000, quiet["baseline_hourly_tokens"])
    check("quiet before the burst", False, quiet["spike_detected"])

    call(client, "POST", "/v1/events", json={
        "event_id": "evt_runaway", "event_name": "x_tokens",
        "customer_id": "c_spike", "timestamp": "2026-04-08T00:05:00Z",
        "properties": {"total_tokens": 10_000}})
    hot = call(client, "GET", "/v1/customers/c_spike/spike-status").json()
    check("current hour tokens", 10_000, hot["current_hour_tokens"])
    check("ratio over baseline", 10.0, hot["ratio"])
    check("SPIKE DETECTED", True, hot["spike_detected"])
    print(f"   \033[2m{hot['message']}\033[0m")


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(line)
    return out
