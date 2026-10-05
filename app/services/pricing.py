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

from app.core.money import micro_to_paise


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
