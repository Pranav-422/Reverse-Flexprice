"""IMPROVEMENT 2 (DIFFERENTIATOR) -- Invoice explainer + runaway-spike alert.

Spec: docs/GAPS.md section 2, "Improvement 2 (Differentiator)". Closes upstream
gaps 10 (invoices carry no explanation of how tokens were packaged/tiered/
prorated) and 8 (no velocity monitoring anywhere in the codebase).

Endpoints:
    GET /v1/invoices/{id}/explanation
    GET /v1/customers/{id}/spike-status

The AI path is OPTIONAL. With AI_PROVIDER_API_KEY empty -- or the SDK missing,
or the API erroring -- the engine must still answer 200 OK from the
deterministic template. These tests pin the fallback path.
"""
TENANT_HEADER = {"X-Tenant-ID": "tenant_test"}

TIERS = [
    {"tier_index": 0, "up_to_units": 1000,
     "unit_amount_micro_paise": 100_000, "flat_amount_paise": 500},
    {"tier_index": 1, "up_to_units": None,
     "unit_amount_micro_paise": 50_000, "flat_amount_paise": 1_000},
]


def _seed_acceptance_scenario(client, at_time):
    """GAPS.md acceptance criterion setup.

    1,500,000 tokens under Slab pricing, plus a mid-month upgrade whose
    proration nets exactly Rs 30.00 (3,000 paise):
        Basic  Rs  60.00/month =  6,000 paise -> credit  -3,000 paise (15/30)
        Growth Rs 120.00/month = 12,000 paise -> charge  +6,000 paise (15/30)
        net                                              +3,000 paise = Rs 30.00
    """
    at_time("2026-04-01T00:00:00Z")
    client.post("/v1/meters", json={
        "id": "meter_tokens", "name": "LLM Inference Tokens",
        "event_name": "token_usage", "aggregation_type": "SUM",
        "value_property": "total_tokens", "divide_by": 1000,
        "round_type": "none"}, headers=TENANT_HEADER)
    client.post("/v1/customers", json={
        "id": "cust_01", "external_id": "ext_01", "name": "Acme AI"},
        headers=TENANT_HEADER)

    # Basic plan: Rs 60/month fixed + the slab-tiered token price.
    client.post("/v1/plans", json={"id": "plan_basic", "name": "Basic Plan",
                                   "lookup_key": "basic"}, headers=TENANT_HEADER)
    client.post("/v1/prices", json={
        "id": "price_basic_fixed", "plan_id": "plan_basic", "type": "fixed",
        "fixed_amount_paise": 6_000}, headers=TENANT_HEADER)
    client.post("/v1/prices", json={
        "id": "price_tokens", "plan_id": "plan_basic", "meter_id": "meter_tokens",
        "type": "usage", "tier_mode": "slab", "tiers": TIERS,
        "display_name": "LLM Inference Tokens"}, headers=TENANT_HEADER)

    # Growth plan: Rs 120/month fixed + the same token price.
    client.post("/v1/plans", json={"id": "plan_growth", "name": "Growth Plan",
                                   "lookup_key": "growth"}, headers=TENANT_HEADER)
    client.post("/v1/prices", json={
        "id": "price_growth_fixed", "plan_id": "plan_growth", "type": "fixed",
        "fixed_amount_paise": 12_000}, headers=TENANT_HEADER)
    client.post("/v1/prices", json={
        "id": "price_tokens_growth", "plan_id": "plan_growth",
        "meter_id": "meter_tokens", "type": "usage", "tier_mode": "slab",
        "tiers": TIERS, "display_name": "LLM Inference Tokens"},
        headers=TENANT_HEADER)

    r = client.post("/v1/subscriptions", json={
        "id": "sub_01", "customer_id": "cust_01", "plan_id": "plan_basic",
        "start_date": "2026-04-01T00:00:00Z"}, headers=TENANT_HEADER)
    assert r.status_code == 201, r.text

    # Consume 1,500,000 tokens.
    at_time("2026-04-10T00:00:00Z")
    r = client.post("/v1/events", json={
        "event_id": "evt_expl_001", "event_name": "token_usage",
        "customer_id": "cust_01", "timestamp": "2026-04-09T12:00:00Z",
        "properties": {"total_tokens": 1_500_000}}, headers=TENANT_HEADER)
    assert r.status_code == 201, r.text

    # Upgrade on Day 15 -> proration nets Rs 30.00.
    at_time("2026-04-16T00:00:00Z")
    r = client.post("/v1/subscriptions/sub_01/upgrade", json={
        "target_plan_id": "plan_growth",
        "proration_behavior": "create_prorations"}, headers=TENANT_HEADER)
    assert r.status_code == 200, r.text
    settlement = r.json()["settlement_invoice"]
    assert settlement["amount_due_paise"] == 3_000          # Rs 30.00 net

    # Seal the cycle.
    at_time("2026-05-01T00:00:00Z")
    r = client.post("/v1/invoices/generate", json={
        "subscription_id": "sub_01", "period_start": "2026-04-01T00:00:00Z",
        "period_end": "2026-05-01T00:00:00Z"}, headers=TENANT_HEADER)
    assert r.status_code in (200, 201), r.text
    return r.json(), settlement


