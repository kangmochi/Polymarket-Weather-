from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from weatherbot.errors import WeatherbotError
from weatherbot.scan import run_scan
from weatherbot.wallet import LiveConfigError
from weatherbot.settings import Settings, load_settings
from weatherbot.stations import Station, load_stations
from weatherbot.storage.db import Database

NOW = datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


class FakeHttp:
    def __init__(self, books: list[dict]) -> None:
        self.books = books
        self.gets: list[str] = []
        self.posts: list[str] = []
        self.telegram: list[tuple[str, dict]] = []

    def get_json(self, url: str):
        self.gets.append(url)
        if "ensemble-api.open-meteo.com" in url:
            return {
                "daily": {
                    "time": ["2026-10-20"],
                    "temperature_2m_max_member01": [20.0],
                    "temperature_2m_max_member02": [20.0],
                    "temperature_2m_max_member03": [20.0],
                    "temperature_2m_max_member04": [20.0],
                }
            }
        if "aviationweather.gov/api/data/metar" in url:
            today = int(datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc).timestamp())
            yesterday = int(datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc).timestamp())
            return [
                {"icaoId": "KLGA", "obsTime": yesterday, "temp": 40, "rawOb": "YESTERDAY"},
                {"icaoId": "KLGA", "obsTime": today, "temp": 15, "rawOb": "TODAY"},
            ]
        if "gamma-api.polymarket.com/events" in url:
            return [_event()]
        raise AssertionError(url)

    def post_json(self, url: str, body: object):
        self.posts.append(url)
        if "api.telegram.org" in url:
            assert isinstance(body, dict)
            self.telegram.append((url, body))
            return {"ok": True}
        assert url == "https://clob.polymarket.com/books"
        return self.books


def _event() -> dict:
    markets = []
    for token, label in (
        ("token-wide", "68°F"),
        ("token-fill", "68°F"),
        ("token-thin", "68°F"),
        ("token-closed", "not a bucket"),
    ):
        markets.append(
            {
                "groupItemTitle": label,
                "conditionId": f"cond-{token}",
                "outcomes": '["Yes", "No"]',
                "clobTokenIds": f'["{token}", "no-{token}"]',
                "outcomePrices": '["0.99", "0.01"]',
                "bestAsk": 0.99,
                "bestBid": 0.01,
                "closed": token == "token-closed",
                "active": True,
                "acceptingOrders": True,
            }
        )
    return {
        "title": "Highest temperature in NYC on October 20?",
        "closed": False,
        "markets": markets,
    }


def _books() -> list[dict]:
    return [
        _level("token-wide", bid="0.10", ask="0.50", ask_size="1000", last="0.99"),
        _level("token-fill", bid="0.30", ask="0.40", ask_size="1000", last="0.99"),
        _level("token-thin", bid="0.30", ask="0.40", ask_size="1", last="0.01"),
    ]


def _level(token: str, bid: str, ask: str, ask_size: str, last: str) -> dict:
    return {
        "asset_id": token,
        "bids": [{"price": bid, "size": "1000"}, {"price": "0.001", "size": "9"}],
        "asks": [{"price": "0.999", "size": "5000"}, {"price": ask, "size": ask_size}],
        "last_trade_price": last,
        "min_order_size": "1",
    }


def _settings(database: str) -> Settings:
    return Settings(
        capital_usd=1000,
        max_order_usd=25,
        capital_fraction=0.05,
        min_order_usd=1,
        min_edge=0.05,
        max_spread=0.10,
        forecast_days=1,
        tail_lower_bound_c=-80,
        tail_upper_bound_c=60,
        sigma_floor_c=0,
        sigma_per_sqrt_day_c=0,
        ensemble_model="ncep_gefs025",
        database=database,
    )


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


