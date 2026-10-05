"""Subscription lifecycle and mid-period plan upgrades."""
import uuid

from app.core import time as btime
from app.db import database
from app.services import invoice as invoice_svc
from app.services import proration


class SubscriptionError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _plan_recurring_paise(tenant_id: str, plan_id: str) -> int:
    """Total fixed (recurring) price of a plan, in paise."""
    row = database.fetch_one(
        "SELECT COALESCE(SUM(fixed_amount_paise), 0) AS v FROM prices "
        "WHERE tenant_id = ? AND plan_id = ? AND type = 'fixed'",
        (tenant_id, plan_id))
    return int(row["v"] or 0)


def serialize(sub) -> dict:
    return {
        "id": sub["id"],
        "tenant_id": sub["tenant_id"],
        "customer_id": sub["customer_id"],
        "plan_id": sub["plan_id"],
        "status": sub["status"],
        "billing_anchor": sub["billing_anchor"],
        "current_period_start": sub["current_period_start"],
        "current_period_end": sub["current_period_end"],
        "current_period_start_iso": btime.to_iso(sub["current_period_start"]),
        "current_period_end_iso": btime.to_iso(sub["current_period_end"]),
    }


def create(tenant_id: str, payload: dict) -> dict:
    customer_id = (payload.get("customer_id") or "").strip()
    plan_id = (payload.get("plan_id") or "").strip()
    if not customer_id or not plan_id:
        raise SubscriptionError(400, "customer_id and plan_id are required")
    if not database.fetch_one("SELECT 1 FROM customers WHERE tenant_id = ? AND id = ?",
                              (tenant_id, customer_id)):
        raise SubscriptionError(404, f"customer_id {customer_id} does not exist")
    if not database.fetch_one("SELECT 1 FROM plans WHERE tenant_id = ? AND id = ?",
                              (tenant_id, plan_id)):
        raise SubscriptionError(404, f"plan_id {plan_id} does not exist")

    raw_start = payload.get("start_date")
    try:
        start = btime.parse_ts(raw_start) if raw_start is not None else btime.now()
    except ValueError as exc:
        raise SubscriptionError(400, f"invalid start_date: {exc}") from exc

    sub_id = (payload.get("id") or f"sub_{uuid.uuid4().hex[:16]}").strip()
    period_end = btime.add_one_period(start)
    database.execute(
        "INSERT INTO subscriptions (id, tenant_id, customer_id, plan_id, status, "
        "billing_anchor, current_period_start, current_period_end, created_at) "
        "VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)",
        (sub_id, tenant_id, customer_id, plan_id, start, start, period_end,
         btime.now()),
    )
    for price in database.fetch_all(
            "SELECT id FROM prices WHERE tenant_id = ? AND plan_id = ?",
            (tenant_id, plan_id)):
        database.execute(
            "INSERT INTO subscription_line_items (id, subscription_id, price_id, "
            "quantity, start_date, end_date) VALUES (?, ?, ?, 1, ?, NULL)",
            (f"sli_{uuid.uuid4().hex[:16]}", sub_id, price["id"], start),
        )
    return serialize(get(tenant_id, sub_id))


def get(tenant_id: str, sub_id: str):
    return database.fetch_one(
        "SELECT * FROM subscriptions WHERE tenant_id = ? AND id = ?",
        (tenant_id, sub_id))


