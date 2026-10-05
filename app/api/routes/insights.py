"""Differentiator endpoints (docs/GAPS.md section 2, Improvement 2)."""
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import tenant
from app.db import database
from app.services import explainer, spike_detector
from app.services.invoice import get_invoice

router = APIRouter()


@router.get("/invoices/{invoice_id}/explanation")
def invoice_explanation(invoice_id: str, tenant_id: str = Depends(tenant)):
    """Plain-language breakdown of token packaging, tiers and proration.

    Returns 200 whether or not AI_PROVIDER_API_KEY is configured -- without a
    key (or if the AI call fails) the deterministic template is returned and
    `source` says `template_fallback`.
    """
    invoice = get_invoice(tenant_id, invoice_id)
    if invoice is None:
        raise HTTPException(404, f"invoice {invoice_id} does not exist")
    return explainer.explain(tenant_id, invoice)


@router.get("/customers/{customer_id}/spike-status")
def customer_spike_status(customer_id: str, tenant_id: str = Depends(tenant)):
    """Real-time velocity: trailing 1-hour usage vs the 7-day hourly average."""
    if not database.fetch_one(
            "SELECT 1 FROM customers WHERE tenant_id = ? AND id = ?",
            (tenant_id, customer_id)):
        raise HTTPException(404, f"customer {customer_id} does not exist")
    return spike_detector.status(tenant_id, customer_id)
