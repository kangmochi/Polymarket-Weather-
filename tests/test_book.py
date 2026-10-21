import pytest

from weatherbot.markets.book import parse_book


def test_best_prices_are_the_touch_even_when_the_payload_is_reversed():
    # Live CLOB payloads are not reliably best-first. Last trade is present and ignored.
    book = parse_book(
        {
            "bids": [
                {"price": "0.001", "size": "46.32"},
                {"price": "0.012", "size": "780.05"},
            ],
            "asks": [
                {"price": "0.999", "size": "26015"},
                {"price": "0.017", "size": "15"},
            ],
            "last_trade_price": "0.983",
            "min_order_size": "5",
        },
        token_id="yes-token",
        fetched_at="2026-10-20T16:00:00Z",
    )
    assert book.best_bid == 0.012
    assert book.best_ask == 0.017
    assert book.bid_depth == 780.05
    assert book.ask_depth == 15
    assert book.spread == pytest.approx(0.005)
    assert not hasattr(book, "last_trade_price")
    assert book.min_order_size == 5


def test_depth_sums_every_order_at_the_same_touch_price():
    book = parse_book(
        {
            "bids": [
                {"price": "0.40", "size": "10"},
                {"price": "0.40", "size": "5"},
                {"price": "0.39", "size": "100"},
            ],
            "asks": [{"price": "0.42", "size": "3"}],
        },
        token_id="yes-token",
        fetched_at="2026-10-20T16:00:00Z",
    )
    assert book.best_bid == 0.4
    assert book.bid_depth == 15
    assert book.best_ask == 0.42
    assert book.ask_depth == 3
