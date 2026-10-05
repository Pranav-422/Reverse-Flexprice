"""Read-only views that back the browser dashboard (/app).

Nothing here writes. Every number is produced by the same services the
billing API uses -- pricing previews call calculate_cost_exact(), upgrade
previews call the proration module -- so the dashboard can never show a
figure the engine would not bill.
"""
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import tenant
from app.core import time as btime
from app.db import database
from app.services import proration, spike_detector
from app.services import subscription as sub_svc
from app.services.invoice import load_tiers
from app.services.pricing import calculate_cost_exact

router = APIRouter()

HOUR = 3600


@router.get("/customers")
def list_customers(tenant_id: str = Depends(tenant)):
    rows = database.fetch_all(
        "SELECT c.id, c.external_id, c.name, c.created_at, "
        "s.id AS subscription_id, s.plan_id, p.name AS plan_name, s.status, "
        "s.current_period_start, s.current_period_end "
        "FROM customers c "
        "LEFT JOIN subscriptions s ON s.customer_id = c.id AND s.status = 'active' "
        "LEFT JOIN plans p ON p.id = s.plan_id "
        "WHERE c.tenant_id = ? ORDER BY c.created_at, c.id", (tenant_id,))
    return {"customers": [dict(r) for r in rows]}


@router.get("/plans")
def list_plans(tenant_id: str = Depends(tenant)):
    plans = []
    for p in database.fetch_all(
            "SELECT * FROM plans WHERE tenant_id = ? ORDER BY created_at, id",
            (tenant_id,)):
        prices = []
        for pr in database.fetch_all(
                "SELECT pr.*, m.name AS meter_name, m.divide_by FROM prices pr "
                "LEFT JOIN meters m ON m.id = pr.meter_id "
                "WHERE pr.plan_id = ? ORDER BY pr.type, pr.id", (p["id"],)):
            prices.append({
                "id": pr["id"], "type": pr["type"], "tier_mode": pr["tier_mode"],
                "fixed_amount_paise": pr["fixed_amount_paise"],
                "display_name": pr["display_name"], "meter_id": pr["meter_id"],
                "meter_name": pr["meter_name"], "divide_by": pr["divide_by"],
                "tiers": [t.__dict__ for t in load_tiers(pr["id"])]
                if pr["type"] == "usage" else [],
            })
        plans.append({"id": p["id"], "name": p["name"],
                      "lookup_key": p["lookup_key"], "prices": prices})
    return {"plans": plans}


@router.get("/prices/{price_id}/preview")
def price_preview(price_id: str, raw_units: int, tenant_id: str = Depends(tenant)):
    """Rate `raw_units` under BOTH slab and volume with this price's tiers."""
    price = database.fetch_one(
        "SELECT pr.*, m.divide_by FROM prices pr LEFT JOIN meters m "
        "ON m.id = pr.meter_id WHERE pr.tenant_id = ? AND pr.id = ?",
        (tenant_id, price_id))
    if price is None or price["type"] != "usage":
        raise HTTPException(404, f"usage price {price_id} does not exist")
    if raw_units < 0:
        raise HTTPException(400, "raw_units must be >= 0")
    tiers = load_tiers(price_id)
    divide_by = price["divide_by"] or 1
    out = {"price_id": price_id, "raw_units": raw_units, "divide_by": divide_by,
           "configured_mode": price["tier_mode"]}
    for mode in ("slab", "volume"):
        r = calculate_cost_exact(raw_units, divide_by, tiers, mode)
        out[mode] = {"amount_paise": r.amount_paise, "billable_units": r.quantity,
                     "amount_micro_paise": r.amount_micro_paise,
                     "tiers": r.metadata["tiers"]}
    return out


@router.get("/subscriptions/{sub_id}/upgrade-preview")
def upgrade_preview(sub_id: str, target_plan_id: str,
                    effective_date: str | None = None,
                    tenant_id: str = Depends(tenant)):
    """What POST /upgrade would settle, computed by the same proration code."""
    sub = sub_svc.get(tenant_id, sub_id)
    if sub is None:
        raise HTTPException(404, f"subscription {sub_id} does not exist")
    try:
        eff = btime.parse_ts(effective_date) if effective_date else btime.now()
    except ValueError as exc:
        raise HTTPException(400, f"invalid effective_date: {exc}") from exc
    start, end = sub["current_period_start"], sub["current_period_end"]
    if not start <= eff <= end:
        raise HTTPException(400, "effective_date is outside the current period")
    coef = proration.coefficient(start, end, eff, mode="day")
    old = sub_svc._plan_recurring_paise(tenant_id, sub["plan_id"])
    new = sub_svc._plan_recurring_paise(tenant_id, target_plan_id)
    credit = proration.credit_amount_paise(old, 1, coef)
    charge = proration.charge_amount_paise(new, 1, coef)
    return {"subscription_id": sub_id, "from_plan_id": sub["plan_id"],
            "target_plan_id": target_plan_id,
            "effective_date_iso": btime.to_iso(eff),
            "period_start_iso": btime.to_iso(start),
            "period_end_iso": btime.to_iso(end),
            "total_days": coef.total_days, "remaining_days": coef.remaining_days,
            "coefficient": coef.as_str(),
            "old_plan_paise": old, "new_plan_paise": new,
            "credit_paise": credit, "charge_paise": charge,
            "net_paise": credit + charge}


