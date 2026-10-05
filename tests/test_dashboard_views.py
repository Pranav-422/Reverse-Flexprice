"""Read-only views behind the /app dashboard.

They must report exactly what the billing engine bills: the pricing preview
reuses the invoice rating path and the upgrade preview reuses the proration
module, so these assert the same PRD.md section 5 numbers as the Killer Tests
-- and that a preview never writes anything.
"""
TENANT_HEADER = {"X-Tenant-ID": "tenant_test"}

TIERS = [
    {"tier_index": 0, "up_to_units": 1000,
     "unit_amount_micro_paise": 100_000, "flat_amount_paise": 500},
    {"tier_index": 1, "up_to_units": None,
     "unit_amount_micro_paise": 50_000, "flat_amount_paise": 1_000},
]


def _post(client, path, body):
    r = client.post(path, json=body, headers=TENANT_HEADER)
    assert r.status_code < 300, r.text
    return r.json()


def _get(client, path):
    r = client.get(path, headers=TENANT_HEADER)
    assert r.status_code == 200, r.text
    return r.json()


def _seed(client, at_time):
    at_time("2026-04-01T00:00:00Z")
    _post(client, "/v1/meters", {
        "id": "meter_tokens", "name": "LLM Inference Tokens", "event_name": "token_usage",
        "aggregation_type": "SUM", "value_property": "total_tokens",
        "divide_by": 1000, "round_type": "none"})
    _post(client, "/v1/customers", {"id": "cust_01", "external_id": "ext_01",
                                    "name": "Acme AI"})
    for pid, paise in (("plan_basic", 6_000), ("plan_growth", 12_000)):
        _post(client, "/v1/plans", {"id": pid, "name": pid, "lookup_key": pid})
        _post(client, "/v1/prices", {"id": f"{pid}_fixed", "plan_id": pid,
                                     "type": "fixed", "fixed_amount_paise": paise})
        _post(client, "/v1/prices", {"id": f"{pid}_tokens", "plan_id": pid,
                                     "meter_id": "meter_tokens", "type": "usage",
                                     "tier_mode": "slab", "tiers": TIERS})
    _post(client, "/v1/subscriptions", {"id": "sub_01", "customer_id": "cust_01",
                                        "plan_id": "plan_basic",
                                        "start_date": "2026-04-01T00:00:00Z"})


def test_price_preview_matches_killer_test_3(client, at_time):
    _seed(client, at_time)
    body = _get(client, "/v1/prices/plan_basic_tokens/preview?raw_units=1500000")
    assert body["slab"]["billable_units"] == 1500
    assert body["slab"]["amount_paise"] == 14_000
    assert [t["cost_paise"] for t in body["slab"]["tiers"]] == [10_500, 3_500]
    assert body["volume"]["amount_paise"] == 8_500
    # Inclusive boundary: exactly 1,000 units stays in tier 1 under volume.
    edge = _get(client, "/v1/prices/plan_basic_tokens/preview?raw_units=1000000")
    assert edge["volume"]["tiers"][0]["tier_index"] == 0


def test_upgrade_preview_matches_killer_test_2_and_writes_nothing(client, at_time):
    _seed(client, at_time)
    p = _get(client, "/v1/subscriptions/sub_01/upgrade-preview"
                     "?target_plan_id=plan_growth&effective_date=2026-04-16T00:00:00Z")
    assert (p["remaining_days"], p["total_days"], p["coefficient"]) == (15, 30, "0.5000")
    assert (p["credit_paise"], p["charge_paise"], p["net_paise"]) == (-3_000, 6_000, 3_000)
    assert _get(client, "/v1/invoices")["invoices"] == []
    assert _get(client, "/v1/subscriptions/sub_01")["plan_id"] == "plan_basic"


def test_lists_and_overview_reflect_stored_rows(client, at_time):
    _seed(client, at_time)
    at_time("2026-04-10T00:00:00Z")
    event = {"event_id": "evt_1", "event_name": "token_usage", "customer_id": "cust_01",
             "timestamp": "2026-04-09T12:00:00Z", "properties": {"total_tokens": 50_000}}
    _post(client, "/v1/events", event)
    _post(client, "/v1/events", event)  # duplicate: 200, not stored

    cust = _get(client, "/v1/customers")["customers"]
    assert [(c["id"], c["plan_id"]) for c in cust] == [("cust_01", "plan_basic")]
    recent = _get(client, "/v1/usage/recent")
    assert recent["total_rows"] == 1 and recent["events"][0]["quantity"] == 50_000

    at_time("2026-05-01T00:00:00Z")
    _post(client, "/v1/invoices/generate", {"subscription_id": "sub_01"})
    invs = _get(client, "/v1/invoices")["invoices"]
    assert len(invs) == 1 and invs[0]["customer_name"] == "Acme AI"

    o = _get(client, "/v1/overview")
    assert o["event_rows"] == o["distinct_event_ids"] == 1
    assert o["billed_paise"] == invs[0]["amount_due_paise"]
    assert o["active_spikes"] == []


def test_dashboard_page_is_served(client):
    r = client.get("/app")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "/v1/overview" in r.text
