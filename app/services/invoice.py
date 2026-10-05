"""Invoice assembly: fixed charges + metered usage + proration adjustments.

OBSERVATIONS.md section 2E: invoices are idempotent via
UNIQUE(tenant_id, idempotency_key) and, for cycle invoices,
UNIQUE(subscription_id, period_start, period_end).
All totals are integer paise (DATA_MODEL.md section 1).
"""
import hashlib
import json
import sqlite3
import uuid

from app.core import time as btime
from app.db import database
from app.services import meter as meter_svc
from app.services.pricing import Tier, calculate_cost, unit_amount_paise


class InvoiceError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def load_tiers(price_id: str) -> list[Tier]:
    rows = database.fetch_all(
        "SELECT * FROM price_tiers WHERE price_id = ? ORDER BY tier_index",
        (price_id,))
    return [Tier(r["tier_index"], r["up_to_units"],
                 r["unit_amount_micro_paise"], r["flat_amount_paise"]) for r in rows]


def derive_idempotency_key(invoice_type: str, subscription_id: str,
                           period_start: int, period_end: int) -> str:
    seed = f"{invoice_type}|{subscription_id}|{period_start}|{period_end}"
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


def _insert_invoice(tenant_id: str, customer_id: str, subscription_id: str | None,
                    idempotency_key: str, invoice_type: str, period_start: int,
                    period_end: int, lines: list[dict]) -> str:
    subtotal = sum(li["amount_paise"] for li in lines)
    invoice_id = _new_id("inv")
    database.execute(
        "INSERT INTO invoices (id, tenant_id, customer_id, subscription_id, "
        "idempotency_key, invoice_type, status, period_start, period_end, "
        "subtotal_paise, total_paise, amount_due_paise, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'finalized', ?, ?, ?, ?, ?, ?)",
        (invoice_id, tenant_id, customer_id, subscription_id, idempotency_key,
         invoice_type, period_start, period_end, subtotal, subtotal, subtotal,
         btime.now()),
    )
    for position, li in enumerate(lines):
        database.execute(
            "INSERT INTO invoice_line_items (id, invoice_id, price_id, description, "
            "quantity, unit_amount_paise, amount_paise, metadata_json, position) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_new_id("ili"), invoice_id, li.get("price_id"), li["description"],
             li["quantity"], li.get("unit_amount_paise", 0), li["amount_paise"],
             json.dumps(li.get("metadata") or {}, sort_keys=True), position),
        )
    return invoice_id


def get_invoice(tenant_id: str, invoice_id: str) -> dict | None:
    inv = database.fetch_one(
        "SELECT * FROM invoices WHERE tenant_id = ? AND id = ?",
        (tenant_id, invoice_id))
    if inv is None:
        return None
    return serialize(inv)


def serialize(inv: sqlite3.Row) -> dict:
    rows = database.fetch_all(
        "SELECT * FROM invoice_line_items WHERE invoice_id = ? ORDER BY position, id",
        (inv["id"],))
    return {
        "id": inv["id"],
        "tenant_id": inv["tenant_id"],
        "customer_id": inv["customer_id"],
        "subscription_id": inv["subscription_id"],
        "idempotency_key": inv["idempotency_key"],
        "invoice_type": inv["invoice_type"],
        "status": inv["status"],
        "period_start": inv["period_start"],
        "period_end": inv["period_end"],
        "period_start_iso": btime.to_iso(inv["period_start"]),
        "period_end_iso": btime.to_iso(inv["period_end"]),
        "subtotal_paise": inv["subtotal_paise"],
        "total_paise": inv["total_paise"],
        "amount_due_paise": inv["amount_due_paise"],
        "created_at": inv["created_at"],
        "line_items": [{
            "id": r["id"],
            "price_id": r["price_id"],
            "description": r["description"],
            "quantity": r["quantity"],
            "unit_amount_paise": r["unit_amount_paise"],
            "amount_paise": r["amount_paise"],
            "metadata": json.loads(r["metadata_json"] or "{}"),
        } for r in rows],
    }


