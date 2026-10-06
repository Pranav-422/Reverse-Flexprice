"""IMPROVEMENT 1 (FIX) -- Deterministic dedup + sub-paise micro-token rating.

Spec: docs/GAPS.md section 2, "Improvement 1 (Fix)". Closes upstream gaps
1 (ReplacingMergeTree keys on timestamp), 11 (24h Redis dedup TTL) and
7 (INR hardcoded to 2 decimals, so sub-paise token rates round to 0).

GAPS.md acceptance criterion:
  Given price Rs 0.10 per 1,000 tokens (100,000 micro-paise per 1k units)
  When two concurrent requests submit evt_retry_10 with 50,000 tokens
       at 12:00:00 and 12:00:02
  Then exactly one row is stored, total tokens recorded = 50,000, and the
       billed amount equals exactly Rs 5.00 (500 paise), zero duplicate leakage.
"""
import threading

from app.core.money import micro_to_paise

TENANT_HEADER = {"X-Tenant-ID": "tenant_test"}


def _seed(client, divide_by=1000, unit_micro=100_000, flat=0, event_name="token_usage"):
    client.post("/v1/meters", json={
        "id": "meter_tokens", "name": "LLM Inference Tokens",
        "event_name": event_name, "aggregation_type": "SUM",
        "value_property": "total_tokens", "divide_by": divide_by,
        "round_type": "none"}, headers=TENANT_HEADER)
    client.post("/v1/plans", json={
        "id": "plan_ai", "name": "AI Plan", "lookup_key": "ai"},
        headers=TENANT_HEADER)
    client.post("/v1/prices", json={
        "id": "price_tokens", "plan_id": "plan_ai", "meter_id": "meter_tokens",
        "type": "usage", "tier_mode": "slab",
        "tiers": [{"tier_index": 0, "up_to_units": None,
                   "unit_amount_micro_paise": unit_micro,
                   "flat_amount_paise": flat}]}, headers=TENANT_HEADER)
    client.post("/v1/customers", json={
        "id": "cust_01", "external_id": "ext_01", "name": "Acme AI"},
        headers=TENANT_HEADER)
    r = client.post("/v1/subscriptions", json={
        "id": "sub_01", "customer_id": "cust_01", "plan_id": "plan_ai",
        "start_date": "2026-10-01T00:00:00Z"}, headers=TENANT_HEADER)
    assert r.status_code == 201, r.text


def _invoice(client, sub_id="sub_01"):
    r = client.post("/v1/invoices/generate", json={
        "subscription_id": sub_id, "period_start": "2026-10-01T00:00:00Z",
        "period_end": "2026-11-01T00:00:00Z"}, headers=TENANT_HEADER)
    assert r.status_code in (200, 201), r.text
    return r.json()


