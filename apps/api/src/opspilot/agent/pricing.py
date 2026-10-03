"""Model price table and cost estimation.

Every figure this produces is an **estimate** computed from a hard-coded table, not
billed truth. It is labelled as an estimate wherever it is displayed, and the release
gate on cost says "target mean below USD 0.02 per investigation" — a target measured
against these estimates, with the provider's own invoice as the real check.

Prices are per million tokens and go stale. Update them with the date checked.
"""

from __future__ import annotations

from decimal import Decimal

from opspilot.core.config import Settings

#: model -> (input USD per 1M tokens, output USD per 1M tokens). Checked 2026-09-12.
PRICE_TABLE: dict[str, tuple[Decimal, Decimal]] = {
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("1.60")),
    "text-embedding-3-small": (Decimal("0.02"), Decimal("0")),
    # The fake provider costs nothing; keeping it in the table means the accounting
    # code path runs identically in CI.
    "fake-1": (Decimal("0"), Decimal("0")),
}

_MILLION = Decimal("1000000")


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """Estimated USD for one call. Unknown models cost 0 and are reported as unknown.

    Returning 0 rather than guessing is deliberate: a fabricated cost in a dashboard is
    worse than a visible gap, because it looks like a measurement.
    """
    prices = PRICE_TABLE.get(model)
    if prices is None:
        return Decimal("0")
    per_in, per_out = prices
    cost = (Decimal(input_tokens) * per_in + Decimal(output_tokens) * per_out) / _MILLION
    # Six decimal places: a single cheap call is well below a cent.
    return cost.quantize(Decimal("0.000001"))


def is_priced(model: str) -> bool:
    """False when a cost figure for this model would be a guess."""
    return model in PRICE_TABLE


def configured_cost(settings: Settings, model: str, inputs: int, outputs: int) -> Decimal:
    if settings.llm_provider != "openrouter":
        return estimate_cost(model, inputs, outputs)
    if settings.openrouter_free_only:
        return Decimal("0")
    return (
        (
            Decimal(inputs) * Decimal(str(settings.openrouter_input_usd_per_million))
            + Decimal(outputs) * Decimal(str(settings.openrouter_output_usd_per_million))
        )
        / _MILLION
    ).quantize(Decimal("0.000001"))