def build_cycle_lines(tenant_id: str, subscription, period_start: int,
                      period_end: int) -> list[dict]:
    """Fixed subscription charges + metered usage charges for one cycle."""
    prices = database.fetch_all(
        "SELECT * FROM prices WHERE tenant_id = ? AND plan_id = ? "
        "ORDER BY type DESC, created_at, id",
        (tenant_id, subscription["plan_id"]))
    plan = database.fetch_one("SELECT * FROM plans WHERE id = ?",
                              (subscription["plan_id"],))
    lines: list[dict] = []

    for price in prices:
        if price["type"] == "fixed":
            amount = int(price["fixed_amount_paise"])
            if amount == 0:
                continue
            lines.append({
                "price_id": price["id"],
                "description": price["display_name"] or f"{plan['name']} (subscription)",
                "quantity": 1,
                "unit_amount_paise": amount,
                "amount_paise": amount,
                "metadata": {"charge_type": "fixed"},
            })
            continue

        mtr = meter_svc.get_meter(tenant_id, price["meter_id"])
        if mtr is None:
            raise InvoiceError(404, f"meter {price['meter_id']} does not exist")
        use = meter_svc.usage(tenant_id, mtr, subscription["customer_id"],
                             period_start, period_end)
        if use["quantity"] == 0:
            continue

        result = calculate_cost(use["quantity"], load_tiers(price["id"]),
                                price["tier_mode"])
        mode_label = "Tiered Slab" if price["tier_mode"] == "slab" else "Tiered Volume"
        meta = dict(result.metadata)
        meta.update({
            "charge_type": "usage",
            "meter_id": mtr["id"],
            "raw_units": use["raw_units"],
            "divide_by": use["divide_by"],
            "billed_units": use["quantity"],
            "amount_micro_paise": result.amount_micro_paise,
        })
        lines.append({
            "price_id": price["id"],
            "description": f"{price['display_name'] or mtr['name']} ({mode_label})",
            "quantity": use["quantity"],
            "unit_amount_paise": unit_amount_paise(result.amount_micro_paise,
                                                   use["quantity"]),
            "amount_paise": result.amount_paise,
            "metadata": meta,
        })

    return lines


def generate_cycle_invoice(tenant_id: str, subscription_id: str,
                           period_start: int, period_end: int,
                           idempotency_key: str | None = None) -> tuple[int, dict]:
    if period_end <= period_start:
        raise InvoiceError(400, "period_end must be greater than period_start")

    sub = database.fetch_one(
        "SELECT * FROM subscriptions WHERE tenant_id = ? AND id = ?",
        (tenant_id, subscription_id))
    if sub is None:
        raise InvoiceError(404, f"subscription {subscription_id} does not exist")

    key = idempotency_key or derive_idempotency_key(
        "subscription_cycle", subscription_id, period_start, period_end)

    existing = database.fetch_one(
        "SELECT * FROM invoices WHERE tenant_id = ? AND idempotency_key = ?",
        (tenant_id, key))
    if existing is not None:
        # Replaying the same request returns the same sealed invoice (200),
        # it never produces a second one.
        return 200, serialize(existing)

    lines = build_cycle_lines(tenant_id, sub, period_start, period_end)
    try:
        invoice_id = _insert_invoice(
            tenant_id, sub["customer_id"], subscription_id, key,
            "subscription_cycle", period_start, period_end, lines)
    except sqlite3.IntegrityError as exc:
        dupe = database.fetch_one(
            "SELECT * FROM invoices WHERE tenant_id = ? AND idempotency_key = ?",
            (tenant_id, key))
        if dupe is not None:
            return 200, serialize(dupe)
        raise InvoiceError(409, f"invoice already exists for this cycle: {exc}") from exc

    return 201, get_invoice(tenant_id, invoice_id)


def create_proration_invoice(tenant_id: str, subscription, effective_date: int,
                             lines: list[dict]) -> dict:
    key = derive_idempotency_key("one_off", subscription["id"],
                                 effective_date, effective_date)
    existing = database.fetch_one(
        "SELECT * FROM invoices WHERE tenant_id = ? AND idempotency_key = ?",
        (tenant_id, key))
    if existing is not None:
        return serialize(existing)
    invoice_id = _insert_invoice(
        tenant_id, subscription["customer_id"], subscription["id"], key,
        "one_off", effective_date, effective_date, lines)
    return get_invoice(tenant_id, invoice_id)
