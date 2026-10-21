from datetime import datetime
from typing import Any

from weatherbot.errors import WeatherbotError
from weatherbot.execution.keys import idempotency_key
from weatherbot.execution.live import build_live_client, execute_live
from weatherbot.execution.paper import execute_paper, touch_depth
from weatherbot.markets.book import Book, fetch_books
from weatherbot.notify.telegram import TelegramNotifier, load_notifier
from weatherbot.markets.buckets import parse_bucket
from weatherbot.markets.discover import fetch_event_buckets
from weatherbot.model.probability import (
    bucket_probability,
    sigma_for_lead,
    temperatures_for_market,
)
from weatherbot.settings import Settings
from weatherbot.signals.decide import decide
from weatherbot.settlement import settle_expired
from weatherbot.stations import Station
from weatherbot.storage.db import Database
from weatherbot.timeutil import forecast_dates, isoformat_z, lead_hours, local_today
from weatherbot.wallet import LiveConfigError, require_live_wallet
from weatherbot.weather.ensemble import fetch_ensemble
from weatherbot.weather.metar import fetch_metar_today


_GAMMA_STOP_AFTER = 3


def _progress(message: str) -> None:
    print(message, flush=True)


def collect_weather(
    http: Any,
    db: Database,
    stations: list[Station],
    settings: Settings,
    now: datetime,
) -> None:
    """Store GFS members and today's METAR. Does not read markets or place orders."""
    fetched_at = isoformat_z(now)
    for station in stations:
        _progress(f"weather {station.city}")
        try:
            snapshot = fetch_ensemble(
                http,
                station,
                settings.ensemble_model,
                settings.forecast_days,
                now,
            )
        except WeatherbotError as exc:
            db.insert_fetch_error(fetched_at, exc.source, station.id, exc.detail)
            _progress(f"weather {station.city} failed: {exc.detail}")
            continue
        db.insert_ensemble(
            snapshot.fetched_at,
            station.id,
            snapshot.model,
            (
                (member.valid_date.isoformat(), member.member_name, member.temperature_c)
                for member in snapshot.members
            ),
        )
        _store_metar(http, db, station, now, fetched_at)


def run_scan(
    http: Any,
    db: Database,
    stations: list[Station],
    settings: Settings,
    now: datetime,
    telegram_env: str | None = None,
    trading_mode: str = "paper",
    wallet_env: str | None = None,
    open_live_client: Any | None = None,
) -> str:
    mode = trading_mode.strip().lower()
    if mode not in {"paper", "live"}:
        raise ValueError("TRADING_MODE must be paper or live")
    live_client = None
    if mode == "live":
        wallet = require_live_wallet(wallet_env or "config/wallet.env")
        opener = open_live_client or build_live_client
        try:
            live_client = opener(wallet)
        except LiveConfigError:
            raise
        except Exception as exc:
            raise LiveConfigError(
                "live mode could not sign in to the CLOB host; no order was sent"
            ) from exc
    db.ensure_account(settings.capital_usd, isoformat_z(now))
    notifier = load_notifier(telegram_env or "config/telegram.env", http)
    try:
        _progress(f"scan {len(stations)} stations")
        settle_expired(http, db, stations, now, notifier)
        collect_weather(http, db, stations, settings, now)
        gamma_failures = 0
        for index, station in enumerate(stations):
            _progress(f"market {station.city}")
            gamma_failures = _scan_station(
                http,
                db,
                station,
                settings,
                now,
                notifier,
                gamma_failures,
                mode,
                live_client,
            )
            if gamma_failures >= _GAMMA_STOP_AFTER and index + 1 < len(stations):
                _progress(
                    "markets stopped: gamma-api.polymarket.com failed "
                    f"{_GAMMA_STOP_AFTER} times in a row"
                )
                break
        return db.summary()
    finally:
        if notifier is not None:
            notifier.flush()