def test_scan_logs_rejections_and_paper_fills_without_using_market_prices(tmp_path: Path):
    db = Database(str(tmp_path / "paper.sqlite"))
    http = FakeHttp(_books())
    summary = run_scan(http, db, [_station()], _settings(db.path), NOW)

    members = db.conn.execute("SELECT COUNT(*) AS n FROM ensemble_members").fetchone()
    assert members["n"] == 4
    assert db.metar_dates("nyc") == ["2026-10-20"]
    assert db.bias_count() == 0

    rows = {row["token_id"]: row for row in db.signal_rows()}
    assert set(rows) == {"token-wide", "token-fill", "token-thin"}
    assert rows["token-wide"]["reason"] == "spread_too_wide"
    assert rows["token-wide"]["edge"] is None
    assert rows["token-wide"]["model_probability"] == pytest.approx(1)
    assert rows["token-fill"]["decision"] == "accepted"
    assert rows["token-fill"]["side"] == "buy"
    assert rows["token-fill"]["reference_price"] == pytest.approx(0.40)
    assert rows["token-fill"]["model_probability"] == pytest.approx(1)
    assert rows["token-fill"]["execution_status"] == "filled"
    assert rows["token-thin"]["decision"] == "accepted"
    assert rows["token-thin"]["execution_status"] == "unfilled"
    assert rows["token-thin"]["execution_reason"] == "insufficient_depth"
    assert db.cash_usd() == pytest.approx(975)
    assert len(db.position_rows()) == 1
    assert len(db.order_rows()) == 2
    assert "bias rows 0" in summary

    run_scan(http, db, [_station()], _settings(db.path), NOW)
    reasons = [row["reason"] for row in db.signal_rows() if row["token_id"] == "token-fill"]
    assert reasons == ["accepted", "one_position_per_bucket"]
    thin_reasons = [row["reason"] for row in db.signal_rows() if row["token_id"] == "token-thin"]
    assert thin_reasons == ["accepted", "duplicate_order"]
    assert len(db.order_rows()) == 2
    assert db.cash_usd() == pytest.approx(975)
    assert db.bias_count() == 0
    db.close()


def test_scan_sends_one_digest_and_does_not_repeat_filled_orders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    env = tmp_path / "telegram.env"
    env.write_text("TELEGRAM_BOT_TOKEN=123:secret\nTELEGRAM_CHAT_ID=555\n", encoding="utf-8")
    db = Database(str(tmp_path / "paper.sqlite"))
    http = FakeHttp(_books())
    settings = _settings(db.path)
    run_scan(http, db, [_station()], settings, NOW, telegram_env=str(env))
    assert len(http.telegram) == 1
    text = http.telegram[0][1]["text"]
    assert text.startswith("Paper scan: 1 filled, 1 unfilled")
    assert "68°F buy" in text
    assert "123:secret" not in text
    run_scan(http, db, [_station()], settings, NOW, telegram_env=str(env))
    assert len(http.telegram) == 1
    db.close()


