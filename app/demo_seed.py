"""Demo tenant used by scripts/ui (local) and api/index.py (Vercel).

The numbers are the ones scripts/demo.py and the tests check:
  * c_acme     -- 1,500,000 tokens on slab pricing + a Day-15 upgrade
                  netting Rs 30.00, sealed into the April invoice (Rs 260.00)
  * c_live     -- on Basic since 1 Apr, untouched: upgrade it, send it events
                  and invoice it live from the dashboard
  * c_runaway  -- 1,000 tokens/hour for 7 days, then a 10,000-token hour
  * c_steady   -- the same 7-day baseline with a normal last hour

Seeding goes through the public HTTP API (via TestClient), moving BILLING_NOW
as it goes, and finishes with the clock frozen at NOW: just after April
closed and just after the runaway burst.
"""
import os
import warnings

from app.core import time as btime
from app.db import database

NOW = "2026-05-01T00:30:00Z"
TIERS = [{"tier_index": 0, "up_to_units": 1000,
          "unit_amount_micro_paise": 100_000, "flat_amount_paise": 500},
         {"tier_index": 1, "up_to_units": None,
          "unit_amount_micro_paise": 50_000, "flat_amount_paise": 1_000}]


def _ok(resp):
    if resp.status_code >= 400:
        raise RuntimeError(f"seed failed: {resp.request.method} {resp.request.url} "
                           f"-> {resp.status_code} {resp.text}")
    return resp.json()


def seed(c) -> str:
    """Populate an empty database; returns the sealed April invoice id."""
    os.environ["BILLING_NOW"] = "2026-04-01T00:00:00Z"
    _ok(c.post("/v1/meters", json={
        "id": "m_tokens", "name": "LLM Inference Tokens", "event_name": "llm_tokens",
        "aggregation_type": "SUM", "value_property": "total_tokens",
        "divide_by": 1000, "round_type": "none"}))
    for cid, name in (("c_acme", "Acme AI"), ("c_live", "Live Demo Co"),
                      ("c_runaway", "Runaway Co"), ("c_steady", "Steady Labs")):
        _ok(c.post("/v1/customers", json={"id": cid, "external_id": f"ext_{cid}",
                                          "name": name}))
    for pid, name, key, paise in (("pl_basic", "Basic Plan", "basic", 6_000),
                                  ("pl_growth", "Growth Plan", "growth", 12_000)):
        _ok(c.post("/v1/plans", json={"id": pid, "name": name, "lookup_key": key}))
        _ok(c.post("/v1/prices", json={"id": f"prc_{key}_fixed", "plan_id": pid,
                                       "type": "fixed", "fixed_amount_paise": paise}))
        _ok(c.post("/v1/prices", json={
            "id": f"prc_{key}_tokens", "plan_id": pid, "meter_id": "m_tokens",
            "type": "usage", "tier_mode": "slab", "tiers": TIERS,
            "display_name": "LLM Inference Tokens"}))
    for sid, cid in (("s_acme", "c_acme"), ("s_live", "c_live")):
        _ok(c.post("/v1/subscriptions", json={
            "id": sid, "customer_id": cid, "plan_id": "pl_basic",
            "start_date": "2026-04-01T00:00:00Z"}))

    os.environ["BILLING_NOW"] = "2026-04-10T00:00:00Z"
    _ok(c.post("/v1/events", json={
        "event_id": "evt_acme_1", "event_name": "llm_tokens", "customer_id": "c_acme",
        "timestamp": "2026-04-09T12:00:00Z", "properties": {"total_tokens": 1_500_000}}))
    os.environ["BILLING_NOW"] = "2026-04-16T00:00:00Z"
    _ok(c.post("/v1/subscriptions/s_acme/upgrade", json={
        "target_plan_id": "pl_growth", "proration_behavior": "create_prorations"}))
    os.environ["BILLING_NOW"] = "2026-05-01T00:00:00Z"
    inv = _ok(c.post("/v1/invoices/generate", json={
        "subscription_id": "s_acme", "period_start": "2026-04-01T00:00:00Z",
        "period_end": "2026-05-01T00:00:00Z"}))

    # Two customers with an identical 7-day baseline of 1,000 tokens/hour
    # (24 Apr 00:00 .. 30 Apr 23:00), then the last hour before NOW: a runaway
    # loop vs a normal hour.
    os.environ["BILLING_NOW"] = NOW
    t0 = btime.parse_ts("2026-04-24T00:00:00Z")
    for cid in ("c_runaway", "c_steady"):
        for hh in range(168):
            _ok(c.post("/v1/events", json={
                "event_id": f"evt_{cid}_{hh:03d}", "event_name": "llm_tokens",
                "customer_id": cid, "timestamp": t0 + hh * 3600,
                "properties": {"total_tokens": 1_000}}))
    _ok(c.post("/v1/events", json={
        "event_id": "evt_runaway_loop", "event_name": "llm_tokens",
        "customer_id": "c_runaway", "timestamp": "2026-05-01T00:05:00Z",
        "properties": {"total_tokens": 10_000}}))
    _ok(c.post("/v1/events", json={
        "event_id": "evt_steady_hour", "event_name": "llm_tokens",
        "customer_id": "c_steady", "timestamp": "2026-05-01T00:05:00Z",
        "properties": {"total_tokens": 1_200}}))
    return inv["id"]


def ensure_seeded(app) -> None:
    """Seed once per database file, then freeze the clock at NOW."""
    database.init_db()
    if database.fetch_one("SELECT 1 FROM customers LIMIT 1") is None:
        warnings.filterwarnings("ignore")  # starlette testclient deprecation noise
        from fastapi.testclient import TestClient

        # No `with`: entering the context would re-run the app lifespan, which
        # is where this function is called from on Vercel.
        seed(TestClient(app))
    os.environ["BILLING_NOW"] = NOW
