"""KILLER TEST 2 -- Mid-month plan upgrade must prorate exactly.

Source of truth: PRD.md section 5 "Killer Test 2: Mid-Month Plan Upgrade"
and PRD.md section 6 case 3 (Day 1 / Day 30 upgrades).

Hand-worked numbers (30-day April cycle, upgrade on Day 15 = Apr 16 00:00 UTC):
    total_days      = 30
    remaining_days  = 15                 (Apr 16 -> May 1)
    coefficient     = 15 / 30 = 0.5000
    Starter credit  = -(300,000 x 0.5) = -150,000 paise  (-Rs 1,500.00)
    Pro charge      = +(900,000 x 0.5) = +450,000 paise  (+Rs 4,500.00)
    net amount due  =  300,000 paise  (Rs 3,000.00)
"""
TENANT_HEADER = {"X-Tenant-ID": "tenant_test"}


def _seed(client):
    """Starter Plan at Rs 3,000/month, Pro Plan at Rs 9,000/month, billed in advance."""
    for pid, name, key, paise in (
        ("plan_starter", "Starter Plan", "starter", 300_000),
        ("plan_pro", "Pro Plan", "pro", 900_000),
    ):
        r = client.post("/v1/plans", json={"id": pid, "name": name, "lookup_key": key},
                        headers=TENANT_HEADER)
        assert r.status_code == 201, r.text
        r = client.post(
            "/v1/prices",
            json={"id": f"price_{key}", "plan_id": pid, "type": "fixed",
                  "fixed_amount_paise": paise},
            headers=TENANT_HEADER,
        )
        assert r.status_code == 201, r.text

    r = client.post("/v1/customers",
                    json={"id": "cust_01", "external_id": "ext_01", "name": "Acme AI"},
                    headers=TENANT_HEADER)
    assert r.status_code == 201, r.text

    r = client.post(
        "/v1/subscriptions",
        json={"id": "sub_01", "customer_id": "cust_01", "plan_id": "plan_starter",
              "start_date": "2026-04-01T00:00:00Z"},
        headers=TENANT_HEADER,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_period_is_a_30_day_april_cycle(client, at_time):
    at_time("2026-04-01T00:00:00Z")
    sub = _seed(client)
    # Apr 1 00:00:00 UTC (inclusive) -> May 1 00:00:00 UTC (exclusive)
    assert sub["current_period_start"] == 1775001600   # 2026-04-01T00:00:00Z
    assert sub["current_period_end"] == 1777593600     # 2026-05-01T00:00:00Z
    assert (sub["current_period_end"] - sub["current_period_start"]) == 30 * 86400


def test_midmonth_upgrade_prorates_to_exactly_300000_paise(client, at_time):
    at_time("2026-04-01T00:00:00Z")
    _seed(client)

    # When: the customer upgrades on Day 15 (April 16, 00:00:00 UTC).
    at_time("2026-04-16T00:00:00Z")
    r = client.post(
        "/v1/subscriptions/sub_01/upgrade",
        json={"target_plan_id": "plan_pro", "proration_behavior": "create_prorations"},
        headers=TENANT_HEADER,
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["previous_plan_id"] == "plan_starter"
    assert body["target_plan_id"] == "plan_pro"
    assert body["effective_date"] == 1776297600          # 2026-04-16T00:00:00Z

    # Then 1 + 2: day counts and coefficient.
    pr = body["proration"]
    assert pr["total_days"] == 30
    assert pr["remaining_days"] == 15
    assert pr["coefficient"] == "0.5000"

    inv = body["settlement_invoice"]
    assert inv["invoice_type"] == "one_off"
    assert inv["status"] == "finalized"

    # Then 5: exactly two opposing line items, in order.
    lines = inv["line_items"]
    assert len(lines) == 2, lines
    assert lines[0]["description"] == "Unused time on Starter Plan (15 days)"
    assert lines[0]["amount_paise"] == -150_000           # Then 3
    assert lines[0]["quantity"] == 1
    assert lines[1]["description"] == "Remaining time on Pro Plan (15 days)"
    assert lines[1]["amount_paise"] == 450_000            # Then 4
    assert lines[1]["quantity"] == 1

    # Then 6: net amount due is exactly Rs 3,000.00 = 300,000 paise.
    assert inv["amount_due_paise"] == 300_000
    assert inv["subtotal_paise"] == 300_000
    assert inv["total_paise"] == 300_000

    # And the subscription actually moved to the Pro plan.
    r = client.get("/v1/subscriptions/sub_01", headers=TENANT_HEADER)
    assert r.json()["plan_id"] == "plan_pro"


def test_upgrade_on_day_1_charges_full_difference(client, at_time):
    """PRD.md section 6 case 3: Day 1 upgrade -> coefficient 1.0, net = full diff."""
    at_time("2026-04-01T00:00:00Z")
    _seed(client)

    r = client.post(
        "/v1/subscriptions/sub_01/upgrade",
        json={"target_plan_id": "plan_pro", "proration_behavior": "create_prorations"},
        headers=TENANT_HEADER,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["proration"]["remaining_days"] == 30
    assert body["proration"]["coefficient"] == "1.0000"
    lines = body["settlement_invoice"]["line_items"]
    assert lines[0]["amount_paise"] == -300_000
    assert lines[1]["amount_paise"] == 900_000
    assert body["settlement_invoice"]["amount_due_paise"] == 600_000   # full difference


def test_upgrade_on_last_day_prorates_to_20000_paise(client, at_time):
    """PRD.md section 6 case 3: Day 30 upgrade -> 1/30, credit -10,000, charge +30,000."""
    at_time("2026-04-01T00:00:00Z")
    _seed(client)

    at_time("2026-04-30T00:00:00Z")
    r = client.post(
        "/v1/subscriptions/sub_01/upgrade",
        json={"target_plan_id": "plan_pro", "proration_behavior": "create_prorations"},
        headers=TENANT_HEADER,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["proration"]["total_days"] == 30
    assert body["proration"]["remaining_days"] == 1
    lines = body["settlement_invoice"]["line_items"]
    assert lines[0]["amount_paise"] == -10_000
    assert lines[1]["amount_paise"] == 30_000
    assert body["settlement_invoice"]["amount_due_paise"] == 20_000   # Rs 200.00


def test_proration_behavior_none_skips_invoice(client, at_time):
    at_time("2026-04-01T00:00:00Z")
    _seed(client)
    at_time("2026-04-16T00:00:00Z")
    r = client.post(
        "/v1/subscriptions/sub_01/upgrade",
        json={"target_plan_id": "plan_pro", "proration_behavior": "none"},
        headers=TENANT_HEADER,
    )
    assert r.status_code == 200, r.text
    assert r.json()["settlement_invoice"] is None
    assert client.get("/v1/subscriptions/sub_01",
                      headers=TENANT_HEADER).json()["plan_id"] == "plan_pro"


def test_upgrade_to_same_plan_is_rejected(client, at_time):
    at_time("2026-04-01T00:00:00Z")
    _seed(client)
    r = client.post(
        "/v1/subscriptions/sub_01/upgrade",
        json={"target_plan_id": "plan_starter",
              "proration_behavior": "create_prorations"},
        headers=TENANT_HEADER,
    )
    assert r.status_code == 400, r.text
