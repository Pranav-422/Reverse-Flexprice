"""Proration coefficients and credit/charge math for mid-period plan changes.

OBSERVATIONS.md section 2D gives two coefficient styles; both are implemented:

  second-based: remaining_seconds / total_seconds
  day-based:    remaining_days    / total_days

PRD.md section 5 Killer Test 2 requires the DAY-BASED result to be exactly
15/30 = 0.5000 for an Apr 1 -> May 1 cycle upgraded on Apr 16. See
app/core/time.whole_days_between for the logged deviation on the "+1".

Credit / charge / net (OBSERVATIONS.md section 2D):
    CreditAmount = (OldPrice x OldQuantity) x coefficient     [negative line]
    ChargeAmount = (NewPrice x NewQuantity) x coefficient     [positive line]
    NetAmount    = ChargeAmount - CreditAmount
"""
from dataclasses import dataclass

from app.core.money import prorate_paise
from app.core.time import whole_days_between


@dataclass
class Coefficient:
    numerator: int
    denominator: int
    total_days: int
    remaining_days: int
    total_seconds: int
    remaining_seconds: int
    mode: str

    @property
    def value(self) -> float:
        return self.numerator / self.denominator if self.denominator else 0.0

    def as_str(self) -> str:
        """Four decimal places, as asserted by PRD.md section 5 ('0.5000')."""
        return f"{self.value:.4f}"


def coefficient(period_start: int, period_end: int, proration_date: int,
                mode: str = "day") -> Coefficient:
    total_seconds = max(0, period_end - period_start)
    remaining_seconds = max(0, period_end - proration_date)
    total_days = whole_days_between(period_start, period_end)
    remaining_days = whole_days_between(proration_date, period_end)

    if mode == "day" and total_days > 0:
        num, den = remaining_days, total_days
    else:
        mode = "second"
        num, den = remaining_seconds, max(1, total_seconds)

    return Coefficient(
        numerator=min(num, den), denominator=den,
        total_days=total_days, remaining_days=remaining_days,
        total_seconds=total_seconds, remaining_seconds=remaining_seconds,
        mode=mode,
    )


def credit_amount_paise(old_price_paise: int, old_quantity: int,
                        coef: Coefficient) -> int:
    """Unused time on the outgoing plan, as a NEGATIVE amount."""
    gross = int(old_price_paise) * int(old_quantity)
    return -prorate_paise(gross, coef.numerator, coef.denominator)


def charge_amount_paise(new_price_paise: int, new_quantity: int,
                        coef: Coefficient) -> int:
    """Remaining time on the incoming plan, as a POSITIVE amount."""
    gross = int(new_price_paise) * int(new_quantity)
    return prorate_paise(gross, coef.numerator, coef.denominator)
