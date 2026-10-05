"""Plain-language invoice explainer (GAPS.md Improvement 2).

Upstream invoices carry no explanation of how raw tokens were packaged,
tiered or prorated (GAPS.md gap 10), which drives customer disputes.

Design rule: EVERY NUMBER IS COMPUTED LOCALLY from the sealed invoice. The
LLM only rewrites those facts into prose -- it never calculates anything, so
an AI outage, a bad key, or a missing package can change the WORDING but can
never change the MONEY.

Fallback ladder (the app must never crash on any rung):
    1. AI_PROVIDER_API_KEY empty          -> deterministic template
    2. `anthropic` package not installed  -> deterministic template + reason
    3. API call raises (auth/network/429) -> deterministic template + reason
    4. Model declines or returns nothing  -> deterministic template + reason
"""
import json

from app.core import config
from app.core.money import format_rupees
from app.db import database
from app.services.invoice import get_invoice

# Documented template from docs/GAPS.md section 2, Improvement 2.
TEMPLATE = (
    "Billing Summary for {customer_name}: You used {raw_tokens} tokens "
    "({billed_units} units). "
    "Tier 1: {tier_1_units} units at {tier_1_rate} paise = {tier_1_cost} paise. "
    "Tier 2: {tier_2_units} units at {tier_2_rate} paise = {tier_2_cost} paise. "
    "Proration: {proration_credit} paise credited, {proration_charge} paise charged. "
    "Total Due: Rs {total_rupees} ({total_paise} paise)."
)

SYSTEM_PROMPT = (
    "You are a billing-support writer for an Indian AI API company. You will be "
    "given a JSON object of VERIFIED billing facts, all amounts in integer paise "
    "(1 rupee = 100 paise). Write one short, warm paragraph for the customer "
    "explaining how the bill was calculated: how raw tokens were packaged into "
    "billable units, which pricing tiers applied, and any mid-cycle plan-change "
    "credit and charge. Use only the numbers given -- never invent, re-derive or "
    "round a figure. Do not include internal or system XML tags in your response."
)


def _fmt(n) -> str:
    return f"{int(n):,}"


def _tier_facts(line) -> dict:
    """Pull the per-tier audit trail the pricing engine recorded."""
    meta = line.get("metadata") or {}
    facts = {}
    for tier in meta.get("tiers") or []:
        human = int(tier["tier_index"]) + 1
        facts[f"tier_{human}_units"] = tier["units"]
        facts[f"tier_{human}_cost_paise"] = tier["cost_paise"]
        # Rates are stored as micro-paise; the template reports whole paise.
        facts[f"tier_{human}_rate_paise"] = round(
            int(tier["unit_amount_micro_paise"]) / 10_000)
    return facts


def _proration_context(tenant_id: str, invoice: dict) -> dict:
    """Credit/charge adjustments that apply to this invoice.

    A proration settlement is its own `one_off` invoice (DATA_MODEL.md section 7),
    so a cycle invoice's explanation looks up any settlement recorded for the
    same subscription inside the billed period. This is what lets the single
    documented template mention both tier maths and the proration in one
    coherent paragraph, as GAPS.md Improvement 2 requires.
    """
    lines = list(invoice["line_items"])
    if invoice["invoice_type"] != "one_off" and invoice["subscription_id"]:
        for row in database.fetch_all(
                "SELECT * FROM invoices WHERE tenant_id = ? AND subscription_id = ? "
                "AND invoice_type = 'one_off' AND period_start >= ? "
                "AND period_start < ? ORDER BY created_at",
                (tenant_id, invoice["subscription_id"],
                 invoice["period_start"], invoice["period_end"])):
            settlement = get_invoice(tenant_id, row["id"])
            if settlement:
                lines.extend(settlement["line_items"])

    credit = charge = 0
    days = None
    for line in lines:
        kind = (line.get("metadata") or {}).get("charge_type")
        if kind == "proration_credit":
            credit += line["amount_paise"]
        elif kind == "proration_charge":
            charge += line["amount_paise"]
        else:
            continue
        days = (line.get("metadata") or {}).get("remaining_days", days)
    return {"proration_credit_paise": credit,
            "proration_charge_paise": charge,
            "proration_days": days}


