import argparse
import sys
from pathlib import Path

from weatherbot.http_client import HttpClient
from weatherbot.portfolio import show_portfolio
from weatherbot.scan import collect_weather, run_scan
from weatherbot.settings import load_settings
from weatherbot.stations import load_stations
from weatherbot.storage.db import Database
from weatherbot.timeutil import isoformat_z, parse_now
from weatherbot.wallet import LiveConfigError, trading_mode

_COMMANDS = {
    "scan": "Scan using TRADING_MODE from wallet.env. The default is paper.",
    "paper": "Paper scan. Ignores the wallet and does not send an order.",
    "live": "Sign orders with the wallet and send them to the CLOB host.",
    "collect": "Store weather only.",
    "status": "Show the paper account.",
    "portfolio": "Show cash, equity, and open pnl.",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="weatherbot")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in _COMMANDS.items():
        command = sub.add_parser(name, help=help_text)
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
        config_dir = Path(args.paper).resolve().parent
        telegram_env = str(config_dir / "telegram.env")
        wallet_env = str(config_dir / "wallet.env")
        try:
            if args.command == "paper":
                mode = "paper"
            elif args.command == "live":
                mode = "live"
            else:
                mode = trading_mode(wallet_env)
            print(
                run_scan(
                    http,
                    db,
                    stations,
                    settings,
                    now,
                    telegram_env=telegram_env,
                    trading_mode=mode,
                    wallet_env=wallet_env,
                )
            )
        except LiveConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except ValueError as exc:
            if "TRADING_MODE" not in str(exc):
                raise
            print(str(exc), file=sys.stderr)
            return 2
        return 0
    finally:
        db.close()
