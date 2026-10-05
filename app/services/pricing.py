"""Tiered pricing engine -- Volume and Slab (graduated) modes.

Rules transcribed from OBSERVATIONS.md section 2C and PRD.md section 5:
  * TierCost  = (units x unit_amount) + flat_amount
  * up_to is STRICTLY INCLUSIVE: units <= up_to stays in that tier.
  * Volume mode: the whole consumption maps to one matching bracket.
  * Slab mode:   consumption is partitioned across progressive brackets.

All arithmetic is in integer micro-paise. Half-up rounding to paise happens
exactly ONCE, when the final line item amount is produced -- never mid-tier
(GAPS.md gap 7 / Improvement 1).
"""
from dataclasses import dataclass, field

from app.core.money import MICRO_PER_PAISE, half_up_div, micro_to_paise


@dataclass
class Tier:
    tier_index: int
    up_to_units: int | None            # None => final, unbounded tier
    unit_amount_micro_paise: int
    flat_amount_paise: int = 0


@dataclass
class PricingResult:
    quantity: int                      # billable units fed into the tiers
    amount_micro_paise: int            # exact, unrounded
    amount_paise: int                  # half-up rounded, once
    metadata: dict = field(default_factory=dict)


def _ordered(tiers: list[Tier]) -> list[Tier]:
    return sorted(tiers, key=lambda t: t.tier_index)


def calculate_cost(quantity: int, tiers: list[Tier], tier_mode: str) -> PricingResult:
    """Rate an already-integer billable quantity.

    This is the REFERENCE path. It matches every documented scenario exactly
    (all of which divide evenly), but it rates a quantity that has already
    been rounded through the packaging divisor. Invoicing uses
    calculate_cost_exact() instead; this function is retained because
    scripts/demo contrasts the two to show the pre-rounding revenue error.
    """
    if not tiers:
        raise ValueError("price has no tiers")
    mode = (tier_mode or "").lower()
    if mode == "slab":
        return _slab(quantity, _ordered(tiers))
    if mode == "volume":
        return _volume(quantity, _ordered(tiers))
    raise ValueError(f"unsupported tier_mode: {tier_mode!r}")


def _slab(quantity: int, tiers: list[Tier]) -> PricingResult:
    """Partition consumption across progressive brackets."""
    remaining = max(0, int(quantity))
    lower = 0                           # units already consumed by lower tiers
    total_micro = 0
    meta: dict = {"tier_mode": "slab", "tiers": []}

    for tier in tiers:
        if remaining <= 0:
            break
        if tier.up_to_units is None:
            slice_units = remaining
        else:
            # up_to is inclusive and absolute, so this bracket's capacity is
            # up_to minus everything the lower brackets already absorbed.
            capacity = tier.up_to_units - lower
            if capacity <= 0:
                continue
            slice_units = min(remaining, capacity)

        slice_micro = (slice_units * tier.unit_amount_micro_paise
                       + tier.flat_amount_paise * 10_000)
        total_micro += slice_micro
        remaining -= slice_units
        lower += slice_units

        human = tier.tier_index + 1     # docs label tiers 1-based
        meta[f"tier_{human}_units"] = slice_units
        meta[f"tier_{human}_cost_paise"] = micro_to_paise(slice_micro)
        meta["tiers"].append({
            "tier_index": tier.tier_index,
            "units": slice_units,
            "unit_amount_micro_paise": tier.unit_amount_micro_paise,
            "flat_amount_paise": tier.flat_amount_paise,
            "cost_paise": micro_to_paise(slice_micro),
        })

    return PricingResult(int(quantity), total_micro, micro_to_paise(total_micro), meta)


def _volume(quantity: int, tiers: list[Tier]) -> PricingResult:
    """Assign the entire consumption to the single matching bracket."""
    qty = max(0, int(quantity))
    matched = tiers[-1]
    for tier in tiers:
        if tier.up_to_units is None or qty <= tier.up_to_units:
            matched = tier
            break

    total_micro = qty * matched.unit_amount_micro_paise + matched.flat_amount_paise * 10_000
    human = matched.tier_index + 1
    meta = {
        "tier_mode": "volume",
        "matched_tier_index": matched.tier_index,
        f"tier_{human}_units": qty,
        f"tier_{human}_cost_paise": micro_to_paise(total_micro),
        "tiers": [{
            "tier_index": matched.tier_index,
            "units": qty,
            "unit_amount_micro_paise": matched.unit_amount_micro_paise,
            "flat_amount_paise": matched.flat_amount_paise,
            "cost_paise": micro_to_paise(total_micro),
        }],
    }
    return PricingResult(int(quantity), total_micro, micro_to_paise(total_micro), meta)


