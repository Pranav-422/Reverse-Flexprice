"""KILLER TEST 3 -- Tiered pricing must match the hand calculation exactly.

Source of truth: PRD.md section 5 "Killer Test 3: Tiered Pricing Calculation"
and PRD.md section 6 case 1 (exact tier boundary).

Tier table (divide_by = 1000):
    Tier 1: up_to 1,000 units, 100,000 micro-paise/unit (Rs 0.10), flat    500 paise (Rs 5.00)
    Tier 2: up_to  null,        50,000 micro-paise/unit (Rs 0.05), flat  1,000 paise (Rs 10.00)

Hand-worked for 1,500,000 raw tokens = 1,500 billable units:

  CASE A -- SLAB (graduated)
    tier 1 slice: 1,000 x Rs 0.10 + Rs 5.00  = Rs 100.00 + Rs  5.00 = Rs 105.00 = 10,500 paise
    tier 2 slice:   500 x Rs 0.05 + Rs 10.00 = Rs  25.00 + Rs 10.00 = Rs  35.00 =  3,500 paise
    TOTAL                                                             Rs 140.00 = 14,000 paise

  CASE B -- VOLUME (single bracket)
    1,500 > 1,000 so everything lands in tier 2:
    1,500 x Rs 0.05 + Rs 10.00 = Rs 75.00 + Rs 10.00 = Rs 85.00 = 8,500 paise
"""
TENANT_HEADER = {"X-Tenant-ID": "tenant_test"}

TIERS = [
    {"tier_index": 0, "up_to_units": 1000,
     "unit_amount_micro_paise": 100_000, "flat_amount_paise": 500},
    {"tier_index": 1, "up_to_units": None,
     "unit_amount_micro_paise": 50_000, "flat_amount_paise": 1_000},
]


def _seed(client, tier_mode, plan_suffix):
    client.post(
        "/v1/meters",
        json={"id": "meter_tokens", "name": "LLM Inference Tokens",
              "event_name": "token_usage", "aggregation_type": "SUM",
              "value_property": "total_tokens", "divide_by": 1000,
              "round_type": "none"},
        headers=TENANT_HEADER,
    )
    client.post("/v1/customers",
                json={"id": "cust_01", "external_id": "ext_01", "name": "Acme AI"},
                headers=TENANT_HEADER)

    plan_id = f"plan_{plan_suffix}"
    r = client.post("/v1/plans",
                    json={"id": plan_id, "name": f"{tier_mode.title()} Plan",
                          "lookup_key": plan_suffix},
                    headers=TENANT_HEADER)
    assert r.status_code == 201, r.text
    r = client.post(
        "/v1/prices",
        json={"id": f"price_{plan_suffix}", "plan_id": plan_id,
              "meter_id": "meter_tokens", "type": "usage",
              "tier_mode": tier_mode, "tiers": TIERS,
              "display_name": "LLM Inference Tokens"},
        headers=TENANT_HEADER,
    )
    assert r.status_code == 201, r.text

    sub_id = f"sub_{plan_suffix}"
    r = client.post("/v1/subscriptions",
                    json={"id": sub_id, "customer_id": "cust_01",
                          "plan_id": plan_id, "start_date": "2026-10-01T00:00:00Z"},
                    headers=TENANT_HEADER)
    assert r.status_code == 201, r.text
    return sub_id, f"price_{plan_suffix}"


def _ingest(client, raw_tokens, event_id="evt_tier_001"):
    r = client.post(
        "/v1/events",
        json={"event_id": event_id, "event_name": "token_usage",
              "customer_id": "cust_01", "timestamp": "2026-10-05T12:00:00Z",
              "properties": {"total_tokens": raw_tokens}},
        headers=TENANT_HEADER,
    )
    assert r.status_code == 201, r.text


