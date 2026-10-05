"""Meters, plans and prices -- the operator-facing catalog (API.md 1 and 2)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api.deps import tenant
from app.core import time as btime
from app.db import database
from app.services.meter import AGGREGATIONS

router = APIRouter()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


@router.post("/meters", status_code=201)
def create_meter(body: dict, tenant_id: str = Depends(tenant)):
    name = (body.get("name") or "").strip()
    event_name = (body.get("event_name") or "").strip()
    agg = (body.get("aggregation_type") or "").strip().upper()
    if not name:
        raise HTTPException(400, "name is required")
    if not event_name:
        raise HTTPException(400, "event_name is required")
    if agg not in AGGREGATIONS:
        raise HTTPException(400, f"aggregation_type must be one of {AGGREGATIONS}")

    divide_by = int(body.get("divide_by") or 1)
    if divide_by < 1:
        raise HTTPException(400, "divide_by must be >= 1")
    round_type = (body.get("round_type") or "none").lower()
    if round_type not in ("up", "down", "none"):
        raise HTTPException(400, "round_type must be one of up, down, none")

    value_property = body.get("value_property")
    if agg in ("SUM", "COUNT_UNIQUE") and not value_property:
        raise HTTPException(400, f"value_property is required for {agg} meters")

    meter_id = (body.get("id") or _new_id("meter")).strip()
    if database.fetch_one("SELECT 1 FROM meters WHERE id = ?", (meter_id,)):
        raise HTTPException(409, f"meter {meter_id} already exists")

    database.execute(
        "INSERT INTO meters (id, tenant_id, name, event_name, aggregation_type, "
        "value_property, divide_by, round_type, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (meter_id, tenant_id, name, event_name, agg, value_property, divide_by,
         round_type, btime.now()))
    return {"id": meter_id, "tenant_id": tenant_id, "name": name,
            "event_name": event_name, "aggregation_type": agg,
            "value_property": value_property, "divide_by": divide_by,
            "round_type": round_type}


@router.post("/plans", status_code=201)
def create_plan(body: dict, tenant_id: str = Depends(tenant)):
    name = (body.get("name") or "").strip()
    lookup_key = (body.get("lookup_key") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    if not lookup_key:
        raise HTTPException(400, "lookup_key is required")
    if database.fetch_one(
            "SELECT 1 FROM plans WHERE tenant_id = ? AND lookup_key = ?",
            (tenant_id, lookup_key)):
        raise HTTPException(409, f"lookup_key {lookup_key} already exists")

    plan_id = (body.get("id") or _new_id("plan")).strip()
    if database.fetch_one("SELECT 1 FROM plans WHERE id = ?", (plan_id,)):
        raise HTTPException(409, f"plan {plan_id} already exists")
    database.execute(
        "INSERT INTO plans (id, tenant_id, name, lookup_key, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (plan_id, tenant_id, name, lookup_key, btime.now()))
    return {"id": plan_id, "tenant_id": tenant_id, "name": name,
            "lookup_key": lookup_key}


@router.post("/prices", status_code=201)
def create_price(body: dict, tenant_id: str = Depends(tenant)):
    plan_id = (body.get("plan_id") or "").strip()
    ptype = (body.get("type") or "").strip().lower()
    if ptype not in ("fixed", "usage"):
        raise HTTPException(400, "type must be 'fixed' or 'usage'")
    if not database.fetch_one("SELECT 1 FROM plans WHERE tenant_id = ? AND id = ?",
                              (tenant_id, plan_id)):
        raise HTTPException(404, f"plan_id {plan_id} does not exist")

    fixed_amount = int(body.get("fixed_amount_paise") or 0)
    if fixed_amount < 0:
        raise HTTPException(400, "fixed_amount_paise must not be negative")

    meter_id = body.get("meter_id")
    tier_mode = body.get("tier_mode")
    tiers = body.get("tiers") or []

    if ptype == "usage":
        if not meter_id:
            raise HTTPException(400, "meter_id is required when type is 'usage'")
        if not database.fetch_one(
                "SELECT 1 FROM meters WHERE tenant_id = ? AND id = ?",
                (tenant_id, meter_id)):
            raise HTTPException(404, f"meter_id {meter_id} does not exist")
        tier_mode = (tier_mode or "").lower()
        if tier_mode not in ("volume", "slab"):
            raise HTTPException(400, "tier_mode must be 'volume' or 'slab'")
        if not tiers:
            raise HTTPException(400, "usage prices require at least one tier")
        for expected, tier in enumerate(tiers):
            if int(tier.get("tier_index", -1)) != expected:
                raise HTTPException(400, "tier_index values must be sequential from 0")
            if int(tier.get("unit_amount_micro_paise") or 0) < 0:
                raise HTTPException(400, "unit_amount_micro_paise must not be negative")
            if int(tier.get("flat_amount_paise") or 0) < 0:
                raise HTTPException(400, "flat_amount_paise must not be negative")
        if tiers[-1].get("up_to_units") is not None:
            raise HTTPException(400, "the final tier must have up_to_units = null")
    else:
        meter_id, tier_mode, tiers = None, None, []

    price_id = (body.get("id") or _new_id("price")).strip()
    if database.fetch_one("SELECT 1 FROM prices WHERE id = ?", (price_id,)):
        raise HTTPException(409, f"price {price_id} already exists")

    database.execute(
        "INSERT INTO prices (id, tenant_id, plan_id, meter_id, type, tier_mode, "
        "fixed_amount_paise, display_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (price_id, tenant_id, plan_id, meter_id, ptype, tier_mode, fixed_amount,
         body.get("display_name"), btime.now()))
    for tier in tiers:
        database.execute(
            "INSERT INTO price_tiers (id, price_id, tier_index, up_to_units, "
            "unit_amount_micro_paise, flat_amount_paise) VALUES (?, ?, ?, ?, ?, ?)",
            (_new_id("tier"), price_id, int(tier["tier_index"]),
             tier.get("up_to_units"), int(tier["unit_amount_micro_paise"]),
             int(tier.get("flat_amount_paise") or 0)))

    return {"id": price_id, "plan_id": plan_id, "meter_id": meter_id, "type": ptype,
            "tier_mode": tier_mode, "fixed_amount_paise": fixed_amount,
            "tiers": tiers}


@router.post("/customers", status_code=201)
def create_customer(body: dict, tenant_id: str = Depends(tenant)):
    external_id = (body.get("external_id") or "").strip()
    name = (body.get("name") or "").strip()
    if not external_id:
        raise HTTPException(400, "external_id is required")
    if not name:
        raise HTTPException(400, "name is required")
    if database.fetch_one(
            "SELECT 1 FROM customers WHERE tenant_id = ? AND external_id = ?",
            (tenant_id, external_id)):
        raise HTTPException(409, f"external_id {external_id} already registered")

    cust_id = (body.get("id") or _new_id("cust")).strip()
    if database.fetch_one("SELECT 1 FROM customers WHERE id = ?", (cust_id,)):
        raise HTTPException(409, f"customer {cust_id} already exists")
    created = btime.now()
    database.execute(
        "INSERT INTO customers (id, tenant_id, external_id, name, email, timezone, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cust_id, tenant_id, external_id, name, body.get("email"),
         (body.get("timezone") or "UTC"), created))
    return {"id": cust_id, "external_id": external_id, "name": name,
            "email": body.get("email"), "timezone": body.get("timezone") or "UTC",
            "created_at": created}


@router.get("/meters/{meter_id}/usage")
def meter_usage(meter_id: str, customer_id: str, period_start: str | None = None,
                period_end: str | None = None, tenant_id: str = Depends(tenant)):
    from app.services import meter as meter_svc

    mtr = meter_svc.get_meter(tenant_id, meter_id)
    if mtr is None:
        raise HTTPException(404, f"meter {meter_id} does not exist")
    if not database.fetch_one("SELECT 1 FROM customers WHERE tenant_id = ? AND id = ?",
                              (tenant_id, customer_id)):
        raise HTTPException(404, f"customer_id {customer_id} does not exist")

    if period_start is None or period_end is None:
        # Default to the customer's active subscription period, else "all time".
        sub = database.fetch_one(
            "SELECT * FROM subscriptions WHERE tenant_id = ? AND customer_id = ? "
            "AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (tenant_id, customer_id))
        if sub is not None:
            start, end = sub["current_period_start"], sub["current_period_end"]
        else:
            start, end = 0, 2 ** 40
    else:
        try:
            start, end = btime.parse_ts(period_start), btime.parse_ts(period_end)
        except ValueError as exc:
            raise HTTPException(400, f"invalid period: {exc}") from exc
        if end <= start:
            raise HTTPException(400, "period_end must be greater than period_start")

    return JSONResponse(meter_svc.usage(tenant_id, mtr, customer_id, start, end))
