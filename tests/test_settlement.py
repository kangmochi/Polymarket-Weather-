from datetime import date, datetime, timezone

import pytest

from weatherbot.errors import WeatherbotError
from weatherbot.markets.discover import parse_yes_resolutions
from weatherbot.notify.telegram import TelegramNotifier
from weatherbot.portfolio import render_portfolio
from weatherbot.settlement import settlement_result, settle_expired
from weatherbot.stations import Station
from weatherbot.storage.db import Database

NOW = datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc)
OPENED = "2026-10-20T16:00:00Z"


def _station() -> Station:
    return Station(
        id="nyc",
        city="New York",
        title_city="NYC",
        slug_city="nyc",
        timezone="America/New_York",
        latitude=40.7794,
        longitude=-73.8803,
        settlement_station_id="KLGA",
    )


def _market(token: str, status: str, prices: object) -> dict:
    return {
        "umaResolutionStatus": status,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": prices,
        "clobTokenIds": f'["{token}", "no-{token}"]',
    }


def _resolved_event() -> list[dict]:
    return [
        {
            "title": "Highest temperature in NYC on October 20?",
            "closed": True,
            "markets": [
                _market("yes-68", "resolved", '["1", "0"]'),
                _market("yes-67", "resolved", ["0", "1"]),
                _market("yes-open", "proposed", '["0.5", "0.5"]'),
                _market("yes-soft", "resolved", '["0.99", "0.01"]'),
                _market("yes-bool", "resolved", [True, False]),
            ],
        }
    ]


def test_resolution_parser_keeps_only_exact_closed_results():
    valid = date(2026, 10, 20)
    found = parse_yes_resolutions(_resolved_event(), _station(), valid)
    assert found == {"yes-68": True, "yes-67": False}
    open_event = _resolved_event()
    open_event[0]["closed"] = False
    assert parse_yes_resolutions(open_event, _station(), valid) == {}
    assert parse_yes_resolutions([], _station(), valid) == {}
    with pytest.raises(WeatherbotError):
        parse_yes_resolutions({"closed": True}, _station(), valid)


def test_payout_returns_contracts_only_when_the_side_wins():
    assert settlement_result("buy", 10, 4, True) == ("win", 10, 6)
    assert settlement_result("buy", 10, 4, False) == ("loss", 0, -4)
    assert settlement_result("sell", 10, 6, True) == ("loss", 0, -6)
    assert settlement_result("sell", 10, 6, False) == ("win", 10, 4)
    with pytest.raises(ValueError):
        settlement_result("hold", 10, 4, True)


def _position(
    db: Database,
    *,
    key: str,
    station_id: str,
    city: str,
    valid_date: str,
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
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (key, station_id, city, valid_date, key, key, side, contracts, price, notional, OPENED),
    )
    db.conn.commit()


def test_record_settlement_moves_cash_once_and_keeps_history(tmp_path):
    db = Database(str(tmp_path / "paper.sqlite"))
    db.ensure_account(1000, OPENED)
    db.conn.execute("UPDATE paper_account SET cash_usd = 900 WHERE id = 1")
    db.conn.commit()
    _position(
        db,
        key="yes-68",
        station_id="nyc",
        city="New York",
        valid_date="2026-10-20",
        side="buy",
        contracts=10,
        price=0.4,
        notional=4,
    )
    assert db.record_settlement(
        "yes-68",
        yes_won=True,
        result="win",
        payout_usd=10,
        pnl_usd=6,
        settled_at="2026-10-21T16:00:00Z",
    )
    assert db.record_settlement(
        "yes-68",
        yes_won=True,
        result="win",
        payout_usd=10,
        pnl_usd=6,
        settled_at="2026-10-21T16:00:00Z",
    ) is False
    assert db.position_rows() == []
    assert db.cash_usd() == pytest.approx(910)
    rows = db.settlement_rows()
    assert len(rows) == 1
    assert rows[0]["result"] == "win"
    assert rows[0]["entry_price"] == pytest.approx(0.4)
    assert rows[0]["payout_usd"] == pytest.approx(10)
    text = render_portfolio(db, {})
    assert "no positions" in text
    assert "history 1 win, 0 loss, pnl +6.00" in text
    assert "New York 2026-10-20 yes-68 buy win +6.00" in text
    summary = db.summary()
    assert "settlements win 1  loss 0  realized +6.00" in summary
    db.close()


