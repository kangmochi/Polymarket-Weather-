import argparse
from pathlib import Path

from weatherbot.http_client import HttpClient
from weatherbot.portfolio import show_portfolio
from weatherbot.scan import collect_weather, run_scan
from weatherbot.settings import load_settings
from weatherbot.stations import load_stations
from weatherbot.storage.db import Database
from weatherbot.timeutil import isoformat_z, parse_now


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="weatherbot")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "collect", "status", "portfolio"):
        command = sub.add_parser(name)
        command.add_argument("--stations", default="config/stations.yaml")
        command.add_argument("--paper", default="config/paper.yaml")
        command.add_argument("--now", default=None, help="UTC timestamp; default is the current time")
    args = parser.parse_args(argv)
    settings = load_settings(args.paper)
    stations = load_stations(args.stations)
    now = parse_now(args.now)
    db = Database(settings.database)
    try:
        if args.command == "status":
            db.ensure_account(settings.capital_usd, isoformat_z(now))
            print(db.summary())
            return 0
        http = HttpClient()
        if args.command == "portfolio":
            db.ensure_account(settings.capital_usd, isoformat_z(now))
            print(show_portfolio(http, db, now))
            return 0
        if args.command == "collect":
            collect_weather(http, db, stations, settings, now)
            print(db.summary())
            return 0
        telegram_env = str(Path(args.paper).resolve().parent / "telegram.env")
        print(run_scan(http, db, stations, settings, now, telegram_env=telegram_env))
        return 0
    finally:
        db.close()
