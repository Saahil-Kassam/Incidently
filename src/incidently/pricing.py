"""Estimated cost of model calls, from the token counts the API reports.

Prices are US dollars per million tokens for the Claude API, as published on
2026-10-06. They are estimates: they don't track price changes, and a call a
fallback model served is priced as if that model handled all of it (the API
bills the declined part at the first model's rates). Cache writes use the
5-minute TTL, which is all Incidently uses.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .llm import Usage

CACHE_WRITE_MULTIPLIER = 1.25  # 5-minute TTL
PER_MILLION = 1_000_000


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float

    @property
    def cache_write(self) -> float:
        return self.input * CACHE_WRITE_MULTIPLIER


# Longest matching id wins, so a dated or suffixed model name still finds its row.
PRICES: dict[str, Price] = {
    "claude-fable-5-1": Price(10.00, 50.00, 0.25),
    "claude-mythos-5-1": Price(10.00, 50.00, 0.25),
    "claude-fable-5": Price(10.00, 50.00, 1.00),
    "claude-opus-5-5": Price(4.00, 20.00, 0.20),
    "claude-opus-5": Price(5.00, 25.00, 0.50),
    "claude-opus-4-8": Price(5.00, 25.00, 0.50),
    "claude-opus-4-7": Price(5.00, 25.00, 0.50),
    "claude-opus-4-6": Price(5.00, 25.00, 0.50),
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.20),
    "claude-sonnet-5": Price(2.00, 10.00, 0.20),
    "claude-sonnet-4-6": Price(3.00, 15.00, 0.30),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01),
    "claude-haiku-4-5": Price(1.00, 5.00, 0.10),
}


def price_for(model: str) -> Price | None:
    """The price row for a model id, or None for a model not in the table."""
    matches = [known for known in PRICES if model == known or model.startswith(known + "-")]
    return PRICES[max(matches, key=len)] if matches else None


def cost(model: str, usage: Usage) -> float | None:
    """Estimated dollars for ``usage`` on ``model``; None if the model has no known price."""
    price = price_for(model)
    if price is None:
        return None
    return (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_input_tokens * price.cache_read
        + usage.cache_creation_input_tokens * price.cache_write
    ) / PER_MILLION


def total_cost(calls: Iterable[tuple[str, Usage]]) -> tuple[float, bool]:
    """The summed estimate over (model, usage) pairs, and whether every model had a price.

    Calls with no tokens (the offline fake) cost nothing whatever the model.
    """
    total = 0.0
    complete = True
    for model, usage in calls:
        if usage == Usage():
            continue
        estimate = cost(model, usage)
        if estimate is None:
            complete = False
        else:
            total += estimate
    return total, complete