def upgrade(tenant_id: str, sub_id: str, payload: dict) -> dict:
    """Mid-period plan change with day-based proration settlement."""
    sub = get(tenant_id, sub_id)
    if sub is None:
        raise SubscriptionError(404, f"subscription {sub_id} does not exist")
    if sub["status"] != "active":
        raise SubscriptionError(400, f"subscription is {sub['status']}, not active")

    target_plan_id = (payload.get("target_plan_id") or "").strip()
    if not target_plan_id:
        raise SubscriptionError(400, "target_plan_id is required")
    behavior = (payload.get("proration_behavior") or "").strip()
    if behavior not in ("create_prorations", "none"):
        raise SubscriptionError(
            400, "proration_behavior must be 'create_prorations' or 'none'")

    target_plan = database.fetch_one(
        "SELECT * FROM plans WHERE tenant_id = ? AND id = ?",
        (tenant_id, target_plan_id))
    if target_plan is None:
        raise SubscriptionError(404, f"target plan {target_plan_id} does not exist")
    if target_plan_id == sub["plan_id"]:
        raise SubscriptionError(400, "target plan is identical to current plan")

    old_plan = database.fetch_one("SELECT * FROM plans WHERE id = ?", (sub["plan_id"],))

    raw_eff = payload.get("effective_date")
    try:
        effective = btime.parse_ts(raw_eff) if raw_eff is not None else btime.now()
    except ValueError as exc:
        raise SubscriptionError(400, f"invalid effective_date: {exc}") from exc

    period_start = sub["current_period_start"]
    period_end = sub["current_period_end"]
    if not (period_start <= effective <= period_end):
        raise SubscriptionError(
            400,
            f"effective_date {btime.to_iso(effective)} is outside the current "
            f"period {btime.to_iso(period_start)}..{btime.to_iso(period_end)}",
        )

    coef = proration.coefficient(period_start, period_end, effective, mode="day")

    settlement = None
    proration_detail = {
        "mode": coef.mode,
        "total_days": coef.total_days,
        "remaining_days": coef.remaining_days,
        "total_seconds": coef.total_seconds,
        "remaining_seconds": coef.remaining_seconds,
        "coefficient": coef.as_str(),
        "coefficient_numerator": coef.numerator,
        "coefficient_denominator": coef.denominator,
    }

    if behavior == "create_prorations":
        old_paise = _plan_recurring_paise(tenant_id, sub["plan_id"])
        new_paise = _plan_recurring_paise(tenant_id, target_plan_id)
        credit = proration.credit_amount_paise(old_paise, 1, coef)
        charge = proration.charge_amount_paise(new_paise, 1, coef)
        days = coef.remaining_days
        lines = [
            {
                "price_id": None,
                "description": f"Unused time on {old_plan['name']} ({days} days)",
                "quantity": 1,
                "unit_amount_paise": credit,
                "amount_paise": credit,
                "metadata": {"charge_type": "proration_credit",
                             "plan_id": sub["plan_id"],
                             "full_period_paise": old_paise, **proration_detail},
            },
            {
                "price_id": None,
                "description": f"Remaining time on {target_plan['name']} ({days} days)",
                "quantity": 1,
                "unit_amount_paise": charge,
                "amount_paise": charge,
                "metadata": {"charge_type": "proration_charge",
                             "plan_id": target_plan_id,
                             "full_period_paise": new_paise, **proration_detail},
            },
        ]
        proration_detail["credit_paise"] = credit
        proration_detail["charge_paise"] = charge
        proration_detail["net_paise"] = charge + credit
        settlement = invoice_svc.create_proration_invoice(
            tenant_id, sub, effective, lines)

    # Version the line items: terminate the old plan's items at the effective
    # instant and open the new plan's items from it (OBSERVATIONS.md Trace B.5).
    database.execute(
        "UPDATE subscription_line_items SET end_date = ? "
        "WHERE subscription_id = ? AND end_date IS NULL",
        (effective, sub_id))
    for price in database.fetch_all(
            "SELECT id FROM prices WHERE tenant_id = ? AND plan_id = ?",
            (tenant_id, target_plan_id)):
        database.execute(
            "INSERT INTO subscription_line_items (id, subscription_id, price_id, "
            "quantity, start_date, end_date) VALUES (?, ?, ?, 1, ?, NULL)",
            (f"sli_{uuid.uuid4().hex[:16]}", sub_id, price["id"], effective))
    database.execute("UPDATE subscriptions SET plan_id = ? WHERE id = ?",
                     (target_plan_id, sub_id))

    return {
        "subscription_id": sub_id,
        "previous_plan_id": old_plan["id"],
        "target_plan_id": target_plan_id,
        "effective_date": effective,
        "effective_date_iso": btime.to_iso(effective),
        "proration_behavior": behavior,
        "proration": proration_detail,
        "settlement_invoice": settlement,
    }
