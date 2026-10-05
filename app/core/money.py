"""Integer money arithmetic. No floats ever touch a monetary value.

DATA_MODEL.md section 1:
    paise        : 1 INR  = 100 paise           (all stored amounts)
    micro-paise  : 1 paise = 10,000 micro-paise  (unit rates, sub-paise rating)
    half-up rule : AmountPaise = floor((MicroPaise + 5,000) / 10,000)
"""
MICRO_PER_PAISE = 10_000
PAISE_PER_RUPEE = 100


def micro_to_paise(micro: int) -> int:
    """Half-up rounding from micro-paise to integer paise.

    Applied symmetrically around zero so a proration CREDIT of -x rounds the
    same magnitude as a charge of +x (banker-neutral, no drift toward zero).
    """
    micro = int(micro)
    if micro < 0:
        return -((-micro + MICRO_PER_PAISE // 2) // MICRO_PER_PAISE)
    return (micro + MICRO_PER_PAISE // 2) // MICRO_PER_PAISE


def paise_to_micro(paise: int) -> int:
    return int(paise) * MICRO_PER_PAISE


def format_rupees(paise: int) -> str:
    """Render integer paise as a human string, e.g. -150000 -> '-1,500.00'."""
    paise = int(paise)
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), PAISE_PER_RUPEE)
    return f"{sign}{whole:,}.{frac:02d}"


def prorate_paise(amount_paise: int, numerator: int, denominator: int) -> int:
    """amount_paise * (numerator / denominator), rounded half-up, all integer.

    The multiply happens in micro-paise BEFORE the divide, so no precision is
    lost mid-calculation (GAPS.md gap 7: round only at the final line item).
    """
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    micro = paise_to_micro(amount_paise) * int(numerator) // int(denominator)
    return micro_to_paise(micro)