def build_facts(tenant_id: str, invoice: dict) -> dict:
    """Derive every explainable number from the sealed invoice. No AI involved."""
    customer = database.fetch_one(
        "SELECT * FROM customers WHERE tenant_id = ? AND id = ?",
        (tenant_id, invoice["customer_id"]))

    facts = {
        "invoice_id": invoice["id"],
        "invoice_type": invoice["invoice_type"],
        "customer_name": customer["name"] if customer else invoice["customer_id"],
        "period_start": invoice["period_start_iso"],
        "period_end": invoice["period_end_iso"],
        "raw_tokens": 0,
        "billed_units": 0,
        "divide_by": 1,
        "tier_1_units": 0, "tier_1_rate_paise": 0, "tier_1_cost_paise": 0,
        "tier_2_units": 0, "tier_2_rate_paise": 0, "tier_2_cost_paise": 0,
        "fixed_charges_paise": 0,
        "usage_charges_paise": 0,
        "total_paise": invoice["amount_due_paise"],
        "total_rupees": format_rupees(invoice["amount_due_paise"]),
        "line_items": [],
    }

    for line in invoice["line_items"]:
        meta = line.get("metadata") or {}
        facts["line_items"].append({
            "description": line["description"],
            "quantity": line["quantity"],
            "amount_paise": line["amount_paise"],
        })
        if meta.get("charge_type") == "fixed":
            facts["fixed_charges_paise"] += line["amount_paise"]
        elif meta.get("charge_type") == "usage":
            facts["usage_charges_paise"] += line["amount_paise"]
            facts["raw_tokens"] += int(meta.get("raw_units") or 0)
            facts["billed_units"] += int(meta.get("billed_units") or line["quantity"])
            facts["divide_by"] = int(meta.get("divide_by") or 1)
            facts["tier_mode"] = meta.get("tier_mode")
            facts.update(_tier_facts(line))

    facts.update(_proration_context(tenant_id, invoice))
    return facts


def render_template(facts: dict) -> str:
    """The deterministic fallback narrative. Always available, never fails."""
    return TEMPLATE.format(
        customer_name=facts["customer_name"],
        raw_tokens=_fmt(facts["raw_tokens"]),
        billed_units=_fmt(facts["billed_units"]),
        tier_1_units=_fmt(facts["tier_1_units"]),
        tier_1_rate=_fmt(facts["tier_1_rate_paise"]),
        tier_1_cost=_fmt(facts["tier_1_cost_paise"]),
        tier_2_units=_fmt(facts["tier_2_units"]),
        tier_2_rate=_fmt(facts["tier_2_rate_paise"]),
        tier_2_cost=_fmt(facts["tier_2_cost_paise"]),
        proration_credit=_fmt(abs(facts["proration_credit_paise"])),
        proration_charge=_fmt(facts["proration_charge_paise"]),
        total_rupees=facts["total_rupees"],
        total_paise=_fmt(facts["total_paise"]),
    )


def _load_sdk():
    """Import the OPTIONAL anthropic package.

    Kept behind a function so the import happens per request (never at module
    import time) and so tests can simulate an absent package.
    """
    try:
        import anthropic
    except ImportError as exc:                      # pragma: no cover
        raise ImportError(
            "the optional `anthropic` package is not installed; "
            "run `pip install anthropic` to enable AI explanations"
        ) from exc
    return anthropic


def _call_llm(prompt: str, system: str) -> str:
    """One Anthropic Messages API call. Raises on any failure; never swallows."""
    anthropic = _load_sdk()
    client = anthropic.Anthropic(api_key=config.ai_api_key(), timeout=20.0,
                                 max_retries=1)
    # NOTE: no `thinking` argument. Adaptive thinking is always on for
    # claude-opus-5-5 and `thinking: {"type": "disabled"}` returns a 400 at every
    # effort level. max_tokens is a hard cap on thinking PLUS response text.
    response = client.messages.create(
        model=config.get("AI_MODEL"),
        max_tokens=4096,
        output_config={"effort": "low"},
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined to answer (stop_reason=refusal)")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise RuntimeError("model returned no text")
    return text


def explain(tenant_id: str, invoice: dict) -> dict:
    """Explain an invoice. Returns 200-shaped data on every path."""
    facts = build_facts(tenant_id, invoice)
    template_text = render_template(facts)
    key = config.ai_api_key()

    if not key:
        # Rung 1: no key configured. This is a supported mode, not an error.
        return {
            "invoice_id": invoice["id"],
            "source": "template_fallback",
            "ai_enabled": False,
            "ai_error": None,
            "explanation": template_text,
            "template_explanation": template_text,
            "facts": facts,
        }

    try:
        prompt = (
            "Verified billing facts (JSON):\n"
            + json.dumps(facts, indent=2, sort_keys=True)
            + "\n\nFor reference, the deterministic summary of these same facts "
              "is:\n" + template_text
            + "\n\nRewrite this as one clear paragraph for the customer."
        )
        text = _call_llm(prompt, SYSTEM_PROMPT)
        source, error = "ai", None
    except Exception as exc:        # noqa: BLE001 -- the AI path must never 500
        # Rungs 2-4: SDK missing, API error, refusal, or empty output.
        text, source, error = template_text, "template_fallback", str(exc)

    return {
        "invoice_id": invoice["id"],
        "source": source,
        "ai_enabled": True,
        "ai_model": config.get("AI_MODEL") if source == "ai" else None,
        "ai_error": error,
        "explanation": text,
        "template_explanation": template_text,
        "facts": facts,
    }
