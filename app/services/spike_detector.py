"""Runaway usage-spike detection (GAPS.md Improvement 2).

Upstream has no velocity monitoring anywhere (GAPS.md gap 8), so an autonomous
agent stuck in a prompt loop can burn thousands of rupees before anyone sees it.

Logic, as specified in GAPS.md section 2:
    V_current = tokens in the trailing 1 hour          [now - 1h, now)
    V_7day    = tokens in the 7 days BEFORE that hour  [now - 7d, now - 1h)
                divided by those 167 hours
    spike if  V_current > SPIKE_THRESHOLD_FACTOR x V_7day     (default 3.0)

The two windows are deliberately DISJOINT. If the baseline window included the
current hour, a runaway burst would inflate its own baseline (diluting the
signal), and a customer's very first hour of traffic would manufacture a
baseline out of that same traffic and flag itself. With disjoint windows, "no
history" is genuinely zero and is reported as `insufficient_baseline` rather
than as a spike.
"""
import logging

from app.core import config, time as btime
from app.db import database

logger = logging.getLogger("billing.spike")

HOUR = 3600
WEEK_HOURS = 168
WEEK = WEEK_HOURS * HOUR
BASELINE_HOURS = WEEK_HOURS - 1          # the 7 days excluding the current hour


def _tokens_between(tenant_id: str, customer_id: str, start: int, end: int) -> int:
    row = database.fetch_one(
        "SELECT COALESCE(SUM(quantity), 0) AS v FROM usage_events "
        "WHERE tenant_id = ? AND customer_id = ? AND timestamp >= ? "
        "AND timestamp < ?",
        (tenant_id, customer_id, start, end))
    return int(row["v"] or 0)


def status(tenant_id: str, customer_id: str) -> dict:
    now = btime.now()
    factor = config.get_float("SPIKE_THRESHOLD_FACTOR")

    current = _tokens_between(tenant_id, customer_id, now - HOUR, now)
    week_total = _tokens_between(tenant_id, customer_id, now - WEEK, now - HOUR)
    baseline = week_total // BASELINE_HOURS   # integer tokens/hour

    if baseline <= 0:
        # No history to compare against -- refuse to raise a false alarm on a
        # customer's very first hour of traffic.
        return {
            "customer_id": customer_id,
            "evaluated_at": now,
            "evaluated_at_iso": btime.to_iso(now),
            "window": "1h vs 7d hourly average",
            "current_hour_tokens": current,
            "seven_day_tokens": week_total,
            "baseline_hourly_tokens": 0,
            "threshold_factor": factor,
            "threshold_tokens": 0,
            "ratio": None,
            "spike_detected": False,
            "reason": "insufficient_baseline",
            "message": ("No 7-day baseline yet for this customer, so velocity "
                        "cannot be judged. Monitoring continues."),
        }

    threshold = factor * baseline
    ratio = round(current / baseline, 4)
    detected = current > threshold

    if detected:
        # GAPS.md: "flags spike_detected: true and logs an alert".
        logger.warning(
            "USAGE SPIKE tenant=%s customer=%s current_hour=%s tokens "
            "baseline=%s tokens/h ratio=%.2fx threshold=%.1fx",
            tenant_id, customer_id, current, baseline, ratio, factor)

    return {
        "customer_id": customer_id,
        "evaluated_at": now,
        "evaluated_at_iso": btime.to_iso(now),
        "window": "1h vs 7d hourly average",
        "current_hour_tokens": current,
        "seven_day_tokens": week_total,
        "baseline_hourly_tokens": baseline,
        "threshold_factor": factor,
        "threshold_tokens": threshold,
        "ratio": ratio,
        "spike_detected": detected,
        "reason": "above_threshold" if detected else "within_threshold",
        "message": (
            f"Usage spike detected: {current:,} tokens in the last hour is "
            f"{ratio:.2f}x the 7-day average of {baseline:,} tokens/hour "
            f"(alert threshold {factor}x). Check for a runaway agent loop."
            if detected else
            f"Usage is normal: {current:,} tokens in the last hour is "
            f"{ratio:.2f}x the 7-day average of {baseline:,} tokens/hour."
        ),
    }