def unit_amount_paise(amount_micro_paise: int, quantity: int) -> int:
    """Effective per-unit paise shown on the line item (half-up, display only)."""
    if quantity <= 0:
        return 0
    return micro_to_paise(amount_micro_paise // quantity)


# --------------------------------------------------------------------------- #
# IMPROVEMENT 1 (FIX): exact sub-paise rating.
#
# calculate_cost() above rates an already-integer billable quantity. That
# pre-rounds raw consumption through the packaging divisor before any money is
# computed, so 1,500,500 raw tokens at divide_by=1000 become 1,501 units and
# the customer is billed 14,005 paise instead of the true 14,002.5 -> 14,003.
#
# calculate_cost_exact() instead keeps the whole calculation in RAW token space
# over the common denominator divide_by, accumulating an exact integer
# numerator, and applies the DATA_MODEL.md section 1 half-up rule exactly ONCE
# when the invoice line item is produced -- which is what PRD.md section 6
# case 4 and GAPS.md Improvement 1 require.
#
# Internally: actual_micro_paise == micro_numerator / divide_by, held exactly.
# --------------------------------------------------------------------------- #


def _tier_numerator(slice_raw: int, tier: Tier, divide_by: int) -> int:
    """This bracket's contribution to the micro-paise numerator.

    units            = slice_raw / divide_by
    micro_paise      = units * unit_amount + flat_amount * MICRO_PER_PAISE
    numerator        = micro_paise * divide_by   (so the division stays exact)
    """
    return (slice_raw * tier.unit_amount_micro_paise
            + tier.flat_amount_paise * MICRO_PER_PAISE * divide_by)


def _units_label(slice_raw: int, divide_by: int):
    """Report tier units as an int when exact, else as the true fraction."""
    if divide_by <= 1 or slice_raw % divide_by == 0:
        return slice_raw // max(1, divide_by)
    return slice_raw / divide_by


def calculate_cost_exact(raw_units: int, divide_by: int, tiers: list[Tier],
                         tier_mode: str) -> PricingResult:
    """Rate raw consumption with no intermediate rounding anywhere."""
    if not tiers:
        raise ValueError("price has no tiers")
    divide_by = max(1, int(divide_by or 1))
    raw = max(0, int(raw_units))
    mode = (tier_mode or "").lower()
    ordered = _ordered(tiers)

    numerator = 0
    meta: dict = {"tier_mode": mode, "tiers": [], "exact_rating": True}

    if mode == "slab":
        remaining, lower_raw = raw, 0
        for tier in ordered:
            if remaining <= 0:
                break
            if tier.up_to_units is None:
                slice_raw = remaining
            else:
                # up_to is INCLUSIVE and expressed in billable units, so the
                # bracket boundary in raw space is up_to_units * divide_by.
                capacity = tier.up_to_units * divide_by - lower_raw
                if capacity <= 0:
                    continue
                slice_raw = min(remaining, capacity)

            slice_num = _tier_numerator(slice_raw, tier, divide_by)
            numerator += slice_num
            remaining -= slice_raw
            lower_raw += slice_raw
            _record(meta, tier, slice_raw, slice_num, divide_by)

    elif mode == "volume":
        matched = ordered[-1]
        for tier in ordered:
            if tier.up_to_units is None or raw <= tier.up_to_units * divide_by:
                matched = tier
                break
        numerator = _tier_numerator(raw, matched, divide_by)
        meta["matched_tier_index"] = matched.tier_index
        _record(meta, matched, raw, numerator, divide_by)

    else:
        raise ValueError(f"unsupported tier_mode: {tier_mode!r}")

    # THE single rounding step for this line item.
    amount_paise = half_up_div(numerator, divide_by * MICRO_PER_PAISE)

    meta["amount_micro_paise_numerator"] = numerator
    meta["micro_paise_denominator"] = divide_by
    exact_micro = (numerator // divide_by if numerator % divide_by == 0
                   else half_up_div(numerator, divide_by))
    meta["amount_micro_paise"] = exact_micro
    meta["amount_micro_paise_is_exact"] = numerator % divide_by == 0

    quantity = half_up_div(raw, divide_by)   # integer units shown on the line
    return PricingResult(quantity, exact_micro, amount_paise, meta)


def _record(meta: dict, tier: Tier, slice_raw: int, slice_num: int,
            divide_by: int) -> None:
    human = tier.tier_index + 1
    cost_paise = half_up_div(slice_num, divide_by * MICRO_PER_PAISE)
    units = _units_label(slice_raw, divide_by)
    meta[f"tier_{human}_units"] = units
    meta[f"tier_{human}_cost_paise"] = cost_paise
    meta["tiers"].append({
        "tier_index": tier.tier_index,
        "units": units,
        "raw_units": slice_raw,
        "unit_amount_micro_paise": tier.unit_amount_micro_paise,
        "flat_amount_paise": tier.flat_amount_paise,
        "cost_paise": cost_paise,
    })