@router.get("/invoices")
def list_invoices(customer_id: str | None = None, tenant_id: str = Depends(tenant)):
    sql = ("SELECT i.id, i.customer_id, c.name AS customer_name, i.subscription_id, "
           "i.invoice_type, i.status, i.period_start, i.period_end, "
           "i.amount_due_paise, i.created_at FROM invoices i "
           "JOIN customers c ON c.id = i.customer_id WHERE i.tenant_id = ?")
    params: list = [tenant_id]
    if customer_id:
        sql += " AND i.customer_id = ?"
        params.append(customer_id)
    rows = database.fetch_all(sql + " ORDER BY i.created_at DESC, i.id", params)
    return {"invoices": [dict(r, period_start_iso=btime.to_iso(r["period_start"]),
                              period_end_iso=btime.to_iso(r["period_end"]))
                         for r in rows]}


@router.get("/usage/recent")
def recent_events(limit: int = 25, customer_id: str | None = None,
                  tenant_id: str = Depends(tenant)):
    sql = ("SELECT event_id, customer_id, event_name, timestamp, quantity, "
           "ingested_at FROM usage_events WHERE tenant_id = ?")
    params: list = [tenant_id]
    if customer_id:
        sql += " AND customer_id = ?"
        params.append(customer_id)
    sql += " ORDER BY ingested_at DESC, timestamp DESC LIMIT ?"
    params.append(max(1, min(limit, 200)))
    total = database.fetch_one(
        "SELECT COUNT(*) AS n FROM usage_events WHERE tenant_id = ?", (tenant_id,))
    return {"total_rows": total["n"],
            "events": [dict(r, timestamp_iso=btime.to_iso(r["timestamp"]))
                       for r in database.fetch_all(sql, params)]}


@router.get("/usage/hourly")
def hourly_usage(customer_id: str, hours: int = 168, tenant_id: str = Depends(tenant)):
    """Token totals per hour for the trailing `hours`, ending at server now."""
    hours = max(1, min(hours, 24 * 31))
    now = btime.now()
    start = now - hours * HOUR
    rows = database.fetch_all(
        "SELECT (timestamp - ?) / ? AS b, SUM(quantity) AS v FROM usage_events "
        "WHERE tenant_id = ? AND customer_id = ? AND timestamp >= ? AND timestamp < ? "
        "GROUP BY b", (start, HOUR, tenant_id, customer_id, start, now))
    by = {int(r["b"]): int(r["v"]) for r in rows}
    return {"customer_id": customer_id, "now_iso": btime.to_iso(now),
            "points": [{"hour_start_iso": btime.to_iso(start + i * HOUR),
                        "tokens": by.get(i, 0)} for i in range(hours)]}


@router.get("/overview")
def overview(tenant_id: str = Depends(tenant)):
    one = lambda sql: database.fetch_one(sql, (tenant_id,))  # noqa: E731
    billed = one("SELECT COALESCE(SUM(amount_due_paise), 0) AS v, COUNT(*) AS n "
                 "FROM invoices WHERE tenant_id = ? AND status = 'finalized'")
    events = one("SELECT COUNT(*) AS n, COUNT(DISTINCT event_id) AS u "
                 "FROM usage_events WHERE tenant_id = ?")
    customers = database.fetch_all(
        "SELECT id FROM customers WHERE tenant_id = ?", (tenant_id,))
    spikes = [s for s in (spike_detector.status(tenant_id, c["id"]) for c in customers)
              if s["spike_detected"]]
    return {"tenant_id": tenant_id, "now_iso": btime.to_iso(btime.now()),
            "billed_paise": billed["v"], "finalized_invoices": billed["n"],
            "event_rows": events["n"], "distinct_event_ids": events["u"],
            "customers": len(customers),
            "active_spikes": [{"customer_id": s["customer_id"], "ratio": s["ratio"]}
                              for s in spikes]}