def _scan_station(
    http: Any,
    db: Database,
    station: Station,
    settings: Settings,
    now: datetime,
    notifier: TelegramNotifier | None,
    gamma_failures: int,
    mode: str,
    live_client: Any | None,
) -> int:
    created_at = isoformat_z(now)
    dates = forecast_dates(now, station.timezone, settings.forecast_days)
    listed = []
    for valid_date in dates:
        if gamma_failures >= _GAMMA_STOP_AFTER:
            break
        try:
            found = fetch_event_buckets(http, station, valid_date)
        except WeatherbotError as exc:
            db.insert_fetch_error(created_at, exc.source, station.id, exc.detail)
            _progress(f"market {station.city} {valid_date.isoformat()} failed: {exc.detail}")
            if exc.source == "http":
                gamma_failures += 1
            continue
        gamma_failures = 0
        listed.extend(found)
    if not listed:
        return gamma_failures
    try:
        books = fetch_books(http, [item.token_id for item in listed], now)
    except WeatherbotError as exc:
        db.insert_fetch_error(created_at, exc.source, station.id, exc.detail)
        _progress(f"book {station.city} failed: {exc.detail}")
        books = {}
    today = local_today(now, station.timezone)
    observed_max = db.metar_max_c(station.id, today.isoformat())
    weather_fetched_at = _latest_ensemble_time(db, station.id)
    for item in listed:
        try:
            bucket = parse_bucket(
                item.label,
                lower_bound_c=settings.tail_lower_bound_c,
                upper_bound_c=settings.tail_upper_bound_c,
            )
        except ValueError as exc:
            db.insert_fetch_error(created_at, "bucket", station.id, str(exc))
            continue
        member_c = db.latest_members(station.id, item.valid_date.isoformat())
        if not member_c:
            db.insert_fetch_error(
                created_at,
                "ensemble",
                station.id,
                f"no members for {item.valid_date.isoformat()}",
            )
            continue
        temperatures = temperatures_for_market(
            member_c,
            bucket.unit,
            item.valid_date,
            observed_max,
            today if observed_max is not None else None,
        )
        hours = lead_hours(now, item.valid_date, station.timezone)
        sigma = sigma_for_lead(
            hours,
            bucket.unit,
            settings.sigma_floor_c,
            settings.sigma_per_sqrt_day_c,
        )
        probability = bucket_probability(temperatures, bucket, sigma)
        book = books.get(item.token_id)
        fields = _signal_fields(
            station=station,
            valid_date=item.valid_date.isoformat(),
            condition_id=item.condition_id,
            token_id=item.token_id,
            label=bucket.label,
            bound_low=bucket.bound_low,
            bound_high=bucket.bound_high,
            tail=bucket.tail,
            probability=probability,
            sigma=sigma,
            member_count=len(temperatures),
            lead=hours,
            weather_fetched_at=weather_fetched_at,
            book=book,
            created_at=created_at,
        )
        if book is None:
            db.insert_signal(
                {
                    **fields,
                    "side": None,
                    "reference_price": None,
                    "edge": None,
                    "notional_usd": None,
                    "decision": "rejected",
                    "reason": "book_unavailable",
                }
            )
            continue
        valid_date = item.valid_date.isoformat()
        if mode == "live":
            available_cash = max(0.0, settings.capital_usd - db.live_reserved_usd())
            position_open = db.live_position_open(item.token_id)
            order_exists = db.live_order_exists
        else:
            available_cash = db.cash_usd()
            position_open = db.position_open(item.token_id)
            order_exists = db.order_exists
        decision = decide(
            probability=probability,
            book=book,
            max_spread=settings.max_spread,
            min_edge=settings.min_edge,
            capital_usd=settings.capital_usd,
            max_order_usd=settings.max_order_usd,
            capital_fraction=settings.capital_fraction,
            min_order_usd=settings.min_order_usd,
            cash_usd=available_cash,
            position_open=position_open,
            order_exists_for=lambda side, station_id=station.id, valid_date=valid_date, token_id=item.token_id: (
                order_exists(idempotency_key(station_id, valid_date, token_id, side))
            ),
            station_id=station.id,
            valid_date=valid_date,
        )
        signal_id = db.insert_signal(
            {
                **fields,
                "side": decision.side,
                "reference_price": decision.reference_price,
                "edge": decision.edge,
                "notional_usd": decision.notional_usd,
                "decision": decision.decision,
                "reason": decision.reason,
            }
        )
        if decision.decision != "accepted" or decision.side is None:
            continue
        depth = touch_depth(decision.side, book.bid_depth, book.ask_depth)
        if mode == "live":
            result = execute_live(
                db,
                signal_id,
                decision,
                live_client,
                bucket_key=item.token_id,
                station_id=station.id,
                valid_date=valid_date,
                token_id=item.token_id,
                depth=depth,
                created_at=created_at,
            )
        else:
            result = execute_paper(
                db,
                signal_id,
                decision,
                bucket_key=item.token_id,
                station_id=station.id,
                city=station.city,
                valid_date=valid_date,
                token_id=item.token_id,
                bucket_label=bucket.label,
                depth=depth,
                created_at=created_at,
            )
        if (
            notifier is not None
            and decision.reference_price is not None
            and decision.notional_usd is not None
        ):
            notifier.notify_order(
                city=station.city,
                valid_date=valid_date,
                bucket_label=bucket.label,
                side=decision.side,
                price=decision.reference_price,
                notional_usd=decision.notional_usd,
                status=result.status,
            )
    return gamma_failures


def _signal_fields(
    *,
    station: Station,
    valid_date: str,
    condition_id: str,
    token_id: str,
    label: str,
    bound_low: float,
    bound_high: float,
    tail: str,
    probability: float,
    sigma: float,
    member_count: int,
    lead: float,
    weather_fetched_at: str | None,
    book: Book | None,
    created_at: str,
) -> dict[str, object]:
    return {
        "weather_fetched_at": weather_fetched_at,
        "book_fetched_at": None if book is None else book.fetched_at,
        "station_id": station.id,
        "city": station.city,
        "settlement_station_id": station.settlement_station_id,
        "valid_date": valid_date,
        "condition_id": condition_id,
        "token_id": token_id,
        "bucket_label": label,
        "bound_low": bound_low,
        "bound_high": bound_high,
        "tail": tail,
        "model_probability": probability,
        "sigma": sigma,
        "member_count": member_count,
        "lead_hours": lead,
        "best_bid": None if book is None else book.best_bid,
        "best_ask": None if book is None else book.best_ask,
        "bid_depth": None if book is None else book.bid_depth,
        "ask_depth": None if book is None else book.ask_depth,
        "spread": None if book is None else book.spread,
        "created_at": created_at,
    }


def _store_metar(http: Any, db: Database, station: Station, now: datetime, fetched_at: str) -> None:
    try:
        observations = fetch_metar_today(http, station, now)
    except WeatherbotError as exc:
        db.insert_fetch_error(fetched_at, exc.source, station.id, exc.detail)
        return
    for observation in observations:
        db.insert_metar(
            fetched_at,
            station.id,
            observation.settlement_station_id,
            observation.observed_at,
            observation.local_date.isoformat(),
            observation.temperature_c,
            observation.raw_text,
        )


def _latest_ensemble_time(db: Database, station_id: str) -> str | None:
    row = db.conn.execute(
        "SELECT fetched_at FROM ensemble_fetches WHERE station_id = ? ORDER BY id DESC LIMIT 1",
        (station_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row["fetched_at"])
