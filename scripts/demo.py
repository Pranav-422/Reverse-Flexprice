#!/usr/bin/env python3
"""Live, step-by-step demo of the three Killer Tests.

Boots a REAL uvicorn server on a throwaway SQLite file, then drives it over
HTTP with httpx, printing every request, the EXPECTED value taken straight
from docs/PRD.md, the ACTUAL value returned, and a PASS/FAIL verdict.

Run:  ./scripts/demo          (or  python scripts/demo.py)
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Point the engine at a throwaway DB and freeze the clock BEFORE importing it.
_TMP = tempfile.mkdtemp(prefix="billing_demo_")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TMP) / 'demo.db'}"
os.environ["BILLING_PERIOD_MINUTES"] = "0"
os.environ.setdefault("AI_PROVIDER_API_KEY", "")

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from app.core.money import format_rupees  # noqa: E402

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, CYAN, YELLOW = "\033[32m", "\033[31m", "\033[36m", "\033[33m"

HEADERS = {"X-Tenant-ID": "tenant_demo"}
FAILURES: list[str] = []


# --------------------------------------------------------------------------- #
# presentation helpers
# --------------------------------------------------------------------------- #
def banner(text: str) -> None:
    print(f"\n{BOLD}{CYAN}{'=' * 78}\n{text}\n{'=' * 78}{RESET}")


def step(text: str) -> None:
    print(f"\n{BOLD}{YELLOW}-> {text}{RESET}")


def note(text: str) -> None:
    print(f"   {DIM}{text}{RESET}")


def call(client, method: str, path: str, **kw):
    body = kw.get("json")
    print(f"   {DIM}{method} {path}{RESET}")
    if body is not None:
        for line in json.dumps(body, indent=2).splitlines():
            print(f"     {DIM}{line}{RESET}")
    r = client.request(method, path, headers=HEADERS, **kw)
    print(f"   {DIM}<- HTTP {r.status_code}{RESET}")
    return r


def check(label: str, expected, actual) -> None:
    ok = expected == actual
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"   [{mark}] {label}")
    print(f"          expected: {expected!r}")
    print(f"          actual:   {actual!r}")
    if not ok:
        FAILURES.append(label)


# --------------------------------------------------------------------------- #
# server bootstrap
# --------------------------------------------------------------------------- #
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int):
    from app.db.database import init_db
    from app.main import app

    init_db()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5)
            return server
        except httpx.HTTPError:
            time.sleep(0.1)
    raise RuntimeError("server did not start")


def set_now(client, iso: str) -> None:
    """Move the injected clock (BILLING_NOW) that the server reads per request."""
    os.environ["BILLING_NOW"] = iso
    note(f"BILLING_NOW = {iso}   (injected clock -- no waiting for real time)")


# --------------------------------------------------------------------------- #
# KILLER TEST 1
# --------------------------------------------------------------------------- #
def killer_1(client) -> None:
    banner("KILLER TEST 1  --  A duplicate usage event must be counted ONCE\n"
           "docs/PRD.md section 5: 50,000 raw tokens / 1,000 = 50 billable units.\n"
           "A retry must NEVER make that 100,000 tokens / 100 units.")

    set_now(client, "2026-10-01T10:00:10Z")

    step("SEED: meter (SUM of total_tokens, divide_by=1000), plan, price, customer, subscription")
    call(client, "POST", "/v1/meters", json={
        "id": "m_tok", "name": "LLM Inference Tokens", "event_name": "token_usage",
        "aggregation_type": "SUM", "value_property": "total_tokens",
        "divide_by": 1000, "round_type": "none"})
    call(client, "POST", "/v1/plans", json={
        "id": "p_dev", "name": "Developer Plan", "lookup_key": "developer"})
    call(client, "POST", "/v1/prices", json={
        "id": "pr_tok", "plan_id": "p_dev", "meter_id": "m_tok", "type": "usage",
        "tier_mode": "slab",
        "tiers": [{"tier_index": 0, "up_to_units": None,
                   "unit_amount_micro_paise": 100_000, "flat_amount_paise": 0}]})
    call(client, "POST", "/v1/customers", json={
        "id": "c_ai", "external_id": "ai_startup_01", "name": "Acme AI"})
    call(client, "POST", "/v1/subscriptions", json={
        "id": "s_dev", "customer_id": "c_ai", "plan_id": "p_dev",
        "start_date": "2026-10-01T00:00:00Z"})

    payload = {"event_id": "evt_dedup_001", "event_name": "token_usage",
               "customer_id": "c_ai", "timestamp": "2026-10-01T10:00:00Z",
               "properties": {"total_tokens": 50000, "model": "gpt-4o"}}

    step("The AI gateway posts the usage event (first time)")
    r1 = call(client, "POST", "/v1/events", json=payload)
    check("first ingest -> HTTP 201 Created", 201, r1.status_code)
    check("status", "created", r1.json()["status"])
    check("recorded_quantity", 50000, r1.json()["recorded_quantity"])

    step("Network hiccup: the SAME event_id is retried 5 seconds later "
         "with a DIFFERENT timestamp")
    note("This is exactly the case upstream gets wrong (docs/GAPS.md gap 1):")
    note("ClickHouse ReplacingMergeTree keys on timestamp, so a mutated")
    note("timestamp keeps BOTH rows and the customer is billed twice.")
    r2 = call(client, "POST", "/v1/events",
              json=dict(payload, timestamp="2026-10-01T10:00:05Z"))
    check("duplicate -> HTTP 200 (idempotent ack)", 200, r2.status_code)
    check("status", "duplicate_skipped", r2.json()["status"])

    step("Eight CONCURRENT workers all post event_id 'evt_race_99' at once")
    note("docs/PRD.md section 6 case 2: the DB UNIQUE(tenant_id, event_id) index "
         "must permit exactly 1 write.")
    race = {"event_id": "evt_race_99", "event_name": "token_usage",
            "customer_id": "c_ai", "timestamp": "2026-10-01T10:00:00Z",
            "properties": {"total_tokens": 50000}}
    results: list[str] = []
    barrier = threading.Barrier(8)

    def worker():
        with httpx.Client(base_url=client.base_url, timeout=30) as c:
            barrier.wait()
            results.append(c.post("/v1/events", json=race,
                                  headers=HEADERS).json()["status"])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"   {DIM}8 concurrent responses: {results}{RESET}")
    check("exactly 1 worker wrote the row", 1, results.count("created"))
    check("the other 7 degraded gracefully", 7, results.count("duplicate_skipped"))
    rows = call(client, "GET", "/v1/events", params={"event_id": "evt_race_99"})
    check("rows persisted for evt_race_99", 1, rows.json()["count"])

    step("Read the aggregated meter usage")
    r3 = call(client, "GET", "/v1/meters/m_tok/usage",
              params={"customer_id": "c_ai",
                      "period_start": "2026-10-01T00:00:00Z",
                      "period_end": "2026-11-01T00:00:00Z"})
    u = r3.json()
    check("raw_units  (50,000 from evt_dedup_001 + 50,000 from evt_race_99)",
          100000, u["raw_units"])
    check("quantity (billable units = raw / 1,000)", 100, u["quantity"])
    note("Both events are REAL distinct events; the duplicates of each were")
    note("dropped. Without dedup this would read 500,000 tokens / 500 units.")

    step("Generate the cycle invoice")
    r4 = call(client, "POST", "/v1/invoices/generate", json={
        "subscription_id": "s_dev", "period_start": "2026-10-01T00:00:00Z",
        "period_end": "2026-11-01T00:00:00Z"})
    line = next(li for li in r4.json()["line_items"] if li["price_id"] == "pr_tok")
    check("invoice line quantity (never 500)", 100, line["quantity"])
    check("invoice line amount_paise (100 units x 10 paise)", 1000, line["amount_paise"])
    print(f"   {DIM}=> billed Rs {format_rupees(line['amount_paise'])}{RESET}")


# --------------------------------------------------------------------------- #
# KILLER TEST 2
# --------------------------------------------------------------------------- #
def killer_2(client) -> None:
    banner("KILLER TEST 2  --  Mid-month plan upgrade must prorate EXACTLY\n"
           "docs/PRD.md section 5: 30-day April cycle, upgrade on Day 15.\n"
           "  coefficient    = 15 / 30 = 0.5000\n"
           "  Starter credit = -(300,000 x 0.5) = -150,000 paise  (-Rs 1,500.00)\n"
           "  Pro charge     = +(900,000 x 0.5) = +450,000 paise  (+Rs 4,500.00)\n"
           "  NET DUE        =                     300,000 paise  ( Rs 3,000.00)")

    set_now(client, "2026-04-01T00:00:00Z")

    step("SEED: Starter Rs 3,000/mo and Pro Rs 9,000/mo, billed in advance")
    for pid, name, key, paise in (("pl_start", "Starter Plan", "starter", 300_000),
                                  ("pl_pro", "Pro Plan", "pro", 900_000)):
        call(client, "POST", "/v1/plans",
             json={"id": pid, "name": name, "lookup_key": key})
        call(client, "POST", "/v1/prices",
             json={"id": f"prc_{key}", "plan_id": pid, "type": "fixed",
                   "fixed_amount_paise": paise})
        note(f"{name}: {paise} paise = Rs {format_rupees(paise)} / month")
    call(client, "POST", "/v1/customers",
         json={"id": "c_up", "external_id": "ext_up", "name": "Acme AI"})

    step("Customer subscribes to Starter on April 1")
    rs = call(client, "POST", "/v1/subscriptions", json={
        "id": "s_up", "customer_id": "c_up", "plan_id": "pl_start",
        "start_date": "2026-04-01T00:00:00Z"})
    sub = rs.json()
    print(f"   {DIM}period: {sub['current_period_start_iso']} .. "
          f"{sub['current_period_end_iso']}{RESET}")
    check("cycle length in days", 30,
          (sub["current_period_end"] - sub["current_period_start"]) // 86400)

    step("Fast-forward to Day 15 and upgrade to Pro")
    set_now(client, "2026-04-16T00:00:00Z")
    r = call(client, "POST", "/v1/subscriptions/s_up/upgrade", json={
        "target_plan_id": "pl_pro", "proration_behavior": "create_prorations"})
    body = r.json()
    pr, inv = body["proration"], body["settlement_invoice"]

    check("total_days", 30, pr["total_days"])
    check("remaining_days", 15, pr["remaining_days"])
    check("coefficient", "0.5000", pr["coefficient"])
    check("line item count (two opposing lines)", 2, len(inv["line_items"]))
    check("line 1 description", "Unused time on Starter Plan (15 days)",
          inv["line_items"][0]["description"])
    check("line 1 amount_paise (credit)", -150_000, inv["line_items"][0]["amount_paise"])
    check("line 2 description", "Remaining time on Pro Plan (15 days)",
          inv["line_items"][1]["description"])
    check("line 2 amount_paise (charge)", 450_000, inv["line_items"][1]["amount_paise"])
    check("NET amount_due_paise", 300_000, inv["amount_due_paise"])
    print(f"   {DIM}=> settlement: Rs {format_rupees(inv['amount_due_paise'])} due"
          f"{RESET}")

    step("Confirm the subscription actually moved plans")
    check("plan_id after upgrade", "pl_pro",
          call(client, "GET", "/v1/subscriptions/s_up").json()["plan_id"])

    step("Edge case: upgrading on the LAST day (Day 30) -> coefficient 1/30")
    note("docs/PRD.md section 6 case 3: credit -10,000, charge +30,000, "
         "net +20,000 paise")
    set_now(client, "2026-04-01T00:00:00Z")
    call(client, "POST", "/v1/customers",
         json={"id": "c_last", "external_id": "ext_last", "name": "Last Day Co"})
    call(client, "POST", "/v1/subscriptions", json={
        "id": "s_last", "customer_id": "c_last", "plan_id": "pl_start",
        "start_date": "2026-04-01T00:00:00Z"})
    set_now(client, "2026-04-30T00:00:00Z")
    rl = call(client, "POST", "/v1/subscriptions/s_last/upgrade", json={
        "target_plan_id": "pl_pro", "proration_behavior": "create_prorations"})
    li = rl.json()["settlement_invoice"]
    check("remaining_days", 1, rl.json()["proration"]["remaining_days"])
    check("credit", -10_000, li["line_items"][0]["amount_paise"])
    check("charge", 30_000, li["line_items"][1]["amount_paise"])
    check("net due", 20_000, li["amount_due_paise"])


# --------------------------------------------------------------------------- #
# KILLER TEST 3
# --------------------------------------------------------------------------- #
def killer_3(client) -> None:
    banner("KILLER TEST 3  --  Tiered pricing must match the HAND calculation\n"
           "docs/PRD.md section 5, 1,500,000 raw tokens = 1,500 billable units:\n"
           "  SLAB:   1,000 x Rs 0.10 + Rs 5.00  = Rs 105.00 (10,500 paise)\n"
           "        +   500 x Rs 0.05 + Rs 10.00 = Rs  35.00 ( 3,500 paise)\n"
           "        =                              Rs 140.00 (14,000 paise)\n"
           "  VOLUME: 1,500 x Rs 0.05 + Rs 10.00 = Rs  85.00 ( 8,500 paise)")

    set_now(client, "2026-10-06T00:00:00Z")
    tiers = [{"tier_index": 0, "up_to_units": 1000,
              "unit_amount_micro_paise": 100_000, "flat_amount_paise": 500},
             {"tier_index": 1, "up_to_units": None,
              "unit_amount_micro_paise": 50_000, "flat_amount_paise": 1_000}]

    step("SEED: same meter, one SLAB plan and one VOLUME plan on identical tiers")
    note("rates are stored as micro-paise (1 paise = 10,000 micro-paise) so")
    note("sub-paise token prices never round prematurely (docs/GAPS.md gap 7)")
    call(client, "POST", "/v1/meters", json={
        "id": "m_tok3", "name": "LLM Inference Tokens", "event_name": "tokens3",
        "aggregation_type": "SUM", "value_property": "total_tokens",
        "divide_by": 1000, "round_type": "none"})
    call(client, "POST", "/v1/customers",
         json={"id": "c_t3", "external_id": "ext_t3", "name": "Tier Test Co"})
    for mode in ("slab", "volume"):
        call(client, "POST", "/v1/plans", json={
            "id": f"pl_{mode}", "name": f"{mode.title()} Plan", "lookup_key": mode})
        call(client, "POST", "/v1/prices", json={
            "id": f"prc_{mode}", "plan_id": f"pl_{mode}", "meter_id": "m_tok3",
            "type": "usage", "tier_mode": mode, "tiers": tiers,
            "display_name": "LLM Inference Tokens"})
        call(client, "POST", "/v1/subscriptions", json={
            "id": f"s_{mode}", "customer_id": "c_t3", "plan_id": f"pl_{mode}",
            "start_date": "2026-10-01T00:00:00Z"})

    step("Ingest 1,500,000 raw tokens")
    call(client, "POST", "/v1/events", json={
        "event_id": "evt_tier_001", "event_name": "tokens3", "customer_id": "c_t3",
        "timestamp": "2026-10-05T12:00:00Z",
        "properties": {"total_tokens": 1_500_000}})

    step("CASE A -- SLAB (graduated): generate the invoice")
    ia = call(client, "POST", "/v1/invoices/generate", json={
        "subscription_id": "s_slab", "period_start": "2026-10-01T00:00:00Z",
        "period_end": "2026-11-01T00:00:00Z"}).json()
    la = next(li for li in ia["line_items"] if li["price_id"] == "prc_slab")
    check("billable units", 1500, la["quantity"])
    check("tier 1 units", 1000, la["metadata"]["tier_1_units"])
    check("tier 1 cost_paise", 10_500, la["metadata"]["tier_1_cost_paise"])
    check("tier 2 units", 500, la["metadata"]["tier_2_units"])
    check("tier 2 cost_paise", 3_500, la["metadata"]["tier_2_cost_paise"])
    check("invoice amount_due_paise", 14_000, ia["amount_due_paise"])
    print(f"   {DIM}=> Rs {format_rupees(ia['amount_due_paise'])}{RESET}")

    step("CASE B -- VOLUME: same usage, same tiers, single bracket")
    ib = call(client, "POST", "/v1/invoices/generate", json={
        "subscription_id": "s_volume", "period_start": "2026-10-01T00:00:00Z",
        "period_end": "2026-11-01T00:00:00Z"}).json()
    lb = next(li for li in ib["line_items"] if li["price_id"] == "prc_volume")
    check("matched_tier_index (1,500 > 1,000 so tier 2)", 1,
          lb["metadata"]["matched_tier_index"])
    check("invoice amount_due_paise", 8_500, ib["amount_due_paise"])
    print(f"   {DIM}=> Rs {format_rupees(ib['amount_due_paise'])}{RESET}")

    step("Edge case: EXACTLY 1,000 units -- up_to is INCLUSIVE, stays in tier 1")
    note("docs/PRD.md section 6 case 1: expect 10,500 paise, tier 2 untouched")
    call(client, "POST", "/v1/plans",
         json={"id": "pl_edge", "name": "Edge Plan", "lookup_key": "edge"})
    call(client, "POST", "/v1/prices", json={
        "id": "prc_edge", "plan_id": "pl_edge", "meter_id": "m_tok3",
        "type": "usage", "tier_mode": "slab", "tiers": tiers,
        "display_name": "LLM Inference Tokens"})
    call(client, "POST", "/v1/customers",
         json={"id": "c_edge", "external_id": "ext_edge", "name": "Edge Co"})
    call(client, "POST", "/v1/subscriptions", json={
        "id": "s_edge", "customer_id": "c_edge", "plan_id": "pl_edge",
        "start_date": "2026-10-01T00:00:00Z"})
    call(client, "POST", "/v1/events", json={
        "event_id": "evt_edge_001", "event_name": "tokens3", "customer_id": "c_edge",
        "timestamp": "2026-10-05T12:00:00Z",
        "properties": {"total_tokens": 1_000_000}})
    ie = call(client, "POST", "/v1/invoices/generate", json={
        "subscription_id": "s_edge", "period_start": "2026-10-01T00:00:00Z",
        "period_end": "2026-11-01T00:00:00Z"}).json()
    le = next(li for li in ie["line_items"] if li["price_id"] == "prc_edge")
    check("units", 1000, le["quantity"])
    check("amount_paise (tier 1 only)", 10_500, le["amount_paise"])
    check("tier 2 units", 0, le["metadata"].get("tier_2_units", 0))


# --------------------------------------------------------------------------- #
def main() -> int:
    port = free_port()
    start_server(port)
    base = f"http://127.0.0.1:{port}"
    banner("CORE BILLING ENGINE -- LIVE DEMO\n"
           f"server: {base}   db: {os.environ['DATABASE_URL']}\n"
           "Every EXPECTED number below is quoted from docs/PRD.md.")
    with httpx.Client(base_url=base, timeout=30) as client:
        h = client.get("/health").json()
        print(f"   {DIM}health: {json.dumps(h)}{RESET}")
        killer_1(client)
        killer_2(client)
        killer_3(client)

        extra = ROOT / "scripts" / "demo_improvements.py"
        if extra.exists():
            from scripts.demo_improvements import run_improvements
            run_improvements(client, banner, step, note, call, check, set_now)

    banner("DEMO SUMMARY")
    if FAILURES:
        print(f"{RED}{BOLD}{len(FAILURES)} CHECK(S) FAILED:{RESET}")
        for f in FAILURES:
            print(f"  {RED}- {f}{RESET}")
        return 1
    print(f"{GREEN}{BOLD}ALL CHECKS PASSED -- every number matches docs/PRD.md "
          f"exactly.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
