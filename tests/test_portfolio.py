from datetime import datetime, timezone

from weatherbot.markets.book import Book
from weatherbot.portfolio import render_portfolio, show_portfolio
from weatherbot.storage.db import Database

NOW = "2026-10-20T16:00:00Z"


def _book(token: str, bid: str, ask: str) -> Book:
    return Book(
        token_id=token,
        best_bid=float(bid),
        best_ask=float(ask),
        bid_depth=100,
        ask_depth=100,
        spread=float(ask) - float(bid),
        min_order_size=1,
        fetched_at=NOW,
    )


def _position(
    db: Database,
    *,
    token: str,
    city: str,
    label: str,
    side: str,
    contracts: float,
    price: float,
    notional: float,
) -> None:
    db.conn.execute(
        """
        INSERT INTO paper_positions (
          bucket_key, station_id, city, valid_date, token_id, bucket_label,
          side, contracts, price, notional_usd, opened_at
        ) VALUES (?, 'miami', ?, '2026-10-22', ?, ?, ?, ?, ?, ?, ?)
        """,
        (token, city, token, label, side, contracts, price, notional, NOW),
    )
    db.conn.commit()


def test_marks_a_buy_at_the_bid_and_a_sell_at_the_ask(tmp_path) -> None:
    db = Database(str(tmp_path / "paper.sqlite"))
    db.ensure_account(1000, NOW)
    db.conn.execute("UPDATE paper_account SET cash_usd = 969 WHERE id = 1")
    _position(db, token="yes-long", city="Miami", label="80-81°F", side="buy", contracts=62.5, price=0.40, notional=25)
    _position(db, token="yes-short", city="Atlanta", label="72-73°F", side="sell", contracts=10, price=0.40, notional=6)
    books = {
        "yes-long": _book("yes-long", "0.50", "0.55"),
        "yes-short": _book("yes-short", "0.30", "0.35"),
    }
    text = render_portfolio(db, books)
    assert "capital 1000.00  cash 969.00  equity 1006.75  pnl +6.75" in text
    assert "Miami 2026-10-22 80-81°F buy 62.50 @ 0.4000 mark 0.5000 pnl +6.25" in text
    assert "Atlanta 2026-10-22 72-73°F sell 10.00 @ 0.4000 mark 0.3500 pnl +0.50" in text
    assert "open pnl +6.75" in text
    db.close()


def test_missing_exit_keeps_the_locked_notional(tmp_path) -> None:
    db = Database(str(tmp_path / "paper.sqlite"))
    db.ensure_account(1000, NOW)
    db.conn.execute("UPDATE paper_account SET cash_usd = 975 WHERE id = 1")
    _position(db, token="yes-long", city="Miami", label="80-81°F", side="buy", contracts=62.5, price=0.40, notional=25)
    text = render_portfolio(db, {}, "GET https://clob.polymarket.com/books -> timed out")
    assert "book failed:" in text
    assert "equity 1000.00  pnl +0.00" in text
    assert "1 unmarked" in text
    assert "mark - pnl n/a" in text
    db.close()


def test_show_portfolio_reads_the_live_book(tmp_path) -> None:
    db = Database(str(tmp_path / "paper.sqlite"))
    db.ensure_account(1000, NOW)
    db.conn.execute("UPDATE paper_account SET cash_usd = 975 WHERE id = 1")
    _position(db, token="yes-long", city="Miami", label="80-81°F", side="buy", contracts=10, price=0.40, notional=4)

    class Books:
        def post_json(self, url: str, body: object) -> list[dict[str, object]]:
            assert url == "https://clob.polymarket.com/books"
            return [
                {
                    "asset_id": "yes-long",
                    "bids": [{"price": "0.45", "size": "20"}],
                    "asks": [{"price": "0.50", "size": "20"}],
                    "last_trade_price": "0.99",
                }
            ]

    text = show_portfolio(Books(), db, datetime(2026, 10, 20, 16, tzinfo=timezone.utc))
    assert "mark 0.4500 pnl +0.50" in text
    assert "0.99" not in text
    db.close()
