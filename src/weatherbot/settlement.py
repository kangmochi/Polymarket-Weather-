"""Close paper positions after Polymarket resolves the bucket. No new orders."""

from datetime import date, datetime
from typing import Any

from weatherbot.errors import WeatherbotError
from weatherbot.markets.discover import event_url, parse_yes_resolutions
from weatherbot.notify.telegram import TelegramNotifier
from weatherbot.stations import Station
from weatherbot.storage.db import Database
from weatherbot.timeutil import isoformat_z, local_today


def settlement_result(
    side: str,
    contracts: float,
    notional_usd: float,
    yes_won: bool,
) -> tuple[str, float, float]:
    """Return result, cash credited, and pnl.

    A buy receives one dollar per contract when YES wins. A sell receives the
    contracts back when YES loses, because that returns the locked collateral
    and the premium that was never added to cash.
    """
    if side == "buy":
        payout = contracts if yes_won else 0.0
    elif side == "sell":
        payout = 0.0 if yes_won else contracts
    else:
        raise ValueError(f"unknown side {side}")
    pnl = payout - notional_usd
    won = (side == "buy" and yes_won) or (side == "sell" and not yes_won)
    return ("win" if won else "loss", payout, pnl)


def settle_expired(
    http: Any,
    db: Database,
    stations: list[Station],
    now: datetime,
    notifier: TelegramNotifier | None,
) -> None:
    by_id = {station.id: station for station in stations}
    grouped: dict[tuple[str, date], list[Any]] = {}
    for row in db.position_rows():
        station = by_id.get(str(row["station_id"]))
        if station is None:
            continue
        valid = date.fromisoformat(str(row["valid_date"]))
        if valid >= local_today(now, station.timezone):
            continue
        grouped.setdefault((station.id, valid), []).append(row)
    settled_at = isoformat_z(now)
    for (station_id, valid), rows in grouped.items():
        station = by_id[station_id]
        try:
            payload = http.get_json(event_url(station.slug_city, valid))
            resolutions = parse_yes_resolutions(payload, station, valid)
        except WeatherbotError as exc:
            db.insert_fetch_error(settled_at, exc.source, station.id, exc.detail)
            print(f"settlement {station.city} {valid.isoformat()} failed: {exc.detail}", flush=True)
            continue
        for row in rows:
            yes_won = resolutions.get(str(row["token_id"]))
            if yes_won is None:
                continue
            result, payout, pnl = settlement_result(
                str(row["side"]),
                float(row["contracts"]),
                float(row["notional_usd"]),
                yes_won,
            )
            recorded = db.record_settlement(
                str(row["bucket_key"]),
                yes_won=yes_won,
                result=result,
                payout_usd=payout,
                pnl_usd=pnl,
                settled_at=settled_at,
            )
            if not recorded:
                continue
            outcome = "win" if result == "win" else "loss"
            print(
                f"settlement {row['city']} {row['valid_date']} {row['bucket_label']} {outcome} {pnl:+.2f}",
                flush=True,
            )
            if notifier is not None:
                notifier.notify_settlement(
                    city=str(row["city"]),
                    valid_date=str(row["valid_date"]),
                    bucket_label=str(row["bucket_label"]),
                    side=str(row["side"]),
                    result=result,
                    pnl_usd=pnl,
                )
