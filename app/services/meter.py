"""Meter aggregation over half-open windows.

OBSERVATIONS.md section 2B: windows are half-open --
    timestamp >= period_start AND timestamp < period_end
and late arrivals bucket by EVENT timestamp, not ingestion time.

Unit packaging (OBSERVATIONS.md section 2C):
    BilledUnits = round(RawUnits / DivideBy)   with round in {up, down, none}
"""
import json
import math

from app.db import database

AGGREGATIONS = ("COUNT", "SUM", "COUNT_UNIQUE")


def get_meter(tenant_id: str, meter_id: str):
    return database.fetch_one(
        "SELECT * FROM meters WHERE tenant_id = ? AND id = ?", (tenant_id, meter_id)
    )


def raw_units(tenant_id: str, meter, customer_id: str,
              period_start: int, period_end: int) -> int:
    """Aggregate raw event values inside the half-open window."""
    agg = meter["aggregation_type"].upper()
    params = (tenant_id, customer_id, meter["event_name"], period_start, period_end)
    where = ("tenant_id = ? AND customer_id = ? AND event_name = ? "
             "AND timestamp >= ? AND timestamp < ?")

    if agg == "COUNT":
        row = database.fetch_one(
            f"SELECT COUNT(*) AS v FROM usage_events WHERE {where}", params)
        return int(row["v"] or 0)

    if agg == "SUM":
        row = database.fetch_one(
            f"SELECT COALESCE(SUM(quantity), 0) AS v FROM usage_events WHERE {where}",
            params)
        return int(row["v"] or 0)

    if agg == "COUNT_UNIQUE":
        rows = database.fetch_all(
            f"SELECT properties_json FROM usage_events WHERE {where}", params)
        prop = meter["value_property"]
        seen = set()
        for r in rows:
            props = json.loads(r["properties_json"] or "{}")
            if prop in props:
                seen.add(str(props[prop]))
        return len(seen)

    raise ValueError(f"unsupported aggregation_type: {agg}")


def to_billable_units(raw: int, divide_by: int, round_type: str) -> int:
    """Apply unit packaging, e.g. raw tokens / 1,000 for per-1k-token billing.

    DEVIATION (logged in docs/AGENT_LOG.md): round_type 'none' means no
    directional rounding is configured, but DATA_MODEL.md types both
    invoice_line_items.quantity and the usage response quantity as INTEGER.
    For 'none' we therefore round half-up to the nearest whole unit. Every
    documented scenario divides exactly (50,000/1,000, 1,500,000/1,000), so
    this only affects undocumented, non-exact inputs.
    """
    divide_by = max(1, int(divide_by or 1))
    if divide_by == 1:
        return int(raw)
    rt = (round_type or "none").lower()
    if rt == "up":
        return math.ceil(raw / divide_by)
    if rt == "down":
        return int(raw) // divide_by
    return (int(raw) * 2 + divide_by) // (2 * divide_by)   # half-up, integer only


def usage(tenant_id: str, meter, customer_id: str,
          period_start: int, period_end: int) -> dict:
    raw = raw_units(tenant_id, meter, customer_id, period_start, period_end)
    return {
        "meter_id": meter["id"],
        "customer_id": customer_id,
        "aggregation_type": meter["aggregation_type"],
        "raw_units": raw,
        "quantity": to_billable_units(raw, meter["divide_by"], meter["round_type"]),
        "divide_by": int(meter["divide_by"]),
        "period_start": period_start,
        "period_end": period_end,
    }
