"""KILLER TEST 1 -- Duplicate usage ingestion must count exactly once.

Source of truth: PRD.md section 5 "Killer Test 1: Duplicate Usage Ingestion"
and PRD.md section 6 case 2 "Concurrent Duplicate Submissions".

Hand-worked numbers:
    50,000 raw tokens / divide_by 1,000 = 50 billable units.
    A duplicate must NEVER push that to 100,000 tokens / 100 units.
"""
import threading

import pytest

TENANT_HEADER = {"X-Tenant-ID": "tenant_test"}


def _seed(client):
    """Given: an active subscription + a meter tracking llm_tokens (divide_by=1000)."""
    r = client.post(
        "/v1/meters",
        json={
            "id": "meter_tokens",
            "name": "LLM Inference Tokens",
            "event_name": "token_usage",
            "aggregation_type": "SUM",
            "value_property": "total_tokens",
            "divide_by": 1000,
            "round_type": "none",
        },
        headers=TENANT_HEADER,
    )
    assert r.status_code == 201, r.text

    r = client.post(
        "/v1/plans",
        json={"id": "plan_dev", "name": "Developer Plan", "lookup_key": "developer"},
        headers=TENANT_HEADER,
    )
    assert r.status_code == 201, r.text

    # Flat 10 paise per unit so the invoice line quantity is unambiguous.
    r = client.post(
        "/v1/prices",
        json={
            "id": "price_tokens",
            "plan_id": "plan_dev",
            "meter_id": "meter_tokens",
            "type": "usage",
            "tier_mode": "slab",
            "tiers": [
                {"tier_index": 0, "up_to_units": None,
                 "unit_amount_micro_paise": 100_000, "flat_amount_paise": 0},
            ],
        },
        headers=TENANT_HEADER,
    )
    assert r.status_code == 201, r.text

    r = client.post(
        "/v1/customers",
        json={"id": "cust_ai_01", "external_id": "ai_startup_01", "name": "Acme AI"},
        headers=TENANT_HEADER,
    )
    assert r.status_code == 201, r.text

    r = client.post(
        "/v1/subscriptions",
        json={"id": "sub_01", "customer_id": "cust_ai_01", "plan_id": "plan_dev",
              "start_date": "2026-10-01T00:00:00Z"},
        headers=TENANT_HEADER,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_duplicate_event_counted_once(client, at_time):
    at_time("2026-10-01T10:00:10Z")
    _seed(client)

    payload = {
        "event_id": "evt_dedup_001",
        "event_name": "token_usage",
        "customer_id": "cust_ai_01",
        "timestamp": "2026-10-01T10:00:00Z",
        "properties": {"total_tokens": 50000, "model": "gpt-4o"},
    }

    # Then 1: first request returns 201 Created and persists the event.
    r1 = client.post("/v1/events", json=payload, headers=TENANT_HEADER)
    assert r1.status_code == 201, r1.text
    assert r1.json()["status"] == "created"
    assert r1.json()["recorded_quantity"] == 50000

    # When: identical event_id arrives 5 seconds later.
    dup = dict(payload, timestamp="2026-10-01T10:00:05Z")
    r2 = client.post("/v1/events", json=dup, headers=TENANT_HEADER)

    # Then 2: idempotent duplicate acknowledged (200 OK) or 409 Conflict.
    assert r2.status_code in (200, 409), r2.text
    assert r2.json()["status"] == "duplicate_skipped"

    # Then 3: aggregated meter usage is exactly 50,000 tokens / 50 billable units.
    r3 = client.get(
        "/v1/meters/meter_tokens/usage",
        params={"customer_id": "cust_ai_01",
                "period_start": "2026-10-01T00:00:00Z",
                "period_end": "2026-11-01T00:00:00Z"},
        headers=TENANT_HEADER,
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["raw_units"] == 50000
    assert r3.json()["quantity"] == 50

    # Then 4: invoice line shows quantity 50 units, never 100.
    r4 = client.post(
        "/v1/invoices/generate",
        json={"subscription_id": "sub_01",
              "period_start": "2026-10-01T00:00:00Z",
              "period_end": "2026-11-01T00:00:00Z"},
        headers=TENANT_HEADER,
    )
    assert r4.status_code in (200, 201), r4.text
    inv = r4.json()
    usage_lines = [li for li in inv["line_items"] if li["price_id"] == "price_tokens"]
    assert len(usage_lines) == 1
    assert usage_lines[0]["quantity"] == 50
    assert usage_lines[0]["quantity"] != 100
    # 50 units x 100,000 micro-paise = 5,000,000 micro-paise = 500 paise = Rs 5.00
    assert usage_lines[0]["amount_paise"] == 500


def test_only_one_row_persisted_for_duplicate(client, at_time):
    """The guarantee is at the storage layer: UNIQUE(tenant_id, event_id)."""
    at_time("2026-10-01T10:00:10Z")
    _seed(client)
    payload = {
        "event_id": "evt_dedup_001",
        "event_name": "token_usage",
        "customer_id": "cust_ai_01",
        "timestamp": "2026-10-01T10:00:00Z",
        "properties": {"total_tokens": 50000},
    }
    for _ in range(5):
        client.post("/v1/events", json=payload, headers=TENANT_HEADER)

    r = client.get("/v1/events", params={"event_id": "evt_dedup_001"},
                   headers=TENANT_HEADER)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1


def test_db_unique_constraint_exists(client):
    """Explicitly assert the DB-level index the whole design rests on."""
    from app.db.database import get_connection

    conn = get_connection()
    rows = conn.execute("PRAGMA index_list('usage_events')").fetchall()
    unique_cols = []
    for row in rows:
        name, is_unique = row["name"], row["unique"]
        if not is_unique:
            continue
        cols = [c["name"] for c in
                conn.execute(f"PRAGMA index_info('{name}')").fetchall()]
        unique_cols.append(sorted(cols))
    assert ["event_id", "tenant_id"] in unique_cols, unique_cols


def test_concurrent_duplicate_submissions(client, at_time):
    """PRD.md section 6 case 2: two concurrent workers post event_id evt_race_99.

    Then: the unique constraint permits exactly 1 write, the loser fails
    gracefully, and total recorded usage is 1 event.
    """
    at_time("2026-10-01T10:00:10Z")
    _seed(client)

    payload = {
        "event_id": "evt_race_99",
        "event_name": "token_usage",
        "customer_id": "cust_ai_01",
        "timestamp": "2026-10-01T10:00:00Z",
        "properties": {"total_tokens": 50000},
    }

    results = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()  # maximise the overlap window
        r = client.post("/v1/events", json=payload, headers=TENANT_HEADER)
        results.append((r.status_code, r.json().get("status")))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    created = [r for r in results if r[1] == "created"]
    skipped = [r for r in results if r[1] == "duplicate_skipped"]
    assert len(created) == 1, results          # exactly one writer wins
    assert len(skipped) == 7, results          # the rest degrade gracefully
    assert all(code in (200, 201, 409) for code, _ in results), results

    r = client.get("/v1/events", params={"event_id": "evt_race_99"},
                   headers=TENANT_HEADER)
    assert r.json()["count"] == 1

    r = client.get(
        "/v1/meters/meter_tokens/usage",
        params={"customer_id": "cust_ai_01",
                "period_start": "2026-10-01T00:00:00Z",
                "period_end": "2026-11-01T00:00:00Z"},
        headers=TENANT_HEADER,
    )
    assert r.json()["raw_units"] == 50000
    assert r.json()["quantity"] == 50
