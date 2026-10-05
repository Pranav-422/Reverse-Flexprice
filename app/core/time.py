"""Injectable clock + billing-period arithmetic.

ARCHITECTURE.md section 4 ("Deterministic Time Injection"): nothing in the
engine calls datetime.now() directly. Everything goes through now() here, which
honours BILLING_NOW so a 30-day cycle or a mid-month upgrade can be simulated
instantly.
"""
from datetime import datetime, timedelta, timezone

from app.core import config


def parse_ts(value) -> int:
    """Accept epoch seconds (int/float/numeric str) or an ISO-8601 string.

    API.md uses epoch integers; PRD.md section 7 scenarios use ISO strings like
    "2026-10-01T10:00:00Z". Both are supported so the documented scenarios are
    runnable verbatim.
    """
    if isinstance(value, bool):
        raise ValueError("timestamp must be epoch seconds or an ISO-8601 string")
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            raise ValueError("empty timestamp")
        try:
            return int(s)
        except ValueError:
            pass
        iso = s[:-1] + "+00:00" if s.endswith("Z") else s
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    raise ValueError(f"unsupported timestamp: {value!r}")


def now() -> int:
    """Current server time as epoch seconds, overridable via BILLING_NOW."""
    injected = config.get("BILLING_NOW")
    if injected:
        return parse_ts(injected)
    return int(datetime.now(timezone.utc).timestamp())


def to_iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_one_period(start_epoch: int) -> int:
    """End of the billing period that begins at start_epoch (exclusive bound).

    If BILLING_PERIOD_MINUTES > 0 the cycle is that many minutes long, so a full
    period rollover can be demonstrated in a live demo without waiting a month.
    Otherwise it is one calendar month (ARCHITECTURE.md section 7).
    """
    minutes = config.get_int("BILLING_PERIOD_MINUTES")
    if minutes > 0:
        return start_epoch + minutes * 60

    dt = datetime.fromtimestamp(start_epoch, tz=timezone.utc)
    year, month = dt.year, dt.month + 1
    if month > 12:
        year, month = year + 1, 1
    # Clamp the day so Jan 31 -> Feb 28/29 instead of overflowing.
    day = min(dt.day, _days_in_month(year, month))
    return int(dt.replace(year=year, month=month, day=day).timestamp())


def _days_in_month(year: int, month: int) -> int:
    nxt_y, nxt_m = (year + 1, 1) if month == 12 else (year, month + 1)
    first_next = datetime(nxt_y, nxt_m, 1, tzinfo=timezone.utc)
    return (first_next - timedelta(days=1)).day


def whole_days_between(start_epoch: int, end_epoch: int) -> int:
    """Whole elapsed days between two instants (floor division on seconds).

    DEVIATION (logged in docs/AGENT_LOG.md): OBSERVATIONS.md section 2D states
    day-based counts as daysBetween(a, b) + 1, which for the Killer Test 2
    cycle would yield 16/31. PRD.md section 5 Killer Test 2 requires exactly
    30 total days, 15 remaining and a 15/30 = 0.5000 coefficient, and the
    PRD.md section 6 case 3 edge cases (Day 1 -> 30/30, Day 30 -> 1/30) agree.
    The PRD numbers are authoritative, so the "+1" is not applied.
    """
    return max(0, (end_epoch - start_epoch)) // 86400