# --------------------------------------------------------------------------- #
# The GAPS.md acceptance criterion, with no AI key configured.
# --------------------------------------------------------------------------- #
def test_explanation_without_ai_key_returns_200_and_correct_math(client, at_time,
                                                                 monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_API_KEY", "")
    invoice, settlement = _seed_acceptance_scenario(client, at_time)

    r = client.get(f"/v1/invoices/{invoice['id']}/explanation",
                   headers=TENANT_HEADER)
    assert r.status_code == 200, r.text
    body = r.json()

    # The fallback path must be explicit, not silent.
    assert body["source"] == "template_fallback"
    assert body["ai_enabled"] is False

    facts = body["facts"]
    assert facts["customer_name"] == "Acme AI"
    assert facts["raw_tokens"] == 1_500_000
    assert facts["billed_units"] == 1500
    assert facts["tier_1_units"] == 1000
    assert facts["tier_1_rate_paise"] == 10        # 100,000 micro-paise
    assert facts["tier_1_cost_paise"] == 10_500
    assert facts["tier_2_units"] == 500
    assert facts["tier_2_rate_paise"] == 5         # 50,000 micro-paise
    assert facts["tier_2_cost_paise"] == 3_500
    # Proration recorded for this subscription inside the billed period.
    assert facts["proration_credit_paise"] == -3_000
    assert facts["proration_charge_paise"] == 6_000
    assert facts["proration_days"] == 15
    assert facts["total_paise"] == invoice["amount_due_paise"]

    # The narrative must be the documented template, with real numbers in it.
    text = body["explanation"]
    assert "Billing Summary for Acme AI" in text
    assert "1,500,000 tokens" in text
    assert "1,500 units" in text
    assert "Tier 1: 1,000 units at 10 paise = 10,500 paise" in text
    assert "Tier 2: 500 units at 5 paise = 3,500 paise" in text
    assert "3,000 paise credited" in text
    assert "6,000 paise charged" in text
    assert "Total Due:" in text
    # Internally consistent: the tier costs reconcile to the usage line.
    assert facts["tier_1_cost_paise"] + facts["tier_2_cost_paise"] == 14_000


def test_explanation_of_the_proration_settlement_invoice(client, at_time,
                                                         monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_API_KEY", "")
    _, settlement = _seed_acceptance_scenario(client, at_time)

    r = client.get(f"/v1/invoices/{settlement['id']}/explanation",
                   headers=TENANT_HEADER)
    assert r.status_code == 200, r.text
    facts = r.json()["facts"]
    assert facts["invoice_type"] == "one_off"
    assert facts["proration_credit_paise"] == -3_000
    assert facts["proration_charge_paise"] == 6_000
    assert facts["total_paise"] == 3_000
    assert "3,000 paise credited" in r.json()["explanation"]


def test_explanation_404_for_unknown_invoice(client, at_time):
    at_time("2026-04-01T00:00:00Z")
    assert client.get("/v1/invoices/inv_nope/explanation",
                      headers=TENANT_HEADER).status_code == 404


# --------------------------------------------------------------------------- #
# The AI path must never be able to crash the app.
# --------------------------------------------------------------------------- #
def test_missing_sdk_falls_back_without_crashing(client, at_time, monkeypatch):
    """A key IS set but the optional `anthropic` package is not installed."""
    monkeypatch.setenv("AI_PROVIDER_API_KEY", "sk-ant-not-a-real-key")
    invoice, _ = _seed_acceptance_scenario(client, at_time)

    from app.services import explainer
    monkeypatch.setattr(explainer, "_load_sdk",
                        lambda: (_ for _ in ()).throw(ImportError("no anthropic")))

    r = client.get(f"/v1/invoices/{invoice['id']}/explanation",
                   headers=TENANT_HEADER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "template_fallback"
    assert body["ai_enabled"] is True            # a key was configured...
    assert "anthropic" in body["ai_error"].lower()   # ...but the SDK was absent
    assert "Billing Summary for Acme AI" in body["explanation"]


def test_ai_api_error_falls_back_without_crashing(client, at_time, monkeypatch):
    """The SDK is present but the API call fails (bad key, network, 429, 500)."""
    monkeypatch.setenv("AI_PROVIDER_API_KEY", "sk-ant-not-a-real-key")
    invoice, _ = _seed_acceptance_scenario(client, at_time)

    from app.services import explainer

    def boom(prompt, system):
        raise RuntimeError("401 authentication_error: invalid x-api-key")

    monkeypatch.setattr(explainer, "_call_llm", boom)

    r = client.get(f"/v1/invoices/{invoice['id']}/explanation",
                   headers=TENANT_HEADER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "template_fallback"
    assert "authentication_error" in body["ai_error"]
    # The deterministic numbers survive the AI failure untouched.
    assert body["facts"]["tier_1_cost_paise"] == 10_500


def test_ai_path_is_used_when_the_call_succeeds(client, at_time, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_API_KEY", "sk-ant-not-a-real-key")
    invoice, _ = _seed_acceptance_scenario(client, at_time)

    from app.services import explainer
    captured = {}

    def fake(prompt, system):
        captured["prompt"] = prompt
        return "Your April bill is Rs 170.00: 1,500 billed units across two tiers."

    monkeypatch.setattr(explainer, "_call_llm", fake)

    r = client.get(f"/v1/invoices/{invoice['id']}/explanation",
                   headers=TENANT_HEADER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "ai"
    assert body["ai_error"] is None
    assert body["explanation"].startswith("Your April bill")
    # Facts are computed locally and handed to the model -- never invented by it.
    assert "10500" in captured["prompt"] or "10,500" in captured["prompt"]
    assert body["facts"]["tier_1_cost_paise"] == 10_500


def test_health_reports_template_fallback_when_no_key(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_API_KEY", "")
    assert client.get("/health").json()["ai_explainer"] == "template_fallback"


# --------------------------------------------------------------------------- #
# Runaway usage-spike detection.
# --------------------------------------------------------------------------- #
def _seed_spike_customer(client, at_time):
    at_time("2026-04-08T00:00:00Z")
    client.post("/v1/meters", json={
        "id": "meter_tokens", "name": "LLM Inference Tokens",
        "event_name": "token_usage", "aggregation_type": "SUM",
        "value_property": "total_tokens", "divide_by": 1000,
        "round_type": "none"}, headers=TENANT_HEADER)
    client.post("/v1/customers", json={
        "id": "cust_01", "external_id": "ext_01", "name": "Acme AI"},
        headers=TENANT_HEADER)


def _ingest_at(client, event_id, iso, tokens):
    r = client.post("/v1/events", json={
        "event_id": event_id, "event_name": "token_usage",
        "customer_id": "cust_01", "timestamp": iso,
        "properties": {"total_tokens": tokens}}, headers=TENANT_HEADER)
    assert r.status_code == 201, r.text


def test_spike_status_flags_runaway_loop(client, at_time):
    """GAPS.md: flag when 1-hour velocity > 3.0 x the 7-day hourly average."""
    _seed_spike_customer(client, at_time)

    # Steady baseline: 1,000 tokens per hour for 7 days (168 hours).
    # 168,000 tokens / 168 hours = 1,000 tokens/hour average.
    at_time("2026-04-08T00:00:00Z")
    for h in range(168):
        day, hour = divmod(h, 24)
        _ingest_at(client, f"evt_base_{h:03d}",
                   f"2026-04-{1 + day:02d}T{hour:02d}:00:00Z", 1_000)

    # An autonomous agent enters a prompt loop: 10,000 tokens in the last hour.
    at_time("2026-04-08T00:30:00Z")
    _ingest_at(client, "evt_runaway", "2026-04-08T00:05:00Z", 10_000)

    r = client.get("/v1/customers/cust_01/spike-status", headers=TENANT_HEADER)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["customer_id"] == "cust_01"
    assert body["threshold_factor"] == 3.0
    assert body["current_hour_tokens"] == 10_000
    assert body["baseline_hourly_tokens"] > 0
    assert body["ratio"] > 3.0
    assert body["spike_detected"] is True
    assert "spike" in body["message"].lower()


def test_spike_status_quiet_under_threshold(client, at_time):
    _seed_spike_customer(client, at_time)
    at_time("2026-04-08T00:00:00Z")
    for h in range(168):
        day, hour = divmod(h, 24)
        _ingest_at(client, f"evt_base_{h:03d}",
                   f"2026-04-{1 + day:02d}T{hour:02d}:00:00Z", 1_000)

    at_time("2026-04-08T00:30:00Z")
    _ingest_at(client, "evt_normal", "2026-04-08T00:05:00Z", 1_500)

    body = client.get("/v1/customers/cust_01/spike-status",
                      headers=TENANT_HEADER).json()
    assert body["spike_detected"] is False
    assert body["ratio"] <= 3.0


def test_spike_threshold_factor_is_configurable(client, at_time, monkeypatch):
    _seed_spike_customer(client, at_time)
    at_time("2026-04-08T00:00:00Z")
    for h in range(168):
        day, hour = divmod(h, 24)
        _ingest_at(client, f"evt_base_{h:03d}",
                   f"2026-04-{1 + day:02d}T{hour:02d}:00:00Z", 1_000)
    at_time("2026-04-08T00:30:00Z")
    _ingest_at(client, "evt_mid", "2026-04-08T00:05:00Z", 2_500)

    assert client.get("/v1/customers/cust_01/spike-status",
                      headers=TENANT_HEADER).json()["spike_detected"] is False

    monkeypatch.setenv("SPIKE_THRESHOLD_FACTOR", "2.0")
    body = client.get("/v1/customers/cust_01/spike-status",
                      headers=TENANT_HEADER).json()
    assert body["threshold_factor"] == 2.0
    assert body["spike_detected"] is True


def test_spike_status_with_no_baseline_does_not_false_alarm(client, at_time):
    """A brand-new customer has no 7-day history to compare against."""
    _seed_spike_customer(client, at_time)
    at_time("2026-04-08T00:30:00Z")
    _ingest_at(client, "evt_first", "2026-04-08T00:05:00Z", 50_000)

    body = client.get("/v1/customers/cust_01/spike-status",
                      headers=TENANT_HEADER).json()
    assert body["spike_detected"] is False
    assert body["baseline_hourly_tokens"] == 0
    assert body["reason"] == "insufficient_baseline"


def test_spike_status_404_for_unknown_customer(client, at_time):
    at_time("2026-04-08T00:00:00Z")
    assert client.get("/v1/customers/cust_nope/spike-status",
                      headers=TENANT_HEADER).status_code == 404


def test_ui_page_is_served_and_reads_both_endpoints(client):
    """The browser view (/ui) is a static page over the two endpoints above."""
    resp = client.get("/ui")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "/explanation" in resp.text
    assert "/spike-status" in resp.text


def test_settlement_explanation_does_not_narrate_zero_usage(client, at_time,
                                                            monkeypatch):
    """A proration-only invoice must not read "You used 0 tokens ... 0 units"."""
    monkeypatch.setenv("AI_PROVIDER_API_KEY", "")
    _, settlement = _seed_acceptance_scenario(client, at_time)
    text = client.get(f"/v1/invoices/{settlement['id']}/explanation",
                      headers=TENANT_HEADER).json()["explanation"]
    assert "0 tokens" not in text and "Tier 1" not in text
    assert "15 unused days" in text and "6,000 paise charged" in text