# --------------------------------------------------------------------------- #
# The GAPS.md acceptance criterion, verbatim.
# --------------------------------------------------------------------------- #
def test_gaps_acceptance_concurrent_retry_bills_exactly_500_paise(client, at_time):
    at_time("2026-10-05T12:00:10Z")
    _seed(client)

    payload = {"event_id": "evt_retry_10", "event_name": "token_usage",
               "customer_id": "cust_01", "properties": {"total_tokens": 50000}}

    statuses = []
    barrier = threading.Barrier(2)

    def worker(ts):
        def run():
            barrier.wait()
            r = client.post("/v1/events", json=dict(payload, timestamp=ts),
                            headers=TENANT_HEADER)
            statuses.append(r.json()["status"])
        return run

    # Two concurrent requests, 12:00:00 and 12:00:02.
    threads = [threading.Thread(target=worker("2026-10-05T12:00:00Z")),
               threading.Thread(target=worker("2026-10-05T12:00:02Z"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(statuses) == ["created", "duplicate_skipped"], statuses

    # Then: exactly one row is stored.
    r = client.get("/v1/events", params={"event_id": "evt_retry_10"},
                   headers=TENANT_HEADER)
    assert r.json()["count"] == 1

    # Then: total tokens recorded equals 50,000.
    u = client.get("/v1/meters/meter_tokens/usage",
                   params={"customer_id": "cust_01",
                           "period_start": "2026-10-01T00:00:00Z",
                           "period_end": "2026-11-01T00:00:00Z"},
                   headers=TENANT_HEADER).json()
    assert u["raw_units"] == 50000
    assert u["quantity"] == 50

    # Then: billed amount equals exactly Rs 5.00 (500 paise), zero leakage.
    inv = _invoice(client)
    line = next(li for li in inv["line_items"] if li["price_id"] == "price_tokens")
    assert line["amount_paise"] == 500
    assert inv["amount_due_paise"] == 500
    # 50 units x 100,000 micro-paise = 5,000,000 micro-paise, exact.
    assert line["metadata"]["amount_micro_paise"] == 5_000_000


# --------------------------------------------------------------------------- #
# GAPS gap 1: the retry arrives with a MUTATED timestamp.
# --------------------------------------------------------------------------- #
def test_retry_with_mutated_timestamp_still_dedupes(client, at_time):
    """Upstream keys ClickHouse rows on timestamp, so this leaked a 2nd row."""
    at_time("2026-10-05T12:00:30Z")
    _seed(client)
    base = {"event_id": "evt_mutate_1", "event_name": "token_usage",
            "customer_id": "cust_01", "properties": {"total_tokens": 50000}}

    assert client.post("/v1/events", json=dict(base, timestamp="2026-10-05T12:00:00Z"),
                       headers=TENANT_HEADER).status_code == 201
    for ts in ("2026-10-05T12:00:01Z", "2026-10-05T12:00:20Z", "2026-10-01T09:00:00Z"):
        r = client.post("/v1/events", json=dict(base, timestamp=ts),
                        headers=TENANT_HEADER)
        assert r.status_code == 200, (ts, r.text)
        assert r.json()["status"] == "duplicate_skipped"

    assert client.get("/v1/events", params={"event_id": "evt_mutate_1"},
                      headers=TENANT_HEADER).json()["count"] == 1
    assert _invoice(client)["amount_due_paise"] == 500


# --------------------------------------------------------------------------- #
# GAPS gap 11: replay long after any cache TTL would have expired.
# --------------------------------------------------------------------------- #
def test_replay_after_cache_ttl_window_still_dedupes(client, at_time):
    """A 24-hour Redis lock cannot stop this; a DB unique index can."""
    at_time("2026-10-02T00:00:00Z")
    _seed(client)
    payload = {"event_id": "evt_replay_7d", "event_name": "token_usage",
               "customer_id": "cust_01", "timestamp": "2026-10-02T00:00:00Z",
               "properties": {"total_tokens": 50000}}
    assert client.post("/v1/events", json=payload,
                       headers=TENANT_HEADER).status_code == 201

    # Seven days later a historical batch is re-imported.
    at_time("2026-10-09T00:00:00Z")
    r = client.post("/v1/events", json=payload, headers=TENANT_HEADER)
    assert r.status_code == 200
    assert r.json()["status"] == "duplicate_skipped"
    assert client.get("/v1/events", params={"event_id": "evt_replay_7d"},
                      headers=TENANT_HEADER).json()["count"] == 1


# --------------------------------------------------------------------------- #
# GAPS gap 7 / PRD section 6 case 4: sub-paise per-token rating.
# --------------------------------------------------------------------------- #
def test_sub_paise_per_token_rate_does_not_round_to_zero(client, at_time):
    """Rs 0.002 per token = 0.2 paise = 2,000 micro-paise, divide_by = 1.

    Upstream rounds each charge to 2 INR decimals, so 0.2 paise becomes 0 and
    the revenue vanishes. Micro-paise rating accumulates and rounds ONCE.
    """
    at_time("2026-10-05T12:00:00Z")
    _seed(client, divide_by=1, unit_micro=2_000)

    # Ten separate single-token requests: 10 x 0.2 paise = 2 paise exactly.
    for i in range(10):
        r = client.post("/v1/events", json={
            "event_id": f"evt_micro_{i}", "event_name": "token_usage",
            "customer_id": "cust_01", "timestamp": "2026-10-05T11:59:00Z",
            "properties": {"total_tokens": 1}}, headers=TENANT_HEADER)
        assert r.status_code == 201, r.text

    inv = _invoice(client)
    line = next(li for li in inv["line_items"] if li["price_id"] == "price_tokens")
    assert line["metadata"]["raw_units"] == 10
    assert line["metadata"]["amount_micro_paise"] == 20_000      # exact
    assert line["amount_paise"] == 2                             # NOT 0
    assert inv["amount_due_paise"] == 2


def test_sub_paise_rate_over_large_volume_is_exact(client, at_time):
    """1,234,567 tokens x 0.2 paise = 246,913.4 paise -> 246,913 (half-up)."""
    at_time("2026-10-05T12:00:00Z")
    _seed(client, divide_by=1, unit_micro=2_000)
    assert client.post("/v1/events", json={
        "event_id": "evt_bulk", "event_name": "token_usage",
        "customer_id": "cust_01", "timestamp": "2026-10-05T11:00:00Z",
        "properties": {"total_tokens": 1_234_567}},
        headers=TENANT_HEADER).status_code == 201

    inv = _invoice(client)
    line = next(li for li in inv["line_items"] if li["price_id"] == "price_tokens")
    assert line["metadata"]["amount_micro_paise"] == 1_234_567 * 2_000
    assert line["amount_paise"] == 246_913


def test_fractional_billable_units_round_only_at_the_line_item(client, at_time):
    """divide_by=1000 with a non-multiple raw count must not pre-round units.

    PRD.md section 6 case 4: "fractional paise accumulate unrounded; rounding
    to integer paise occurs only when the final invoice line item is computed."

    1,500,500 raw tokens = 1,500.5 billable units against the Killer Test 3
    tier table:
        tier 1: 1,000.0 units x 10 paise + Rs 5.00  = 10,500     paise
        tier 2:   500.5 units x  5 paise + Rs 10.00 =  3,502.5   paise
        exact total                                 = 14,002.5   paise
        half-up once                                = 14,003     paise
    Pre-rounding units to 1,501 would instead bill 14,005 paise.
    """
    at_time("2026-10-05T12:00:00Z")
    client.post("/v1/meters", json={
        "id": "meter_tokens", "name": "LLM Inference Tokens",
        "event_name": "token_usage", "aggregation_type": "SUM",
        "value_property": "total_tokens", "divide_by": 1000,
        "round_type": "none"}, headers=TENANT_HEADER)
    client.post("/v1/plans", json={"id": "plan_ai", "name": "AI Plan",
                                   "lookup_key": "ai"}, headers=TENANT_HEADER)
    client.post("/v1/prices", json={
        "id": "price_tokens", "plan_id": "plan_ai", "meter_id": "meter_tokens",
        "type": "usage", "tier_mode": "slab",
        "tiers": [{"tier_index": 0, "up_to_units": 1000,
                   "unit_amount_micro_paise": 100_000, "flat_amount_paise": 500},
                  {"tier_index": 1, "up_to_units": None,
                   "unit_amount_micro_paise": 50_000, "flat_amount_paise": 1_000}]},
        headers=TENANT_HEADER)
    client.post("/v1/customers", json={"id": "cust_01", "external_id": "ext_01",
                                       "name": "Acme AI"}, headers=TENANT_HEADER)
    client.post("/v1/subscriptions", json={
        "id": "sub_01", "customer_id": "cust_01", "plan_id": "plan_ai",
        "start_date": "2026-10-01T00:00:00Z"}, headers=TENANT_HEADER)

    assert client.post("/v1/events", json={
        "event_id": "evt_frac", "event_name": "token_usage",
        "customer_id": "cust_01", "timestamp": "2026-10-05T11:00:00Z",
        "properties": {"total_tokens": 1_500_500}},
        headers=TENANT_HEADER).status_code == 201

    inv = _invoice(client)
    line = next(li for li in inv["line_items"] if li["price_id"] == "price_tokens")
    assert line["metadata"]["raw_units"] == 1_500_500
    # Exact micro-paise total: 140,025,000 -> 14,002.5 paise -> 14,003 half-up.
    assert line["metadata"]["amount_micro_paise"] == 140_025_000
    assert line["amount_paise"] == 14_003
    assert line["amount_paise"] != 14_005        # the pre-rounded, wrong answer


# --------------------------------------------------------------------------- #
# The documented half-up rule itself.
# --------------------------------------------------------------------------- #
def test_half_up_rounding_rule_matches_data_model_formula():
    """DATA_MODEL.md section 1: floor((MicroPaise + 5,000) / 10,000)."""
    assert micro_to_paise(0) == 0
    assert micro_to_paise(4_999) == 0
    assert micro_to_paise(5_000) == 1         # exactly .5 rounds UP
    assert micro_to_paise(14_999) == 1
    assert micro_to_paise(15_000) == 2
    assert micro_to_paise(140_025_000) == 14_003
    # Credits round with the same magnitude, so no drift toward zero.
    assert micro_to_paise(-5_000) == -1
    assert micro_to_paise(-140_025_000) == -14_003


# --------------------------------------------------------------------------- #
# Late events aimed at an already-sealed billing period.
# --------------------------------------------------------------------------- #
def _seed_april(client):
    """A customer on a plain April cycle (Apr 1 -> May 1)."""
    client.post("/v1/meters", json={
        "id": "meter_tokens", "name": "LLM Inference Tokens",
        "event_name": "token_usage", "aggregation_type": "SUM",
        "value_property": "total_tokens", "divide_by": 1000,
        "round_type": "none"}, headers=TENANT_HEADER)
    client.post("/v1/plans", json={"id": "plan_ai", "name": "AI Plan",
                                   "lookup_key": "ai"}, headers=TENANT_HEADER)
    client.post("/v1/prices", json={
        "id": "price_tokens", "plan_id": "plan_ai", "meter_id": "meter_tokens",
        "type": "usage", "tier_mode": "slab",
        "tiers": [{"tier_index": 0, "up_to_units": None,
                   "unit_amount_micro_paise": 100_000, "flat_amount_paise": 0}]},
        headers=TENANT_HEADER)
    client.post("/v1/customers", json={
        "id": "cust_01", "external_id": "ext_01", "name": "Acme AI"},
        headers=TENANT_HEADER)
    r = client.post("/v1/subscriptions", json={
        "id": "sub_01", "customer_id": "cust_01", "plan_id": "plan_ai",
        "start_date": "2026-04-01T00:00:00Z"}, headers=TENANT_HEADER)
    assert r.status_code == 201, r.text


def test_late_event_into_finalized_period_is_rejected(client, at_time):
    """A sealed invoice can never be re-billed, so the usage would vanish.

    docs/GAPS.md gap 3: "Events dated months in the past land in sealed,
    immutable invoice periods and are never billed." Ingestion previously
    returned 201 and the meter counted the event, but the finalized invoice is
    idempotent and never changed -- revenue silently lost with no error.
    """
    at_time("2026-04-10T00:00:00Z")
    _seed_april(client)

    # Usage during the open period is billed normally.
    assert client.post("/v1/events", json={
        "event_id": "evt_in_period", "event_name": "token_usage",
        "customer_id": "cust_01", "timestamp": "2026-04-09T12:00:00Z",
        "properties": {"total_tokens": 50_000}},
        headers=TENANT_HEADER).status_code == 201

    # Seal April.
    at_time("2026-05-02T00:00:00Z")
    r = client.post("/v1/invoices/generate", json={
        "subscription_id": "sub_01", "period_start": "2026-04-01T00:00:00Z",
        "period_end": "2026-05-01T00:00:00Z"}, headers=TENANT_HEADER)
    assert r.status_code in (200, 201), r.text
    invoice = r.json()
    assert invoice["status"] == "finalized"
    sealed_total = invoice["amount_due_paise"]
    assert sealed_total == 500          # 50 units x 100,000 micro-paise

    # A late April-dated event now lands in a sealed period -> 409.
    late = client.post("/v1/events", json={
        "event_id": "evt_late_april", "event_name": "token_usage",
        "customer_id": "cust_01", "timestamp": "2026-04-20T10:00:00Z",
        "properties": {"total_tokens": 50_000}}, headers=TENANT_HEADER)
    assert late.status_code == 409, late.text
    detail = late.json()["detail"]
    assert "2026-04-20T10:00:00Z" in detail
    assert "finalized billing period" in detail
    assert "2026-04-01T00:00:00Z..2026-05-01T00:00:00Z" in detail

    # It was refused at the door: nothing stored, meter unchanged.
    assert client.get("/v1/events", params={"event_id": "evt_late_april"},
                      headers=TENANT_HEADER).json()["count"] == 0
    usage = client.get("/v1/meters/meter_tokens/usage",
                       params={"customer_id": "cust_01",
                               "period_start": "2026-04-01T00:00:00Z",
                               "period_end": "2026-05-01T00:00:00Z"},
                       headers=TENANT_HEADER).json()
    assert usage["raw_units"] == 50_000

    # The sealed invoice is untouched.
    assert client.get(f"/v1/invoices/{invoice['id']}",
                      headers=TENANT_HEADER).json()["amount_due_paise"] == sealed_total

    # A May-dated event belongs to the still-open period -> accepted.
    nxt = client.post("/v1/events", json={
        "event_id": "evt_may", "event_name": "token_usage",
        "customer_id": "cust_01", "timestamp": "2026-05-01T10:00:00Z",
        "properties": {"total_tokens": 50_000}}, headers=TENANT_HEADER)
    assert nxt.status_code == 201, nxt.text


def test_duplicate_into_sealed_period_still_returns_200(client, at_time):
    """The sealed-period gate must not change deduplication behaviour."""
    at_time("2026-04-10T00:00:00Z")
    _seed_april(client)
    payload = {"event_id": "evt_dup_sealed", "event_name": "token_usage",
               "customer_id": "cust_01", "timestamp": "2026-04-09T12:00:00Z",
               "properties": {"total_tokens": 50_000}}
    assert client.post("/v1/events", json=payload,
                       headers=TENANT_HEADER).status_code == 201

    at_time("2026-05-02T00:00:00Z")
    client.post("/v1/invoices/generate", json={
        "subscription_id": "sub_01", "period_start": "2026-04-01T00:00:00Z",
        "period_end": "2026-05-01T00:00:00Z"}, headers=TENANT_HEADER)

    # Replaying the SAME event_id is idempotent -- it records nothing new, so it
    # is still a 200, not a 409.
    again = client.post("/v1/events", json=payload, headers=TENANT_HEADER)
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "duplicate_skipped"
    assert client.get("/v1/events", params={"event_id": "evt_dup_sealed"},
                      headers=TENANT_HEADER).json()["count"] == 1


# --------------------------------------------------------------------------- #
# Fractional token counts.
# --------------------------------------------------------------------------- #
def test_fractional_token_count_is_rejected(client, at_time):
    """int(1.7) would silently store 1 -- the same silent coercion as gap 4."""
    at_time("2026-10-05T12:00:00Z")
    _seed(client)

    bad = client.post("/v1/events", json={
        "event_id": "evt_frac_bad", "event_name": "token_usage",
        "customer_id": "cust_01", "timestamp": "2026-10-05T11:00:00Z",
        "properties": {"total_tokens": 1.7}}, headers=TENANT_HEADER)
    assert bad.status_code == 400, bad.text
    assert "whole number" in bad.json()["detail"]
    assert client.get("/v1/events", params={"event_id": "evt_frac_bad"},
                      headers=TENANT_HEADER).json()["count"] == 0

    # An integral float is a legitimate JSON number for a whole count.
    ok = client.post("/v1/events", json={
        "event_id": "evt_frac_ok", "event_name": "token_usage",
        "customer_id": "cust_01", "timestamp": "2026-10-05T11:00:00Z",
        "properties": {"total_tokens": 2.0}}, headers=TENANT_HEADER)
    assert ok.status_code == 201, ok.text
    assert ok.json()["recorded_quantity"] == 2

    # The existing negative and non-numeric rejections still hold.
    neg = client.post("/v1/events", json={
        "event_id": "evt_neg", "event_name": "token_usage",
        "customer_id": "cust_01", "timestamp": "2026-10-05T11:00:00Z",
        "properties": {"total_tokens": -5}}, headers=TENANT_HEADER)
    assert neg.status_code == 400
    assert "negative" in neg.json()["detail"]
