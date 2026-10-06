"""Synchronous, deduplicated usage-event ingestion.

ARCHITECTURE.md section 3.1 + GAPS.md Improvement 1:
  * The HTTP response is returned only AFTER the row is durably committed, so
    a 201 is a real storage guarantee (fixes GAPS.md gap 2's false 202).
  * Deduplication is a DB UNIQUE(tenant_id, event_id) violation caught in
    Python -- not a cache lock with a TTL and not a merge-on-read engine, so
    a retry with a mutated timestamp or a replay after days still dedupes
    (fixes GAPS.md gaps 1 and 11).
"""
import json
import sqlite3
import uuid

from app.core import config, time as btime
from app.db import database


class IngestionError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _extract_quantity(properties: dict, event_name: str, tenant_id: str) -> int:
    """Pull the raw numeric value the matching meters measure.

    Meters declare which property holds the value (value_property). The first
    matching meter for this event_name decides. COUNT meters need no property.
    """
    rows = database.fetch_all(
        "SELECT * FROM meters WHERE tenant_id = ? AND event_name = ? "
        "ORDER BY created_at, id", (tenant_id, event_name))
    for meter in rows:
        prop = meter["value_property"]
        if not prop:
            continue
        if prop in properties:
            value = properties[prop]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise IngestionError(
                    400, f"properties.{prop} must be numeric, got {value!r}")
            if value < 0:
                # GAPS.md gap 4: upstream silently coerced negatives to zero,
                # hiding malformed input. Reject loudly instead.
                raise IngestionError(400, f"properties.{prop} must not be negative")
            if isinstance(value, float) and not value.is_integer():
                # Same class of bug as gap 4, and the one we criticise upstream
                # for: int(1.7) would silently store 1 and bill 0.7 fewer tokens
                # with no error. A token count is a whole number; 1.0 is fine.
                raise IngestionError(
                    400, f"properties.{prop} must be a whole number")
            return int(value)
    return 0


def validate_timestamp(ts: int) -> None:
    """Reject events outside the allowed drift window.

    API.md section 4 makes this a documented 400 on POST /v1/events:
    older than MAX_PAST_DRIFT_DAYS, or more than MAX_FUTURE_DRIFT_MINUTES
    ahead of current time. Without it, backdated events land in sealed
    invoice periods and are never billed (GAPS.md gap 3).
    """
    now = btime.now()
    max_past = config.get_int("MAX_PAST_DRIFT_DAYS") * 86400
    max_future = config.get_int("MAX_FUTURE_DRIFT_MINUTES") * 60

    if ts < now - max_past:
        raise IngestionError(
            400,
            f"timestamp {btime.to_iso(ts)} is older than the allowed "
            f"{config.get_int('MAX_PAST_DRIFT_DAYS')}-day drift window "
            f"(now {btime.to_iso(now)})",
        )
    if ts > now + max_future:
        raise IngestionError(
            400,
            f"timestamp {btime.to_iso(ts)} is more than "
            f"{config.get_int('MAX_FUTURE_DRIFT_MINUTES')} minutes in the future "
            f"(now {btime.to_iso(now)})",
        )


def finalized_period_for(tenant_id: str, customer_id: str, ts: int):
    """The sealed cycle period containing `ts`, or None.

    A `subscription_cycle` invoice that is `finalized` is immutable and
    idempotent: regenerating it returns the stored invoice rather than
    recomputing usage. An event dated inside such a period therefore can never
    be billed, so accepting it would silently drop revenue (GAPS.md gap 3 --
    "events dated months in the past land in sealed, immutable invoice periods
    and are never billed"). Periods are half-open: start <= ts < end.
    """
    return database.fetch_one(
        "SELECT period_start, period_end FROM invoices "
        "WHERE tenant_id = ? AND customer_id = ? "
        "AND invoice_type = 'subscription_cycle' AND status = 'finalized' "
        "AND period_start <= ? AND ? < period_end "
        "ORDER BY period_start LIMIT 1",
        (tenant_id, customer_id, ts, ts))


def ingest(tenant_id: str, payload: dict) -> tuple[int, dict]:
    """Returns (http_status, body). 201 on first write, 200 on duplicate."""
    event_id = (payload.get("event_id") or "").strip()
    event_name = (payload.get("event_name") or "").strip()
    customer_id = (payload.get("customer_id") or "").strip()
    if not event_id:
        raise IngestionError(400, "event_id is required")
    if not event_name:
        raise IngestionError(400, "event_name is required")
    if not customer_id:
        raise IngestionError(400, "customer_id is required")

    properties = payload.get("properties") or {}
    if not isinstance(properties, dict):
        raise IngestionError(400, "properties must be an object")

    raw_ts = payload.get("timestamp")
    try:
        ts = btime.parse_ts(raw_ts) if raw_ts is not None else btime.now()
    except ValueError as exc:
        raise IngestionError(400, f"invalid timestamp: {exc}") from exc
    validate_timestamp(ts)

    if not database.fetch_one(
            "SELECT 1 FROM customers WHERE tenant_id = ? AND id = ?",
            (tenant_id, customer_id)):
        raise IngestionError(404, f"customer_id {customer_id} does not exist")

    # Deduplication is answered FIRST and is unchanged by the sealed-period gate
    # below: a replayed event_id is idempotent, records nothing new, and must
    # still return 200 even if its period has since been finalized. This is a
    # fast path only -- the atomic INSERT further down remains the real arbiter,
    # so the concurrent-writer guarantee is untouched.
    if database.fetch_one(
            "SELECT 1 FROM usage_events WHERE tenant_id = ? AND event_id = ?",
            (tenant_id, event_id)):
        return 200, {
            "status": "duplicate_skipped",
            "event_id": event_id,
            "message": "Event previously processed; duplicate ignored",
        }

    # Reject NEW usage aimed at an already-sealed billing period.
    sealed = finalized_period_for(tenant_id, customer_id, ts)
    if sealed is not None:
        raise IngestionError(
            409,
            f"timestamp {btime.to_iso(ts)} falls in finalized billing period "
            f"{btime.to_iso(sealed['period_start'])}.."
            f"{btime.to_iso(sealed['period_end'])}",
        )

    quantity = _extract_quantity(properties, event_name, tenant_id)
    now = btime.now()

    try:
        # Single atomic INSERT. The UNIQUE index is the arbiter; we never
        # SELECT-then-INSERT, which would leave a race window open.
        database.execute(
            "INSERT INTO usage_events (id, tenant_id, event_id, customer_id, "
            "event_name, timestamp, quantity, properties_json, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"ue_{uuid.uuid4().hex[:20]}", tenant_id, event_id, customer_id,
             event_name, ts, quantity, json.dumps(properties, sort_keys=True), now),
        )
    except sqlite3.IntegrityError as exc:
        if "usage_events" not in str(exc) and "UNIQUE" not in str(exc).upper():
            raise
        return 200, {
            "status": "duplicate_skipped",
            "event_id": event_id,
            "message": "Event previously processed; duplicate ignored",
        }

    return 201, {
        "status": "created",
        "event_id": event_id,
        "recorded_quantity": quantity,
    }