def test_scan_stops_after_three_gamma_timeouts(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    class TimeoutGamma(FakeHttp):
        def __init__(self) -> None:
            super().__init__([])
            self.gamma = 0

        def get_json(self, url: str):
            if "gamma-api.polymarket.com" in url:
                self.gamma += 1
                raise WeatherbotError("http", f"GET {url} -> timed out")
            return super().get_json(url)

    db = Database(str(tmp_path / "paper.sqlite"))
    http = TimeoutGamma()
    stations = [
        _station(),
        replace(_station(), id="chicago", city="Chicago", slug_city="chicago", title_city="Chicago"),
        replace(_station(), id="miami", city="Miami", slug_city="miami", title_city="Miami"),
        replace(_station(), id="atlanta", city="Atlanta", slug_city="atlanta", title_city="Atlanta"),
    ]
    summary = run_scan(http, db, stations, _settings(db.path), NOW)
    output = capsys.readouterr().out
    assert http.gamma == 3
    assert "markets stopped: gamma-api.polymarket.com failed 3 times in a row" in output
    assert "market Atlanta" not in output
    assert "last error 2026-10-20T16:00:00Z http miami:" in summary
    assert "timed out" in summary
    db.close()


def test_shipped_registry_and_paper_config_load():
    stations = load_stations(str(ROOT / "config" / "stations.yaml"))
    by_id = {station.id: station for station in stations}
    assert by_id["nyc"].settlement_station_id == "KLGA"
    assert by_id["nyc"].timezone == "America/New_York"
    assert by_id["denver"].settlement_station_id == "KBKF"
    settings = load_settings(str(ROOT / "config" / "paper.yaml"))
    assert settings.capital_usd == 1000
    assert settings.max_order_usd == 25
    assert settings.capital_fraction == 0.05
    assert settings.ensemble_model == "ncep_gefs025"


def test_decision_sources_do_not_call_a_model_or_hold_a_key():
    banned = ("sklearn", "openai", "torch", "private_key", "last_trade_price")
    decision_dirs = {"model", "signals", "risk", "execution", "markets", "weather"}
    key_holders = {"wallet.py", "live.py"}
    for path in (ROOT / "src" / "weatherbot").rglob("*.py"):
        if path.name == "settings.py":
            continue
        text = path.read_text(encoding="utf-8").lower()
        for word in banned:
            if word == "private_key" and path.name in key_holders:
                continue
            assert word not in text, f"{path.name} contains {word}"
        if path.parent.name in decision_dirs and "telegram" in text:
            raise AssertionError(f"{path} mentions telegram")


class _LiveBook:
    def __init__(self) -> None:
        self.orders: list[dict] = []
        self.posted: list[object] = []

    def create_order(self, order: dict) -> dict:
        self.orders.append(order)
        return {"signed": True, "order": order}

    def post_order(self, signed: dict) -> dict:
        self.posted.append(signed)
        return {"success": True}


def _wallet_file(path: Path, *, key: str, funder: str, mode: str = "live", host: str = "https://clob.example.test") -> None:
    path.write_text(
        "\n".join(
            (
                f"TRADING_MODE={mode}",
                f"POLYMARKET_PRIVATE_KEY={key}",
                f"POLYMARKET_FUNDER_ADDRESS={funder}",
                "POLYMARKET_SIGNATURE_TYPE=2",
                "POLYMARKET_CHAIN_ID=137",
                f"POLYMARKET_CLOB_HOST={host}",
                "",
            )
        ),
        encoding="utf-8",
    )


def test_paper_scan_ignores_the_wallet(tmp_path: Path) -> None:
    wallet = tmp_path / "wallet.env"
    _wallet_file(wallet, key="test-signer", funder="0x0000000000000000000000000000000000000001", mode="live")
    db = Database(str(tmp_path / "paper.sqlite"))
    http = FakeHttp(_books())

    def opener(_wallet: object) -> object:
        raise AssertionError("paper mode opened the wallet")

    run_scan(
        http,
        db,
        [_station()],
        _settings(db.path),
        NOW,
        trading_mode="paper",
        wallet_env=str(wallet),
        open_live_client=opener,
    )
    assert db.cash_usd() == pytest.approx(975)
    assert len(db.order_rows()) == 2
    assert db.conn.execute("SELECT COUNT(*) AS n FROM live_orders").fetchone()["n"] == 0
    db.close()


def test_missing_live_key_stops_before_any_order(tmp_path: Path) -> None:
    wallet = tmp_path / "wallet.env"
    _wallet_file(wallet, key="", funder="")
    db = Database(str(tmp_path / "paper.sqlite"))
    http = FakeHttp(_books())

    def opener(_wallet: object) -> object:
        raise AssertionError("live client opened without a key")

    with pytest.raises(LiveConfigError, match="no order was sent"):
        run_scan(
            http,
            db,
            [_station()],
            _settings(db.path),
            NOW,
            trading_mode="live",
            wallet_env=str(wallet),
            open_live_client=opener,
        )
    assert http.gets == []
    assert http.posts == []
    db.close()


def test_live_scan_signs_and_posts_without_moving_paper_cash(tmp_path: Path) -> None:
    wallet = tmp_path / "wallet.env"
    _wallet_file(wallet, key="test-signer", funder="0x0000000000000000000000000000000000000001")
    db = Database(str(tmp_path / "paper.sqlite"))
    http = FakeHttp(_books())
    book = _LiveBook()
    opened: dict[str, object] = {}

    def opener(loaded: object) -> _LiveBook:
        opened["wallet"] = loaded
        return book

    summary = run_scan(
        http,
        db,
        [_station()],
        _settings(db.path),
        NOW,
        trading_mode="live",
        wallet_env=str(wallet),
        open_live_client=opener,
    )
    run_scan(
        http,
        db,
        [_station()],
        _settings(db.path),
        NOW,
        trading_mode="live",
        wallet_env=str(wallet),
        open_live_client=opener,
    )
    loaded = opened["wallet"]
    assert loaded.clob_host == "https://clob.example.test"  # type: ignore[attr-defined]
    assert loaded.signature_type == 2  # type: ignore[attr-defined]
    assert loaded.chain_id == 137  # type: ignore[attr-defined]
    assert loaded.funder_address == "0x0000000000000000000000000000000000000001"  # type: ignore[attr-defined]
    assert book.orders == [
        {"token_id": "token-fill", "price": pytest.approx(0.4), "size": pytest.approx(62.5), "side": "BUY"}
    ]
    assert book.posted == [{"signed": True, "order": book.orders[0]}]
    assert all(not url.endswith("/order") for url in http.posts)
    assert db.cash_usd() == pytest.approx(1000)
    assert db.position_rows() == []
    assert "live orders" in summary
    assert "filled sent: 1" in summary
    db.close()


def test_live_entry_uses_the_yaml_bankroll_and_not_a_larger_paper_balance(tmp_path: Path) -> None:
    wallet = tmp_path / "wallet.env"
    _wallet_file(wallet, key="test-signer", funder="0x0000000000000000000000000000000000000001")
    db = Database(str(tmp_path / "paper.sqlite"))
    db.ensure_account(1000, "2026-10-20T16:00:00Z")
    http = FakeHttp(_books())
    book = _LiveBook()
    settings = replace(_settings(db.path), capital_usd=200, max_order_usd=25, capital_fraction=0.05)
    run_scan(
        http,
        db,
        [_station()],
        settings,
        NOW,
        trading_mode="live",
        wallet_env=str(wallet),
        open_live_client=lambda _wallet: book,
    )
    assert book.orders == [
        {"token_id": "token-fill", "price": pytest.approx(0.4), "size": pytest.approx(25), "side": "BUY"}
    ]
    assert db.cash_usd() == pytest.approx(1000)
    db.close()


def test_live_does_not_spend_more_than_cash_left_under_the_cap(tmp_path: Path) -> None:
    wallet = tmp_path / "wallet.env"
    _wallet_file(wallet, key="test-signer", funder="0x0000000000000000000000000000000000000001")
    db = Database(str(tmp_path / "paper.sqlite"))
    db.ensure_account(1000, "2026-10-20T16:00:00Z")
    cursor = db.conn.execute(
        """
        INSERT INTO signals (
          station_id, city, settlement_station_id, valid_date, model_probability,
          decision, reason, created_at
        ) VALUES ('nyc', 'New York', 'KLGA', '2026-10-19', 1, 'accepted', 'accepted', '2026-10-20T16:00:00Z')
        """
    )
    db.conn.commit()
    db.record_live_order(
        signal_id=int(cursor.lastrowid),
        idempotency_key="already-sent",
        bucket_key="old-token",
        station_id="nyc",
        valid_date="2026-10-19",
        side="buy",
        limit_price=0.4,
        notional_usd=195,
        contracts=10,
        status="filled",
        reason="sent",
        created_at="2026-10-20T16:00:00Z",
    )
    http = FakeHttp(_books())
    book = _LiveBook()
    settings = replace(_settings(db.path), capital_usd=200, max_order_usd=25, capital_fraction=0.05)
    run_scan(
        http,
        db,
        [_station()],
        settings,
        NOW,
        trading_mode="live",
        wallet_env=str(wallet),
        open_live_client=lambda _wallet: book,
    )
    assert book.orders == []
    assert db.live_reserved_usd() == pytest.approx(195)
    assert db.cash_usd() == pytest.approx(1000)
    db.close()
