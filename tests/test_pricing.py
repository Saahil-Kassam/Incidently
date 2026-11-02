"""Estimating cost from token usage."""

import pytest

from incidently.llm import Usage
from incidently.pricing import PRICES, cost, price_for, total_cost


def test_exact_and_suffixed_model_ids_find_their_price():
    assert price_for("claude-opus-5-5") is PRICES["claude-opus-5-5"]
    assert price_for("claude-opus-5-20260101") is PRICES["claude-opus-5"]
    # The longest match wins: Opus 5.5 is not priced as Opus 5.
    assert price_for("claude-opus-5-5-20261001") is PRICES["claude-opus-5-5"]


def test_unknown_models_have_no_price():
    assert price_for("fake-llm") is None
    assert price_for("claude-opus-55") is None
    assert cost("fake-llm", Usage(100, 100)) is None


def test_cost_prices_each_kind_of_token():
    # Opus 5.5: $4 in, $20 out, $0.20 cache read, $5 cache write (1.25x) per million.
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_read_input_tokens=2_000_000,
        cache_creation_input_tokens=400_000,
    )
    assert cost("claude-opus-5-5", usage) == pytest.approx(4.0 + 2.0 + 0.4 + 2.0)


def test_total_cost_sums_and_flags_unknown_models():
    calls = [("claude-opus-5-5", Usage(1_000_000)), ("claude-sonnet-5-5", Usage(0, 100_000))]
    assert total_cost(calls) == (pytest.approx(5.0), True)

    total, complete = total_cost([*calls, ("mystery-model", Usage(10))])
    assert total == pytest.approx(5.0) and not complete


def test_calls_without_tokens_cost_nothing():
    assert total_cost([("fake-llm", Usage())]) == (0.0, True)