class _Gamma:
    def __init__(self, october_20: object, october_19: object | None = None) -> None:
        self.urls: list[str] = []
        self.october_20 = october_20
        self.october_19 = october_19

    def get_json(self, url: str):
        self.urls.append(url)
        if "october-20-2026" in url:
            return self.october_20
        if "october-19-2026" in url:
            if isinstance(self.october_19, WeatherbotError):
                raise self.october_19
            return self.october_19
        raise AssertionError(url)


def test_settle_expired_notifies_once_and_leaves_unresolved_positions(tmp_path):
    db = Database(str(tmp_path / "paper.sqlite"))
    db.ensure_account(1000, OPENED)
    db.conn.execute("UPDATE paper_account SET cash_usd = 900 WHERE id = 1")
    db.conn.commit()
    _position(db, key="yes-today", station_id="nyc", city="New York", valid_date="2026-10-21", side="buy", contracts=1, price=0.5, notional=0.5)
    _position(db, key="yes-68", station_id="nyc", city="New York", valid_date="2026-10-20", side="buy", contracts=10, price=0.4, notional=4)
    _position(db, key="yes-67", station_id="nyc", city="New York", valid_date="2026-10-20", side="sell", contracts=10, price=0.4, notional=6)
    _position(db, key="yes-loss", station_id="nyc", city="New York", valid_date="2026-10-20", side="buy", contracts=5, price=0.4, notional=2)
    _position(db, key="yes-open", station_id="nyc", city="New York", valid_date="2026-10-20", side="sell", contracts=8, price=0.2, notional=6.4)
    _position(db, key="yes-old", station_id="nyc", city="New York", valid_date="2026-10-19", side="buy", contracts=3, price=0.5, notional=1.5)
    _position(db, key="ghost", station_id="ghost", city="Ghost", valid_date="2026-10-19", side="buy", contracts=1, price=0.5, notional=0.5)
    event = _resolved_event()
    event[0]["markets"].append(_market("yes-loss", "resolved", '["0", "1"]'))
    http = _Gamma(event, WeatherbotError("gamma", "timed out"))
    notifier = TelegramNotifier(token="t", chat_id="1", http=_Posts())
    settle_expired(http, db, [_station()], NOW, notifier)
    settle_expired(http, db, [_station()], NOW, notifier)
    notifier.flush()
    notifier.flush()

    remaining = {row["bucket_key"] for row in db.position_rows()}
    assert remaining == {"yes-today", "yes-open", "yes-old", "ghost"}
    results = {row["bucket_key"]: row["result"] for row in db.settlement_rows()}
    assert results == {"yes-68": "win", "yes-67": "win", "yes-loss": "loss"}
    assert db.cash_usd() == pytest.approx(920)
    assert len(db.conn.execute("SELECT id FROM fetch_errors").fetchall()) == 2
    october_20 = [url for url in http.urls if "october-20-2026" in url]
    october_21 = [url for url in http.urls if "october-21-2026" in url]
    assert len(october_20) == 2
    assert october_21 == []
    posts = notifier.http.posts
    assert len(posts) == 1
    text = posts[0][1]["text"]
    assert text.startswith("Paper settlement: 2 win, 1 loss, pnl +8.00")
    assert "yes-68 buy win +6.00" in text
    assert "yes-67 sell win +4.00" in text
    assert "yes-loss buy loss -2.00" in text
    db.close()


class _Posts:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    def post_json(self, url: str, body: dict):
        self.posts.append((url, body))
        return {"ok": True}


def test_settlement_digest_follows_the_order_digest():
    http = _Posts()
    notifier = TelegramNotifier(token="t", chat_id="1", http=http)
    notifier.notify_order(
        city="Miami",
        valid_date="2026-10-22",
        bucket_label="82-83°F",
        side="buy",
        price=0.4,
        notional_usd=25,
        status="filled",
    )
    notifier.notify_settlement(
        city="Austin",
        valid_date="2026-10-20",
        bucket_label="82-83°F",
        side="sell",
        result="win",
        pnl_usd=3.25,
    )
    notifier.notify_settlement(
        city="Austin",
        valid_date="2026-10-20",
        bucket_label="84-85°F",
        side="sell",
        result="loss",
        pnl_usd=-25,
    )
    notifier.flush()
    assert len(http.posts) == 2
    assert http.posts[0][1]["text"].startswith("Paper scan:")
    text = http.posts[1][1]["text"]
    assert text.startswith("Paper settlement: 1 win, 1 loss, pnl -21.75")
    assert "82-83°F sell win +3.25" in text
    assert "84-85°F sell loss -25.00" in text
