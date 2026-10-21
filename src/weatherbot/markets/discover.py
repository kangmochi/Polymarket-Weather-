import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import quote

from weatherbot.errors import WeatherbotError
from weatherbot.stations import Station
from weatherbot.timeutil import event_slug

GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
_TITLE = re.compile(
    r"^Highest temperature in (?P<city>.+) on (?P<month>[A-Za-z]+) (?P<day>\d+)\?$"
)
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


@dataclass(frozen=True)
class ListedBucket:
    """Identity of one YES bucket. Prices from Gamma are not copied onto this."""

    station_id: str
    city: str
    valid_date: date
    condition_id: str
    token_id: str
    label: str


def event_url(slug_city: str, valid_date: date) -> str:
    slug = event_slug(slug_city, valid_date)
    return f"{GAMMA_EVENTS}?slug={quote(slug)}"


def fetch_event_buckets(
    http: Any,
    station: Station,
    valid_date: date,
) -> tuple[ListedBucket, ...]:
    url = event_url(station.slug_city, valid_date)
    try:
        payload = http.get_json(url)
    except WeatherbotError:
        raise
    except Exception as exc:
        raise WeatherbotError("gamma", str(exc)) from exc
    return parse_event_buckets(payload, station=station, valid_date=valid_date)


def parse_yes_resolutions(
    payload: object,
    station: Station,
    valid_date: date,
) -> dict[str, bool]:
    """Map a YES token to whether that bucket won. Unresolved markets are omitted.

    This reads the closed contract result only. It is not an input to probability.
    """
    if not isinstance(payload, list):
        raise WeatherbotError("gamma", "events response was not a list")
    if not payload:
        return {}
    event = payload[0]
    if not isinstance(event, dict):
        raise WeatherbotError("gamma", "event was not an object")
    if event.get("closed") is not True:
        return {}
    _check_title(str(event.get("title") or ""), station, valid_date)
    markets = event.get("markets")
    if not isinstance(markets, list):
        raise WeatherbotError("gamma", "event has no markets")
    found: dict[str, bool] = {}
    for market in markets:
        if not isinstance(market, dict):
            continue
        yes_won = _resolved_yes(market)
        if yes_won is None:
            continue
        try:
            token_id = _yes_token(market)
        except WeatherbotError:
            continue
        found[token_id] = yes_won
    return found


def parse_event_buckets(
    payload: object,
    station: Station,
    valid_date: date,
) -> tuple[ListedBucket, ...]:
    if not isinstance(payload, list):
        raise WeatherbotError("gamma", "events response was not a list")
    if not payload:
        return ()
    event = payload[0]
    if not isinstance(event, dict):
        raise WeatherbotError("gamma", "event was not an object")
    if event.get("closed") is True:
        return ()
    _check_title(str(event.get("title") or ""), station, valid_date)
    markets = event.get("markets")
    if not isinstance(markets, list):
        raise WeatherbotError("gamma", "event has no markets")
    listed: list[ListedBucket] = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        if market.get("closed") is True or market.get("active") is False:
            continue
        if market.get("acceptingOrders") is False:
            continue
        label = market.get("groupItemTitle")
        if not isinstance(label, str) or not label.strip():
            continue
        token_id = _yes_token(market)
        condition_id = market.get("conditionId")
        if not isinstance(condition_id, str) or not condition_id:
            raise WeatherbotError("gamma", f"{label} has no condition id")
        listed.append(
            ListedBucket(
                station_id=station.id,
                city=station.city,
                valid_date=valid_date,
                condition_id=condition_id,
                token_id=token_id,
                label=label.strip(),
            )
        )
    return tuple(listed)


def _check_title(title: str, station: Station, valid_date: date) -> None:
    match = _TITLE.match(title.strip())
    if match is None:
        raise WeatherbotError("gamma", f"unexpected event title {title!r}")
    if match.group("city") != station.title_city:
        raise WeatherbotError("gamma", f"title city {match.group('city')!r} != {station.title_city}")
    month = _MONTHS.get(match.group("month").lower())
    if month != valid_date.month or int(match.group("day")) != valid_date.day:
        raise WeatherbotError("gamma", f"title date does not match {valid_date.isoformat()}")


def _resolved_yes(market: dict[str, Any]) -> bool | None:
    if str(market.get("umaResolutionStatus") or "").lower() != "resolved":
        return None
    outcomes = _json_list(market.get("outcomes"), "outcomes")
    prices = _json_list(market.get("outcomePrices"), "outcomePrices")
    if len(outcomes) != len(prices):
        return None
    yes_bit: int | None = None
    for outcome, price in zip(outcomes, prices):
        if str(outcome).strip().lower() != "yes":
            continue
        yes_bit = _exact_bit(price)
        break
    if yes_bit is None:
        return None
    return yes_bit == 1


def _exact_bit(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value in (0, 1) and float(value) == int(value):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text in {"0", "0.0", "1", "1.0"}:
            return 1 if text.startswith("1") else 0
    return None


def _yes_token(market: dict[str, Any]) -> str:
    outcomes = _json_list(market.get("outcomes"), "outcomes")
    tokens = _json_list(market.get("clobTokenIds"), "clobTokenIds")
    if len(outcomes) != len(tokens):
        raise WeatherbotError("gamma", "outcomes and token ids differ in length")
    for outcome, token in zip(outcomes, tokens):
        if str(outcome).strip().lower() == "yes":
            text = str(token).strip()
            if not text:
                break
            return text
    raise WeatherbotError("gamma", "YES token missing")


def _json_list(value: object, name: str) -> list[object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise WeatherbotError("gamma", f"{name} was not json") from exc
    if not isinstance(value, list):
        raise WeatherbotError("gamma", f"{name} was not a list")
    return value
