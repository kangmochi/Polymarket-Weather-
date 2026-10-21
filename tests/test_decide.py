import pytest

from weatherbot.markets.book import Book
from weatherbot.risk.sizing import CAPITAL_FRACTION, KELLY_FRACTION, order_notional
from weatherbot.signals.decide import decide


def _book(**overrides) -> Book:
    fields = dict(
        token_id="token-1",
        best_bid=0.30,
        best_ask=0.40,
        bid_depth=1_000,
        ask_depth=1_000,
        spread=0.10,
        min_order_size=None,
        fetched_at="2026-10-20T16:00:00Z",
    )
    fields.update(overrides)
    return Book(**fields)


def _decide(book: Book, probability: float = 1.0, **overrides):
    seen: list[str] = []

    def order_exists_for(side: str) -> bool:
        seen.append(side)
        return overrides.pop("duplicate", False)

    result = decide(
        probability=probability,
        book=book,
        max_spread=overrides.get("max_spread", 0.10),
        min_edge=overrides.get("min_edge", 0.05),
        capital_usd=overrides.get("capital_usd", 1000),
        max_order_usd=overrides.get("max_order_usd", 25),
        min_order_usd=overrides.get("min_order_usd", 1),
        cash_usd=overrides.get("cash_usd", 1000),
        position_open=overrides.get("position_open", False),
        order_exists_for=order_exists_for,
        station_id="nyc",
        valid_date="2026-10-20",
    )
    return result, seen


def test_wide_spread_rejects_before_edge_and_before_order_lookup():
    decision, seen = _decide(
        _book(best_bid=0.10, best_ask=0.50, spread=0.40),
    )
    assert decision.decision == "rejected"
    assert decision.reason == "spread_too_wide"
    assert decision.edge is None
    assert decision.side is None
    assert seen == []


def test_buy_uses_the_ask_and_size_is_the_smallest_of_three_caps():
    decision, _seen = _decide(_book())
    assert decision.decision == "accepted"
    assert decision.side == "buy"
    assert decision.reference_price == pytest.approx(0.40)
    assert decision.edge == pytest.approx(0.60)
    assert decision.notional_usd == pytest.approx(25)
    assert decision.contracts == pytest.approx(25 / 0.40)


def test_capital_fraction_caps_one_entry_and_max_order_can_go_lower():
    fraction_cap = order_notional(1.0, 0.40, "buy", 200, 25, capital_fraction=0.05)
    dollar_cap = order_notional(1.0, 0.40, "buy", 200, 8, capital_fraction=0.05)
    assert fraction_cap == pytest.approx(10)
    assert dollar_cap == pytest.approx(8)


def test_quarter_kelly_binds_when_it_is_smaller_than_the_dollar_and_capital_caps():
    notional = order_notional(0.42, 0.40, "buy", 1000, 25)
    full = (0.42 - 0.40) / (1 - 0.40)
    assert notional == pytest.approx(full * KELLY_FRACTION * 1000)
    assert notional < 25
    assert notional < CAPITAL_FRACTION * 1000


def test_sell_uses_the_bid():
    decision, _seen = _decide(_book(best_bid=0.70, best_ask=0.80, spread=0.10), probability=0.20)
    assert decision.side == "sell"
    assert decision.reference_price == pytest.approx(0.70)
    assert decision.edge == pytest.approx(0.50)
    sell_full = (0.70 - 0.20) / 0.70
    assert decision.notional_usd == pytest.approx(min(sell_full * 0.25 * 1000, 25, 50))


def test_edge_below_minimum_is_stored():
    decision, seen = _decide(_book(), probability=0.42)
    assert decision.reason == "edge_below_minimum"
    assert decision.edge == pytest.approx(0.02)
    assert seen == []


def test_one_position_and_duplicate_and_cash_are_separate_reasons():
    positioned, _ = _decide(_book(), position_open=True)
    duplicate, seen = _decide(_book(), duplicate=True)
    broke, _ = _decide(_book(), cash_usd=10)
    assert positioned.reason == "one_position_per_bucket"
    assert duplicate.reason == "duplicate_order"
    assert seen == ["buy"]
    assert broke.reason == "insufficient_cash"
    assert broke.edge == pytest.approx(0.60)


def test_incomplete_book_has_no_edge():
    decision, seen = _decide(_book(best_bid=None, best_ask=0.4, spread=None, bid_depth=0))
    assert decision.reason == "book_incomplete"
    assert decision.edge is None
    assert seen == []