def _invoice(client, sub_id):
    r = client.post(
        "/v1/invoices/generate",
        json={"subscription_id": sub_id,
              "period_start": "2026-10-01T00:00:00Z",
              "period_end": "2026-11-01T00:00:00Z"},
        headers=TENANT_HEADER,
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_case_a_slab_mode_totals_14000_paise(client, at_time):
    at_time("2026-10-06T00:00:00Z")
    sub_id, price_id = _seed(client, "slab", "slab")
    _ingest(client, 1_500_000)

    inv = _invoice(client, sub_id)
    line = next(li for li in inv["line_items"] if li["price_id"] == price_id)

    assert line["quantity"] == 1500
    assert line["amount_paise"] == 14_000          # Rs 140.00, exactly
    assert inv["subtotal_paise"] == 14_000
    assert inv["total_paise"] == 14_000
    assert inv["amount_due_paise"] == 14_000

    # Per-tier audit trail (PRD.md section 7 scenario 3).
    meta = line["metadata"]
    assert meta["tier_1_units"] == 1000
    assert meta["tier_1_cost_paise"] == 10_500
    assert meta["tier_2_units"] == 500
    assert meta["tier_2_cost_paise"] == 3_500
    assert meta["tier_1_cost_paise"] + meta["tier_2_cost_paise"] == 14_000


def test_case_b_volume_mode_totals_8500_paise(client, at_time):
    at_time("2026-10-06T00:00:00Z")
    sub_id, price_id = _seed(client, "volume", "volume")
    _ingest(client, 1_500_000)

    inv = _invoice(client, sub_id)
    line = next(li for li in inv["line_items"] if li["price_id"] == price_id)

    assert line["quantity"] == 1500
    assert line["amount_paise"] == 8_500           # Rs 85.00, exactly
    assert inv["amount_due_paise"] == 8_500
    # Volume mode assigns the whole consumption to the single matching bracket.
    assert line["metadata"]["matched_tier_index"] == 1
    assert line["metadata"]["tier_2_units"] == 1500


def test_exact_tier_boundary_stays_in_tier_1(client, at_time):
    """PRD.md section 6 case 1: up_to is INCLUSIVE (quantity <= up_to)."""
    at_time("2026-10-06T00:00:00Z")
    sub_id, price_id = _seed(client, "slab", "slab")
    _ingest(client, 1_000_000)      # exactly 1,000 units

    inv = _invoice(client, sub_id)
    line = next(li for li in inv["line_items"] if li["price_id"] == price_id)

    assert line["quantity"] == 1000
    # (1,000 x 10 paise) + 500 flat = 10,500 paise. Tier 2 must NOT trigger.
    assert line["amount_paise"] == 10_500
    assert line["metadata"]["tier_1_units"] == 1000
    assert line["metadata"].get("tier_2_units", 0) == 0


def test_volume_mode_at_boundary_uses_tier_1(client, at_time):
    at_time("2026-10-06T00:00:00Z")
    sub_id, price_id = _seed(client, "volume", "volume")
    _ingest(client, 1_000_000)

    inv = _invoice(client, sub_id)
    line = next(li for li in inv["line_items"] if li["price_id"] == price_id)
    assert line["metadata"]["matched_tier_index"] == 0
    assert line["amount_paise"] == 10_500


def test_usage_split_across_many_events_aggregates_then_tiers(client, at_time):
    """Tiering happens on the AGGREGATE, not per event."""
    at_time("2026-10-06T00:00:00Z")
    sub_id, price_id = _seed(client, "slab", "slab")
    for i in range(15):
        _ingest(client, 100_000, event_id=f"evt_tier_{i:03d}")   # 15 x 100k = 1.5M

    inv = _invoice(client, sub_id)
    line = next(li for li in inv["line_items"] if li["price_id"] == price_id)
    assert line["quantity"] == 1500
    assert line["amount_paise"] == 14_000


def test_unit_amount_paise_is_half_up_rounded(client, at_time):
    """API.md section 6 shows unit_amount_paise 9 for 1,500 units / 14,000 paise."""
    at_time("2026-10-06T00:00:00Z")
    sub_id, price_id = _seed(client, "slab", "slab")
    _ingest(client, 1_500_000)
    inv = _invoice(client, sub_id)
    line = next(li for li in inv["line_items"] if li["price_id"] == price_id)
    # 140,000,000 micro-paise / 1,500 units = 93,333.33 micro-paise -> 9 paise
    assert line["unit_amount_paise"] == 9
