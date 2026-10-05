"""Invoice generation and retrieval (API.md section 6)."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api.deps import tenant
from app.core import time as btime
from app.services import invoice as invoice_svc
from app.services.invoice import InvoiceError

router = APIRouter()


@router.post("/invoices/generate")
def generate_invoice(body: dict, tenant_id: str = Depends(tenant)):
    subscription_id = (body.get("subscription_id") or "").strip()
    if not subscription_id:
        raise HTTPException(400, "subscription_id is required")
    try:
        period_start = btime.parse_ts(body["period_start"]) \
            if body.get("period_start") is not None else None
        period_end = btime.parse_ts(body["period_end"]) \
            if body.get("period_end") is not None else None
    except ValueError as exc:
        raise HTTPException(400, f"invalid period: {exc}") from exc

    if period_start is None or period_end is None:
        sub = invoice_svc.database.fetch_one(
            "SELECT * FROM subscriptions WHERE tenant_id = ? AND id = ?",
            (tenant_id, subscription_id))
        if sub is None:
            raise HTTPException(404, f"subscription {subscription_id} does not exist")
        period_start = period_start or sub["current_period_start"]
        period_end = period_end or sub["current_period_end"]

    try:
        status, payload = invoice_svc.generate_cycle_invoice(
            tenant_id, subscription_id, period_start, period_end,
            body.get("idempotency_key"))
    except InvoiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return JSONResponse(payload, status_code=status)


@router.get("/invoices/{invoice_id}")
def read_invoice(invoice_id: str, tenant_id: str = Depends(tenant)):
    inv = invoice_svc.get_invoice(tenant_id, invoice_id)
    if inv is None:
        raise HTTPException(404, f"invoice {invoice_id} does not exist")
    return inv
