from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from weatherbot.errors import WeatherbotError
from weatherbot.timeutil import isoformat_z

CLOB_BOOKS = "https://clob.polymarket.com/books"
_CHUNK = 100


@dataclass(frozen=True)
class Book:
    """Top of book from the CLOB. Last trade price is not a field."""

    token_id: str
    best_bid: float | None
    best_ask: float | None
    bid_depth: float
    ask_depth: float
    spread: float | None
    min_order_size: float | None
    fetched_at: str


def fetch_books(http: Any, token_ids: list[str], now: datetime) -> dict[str, Book]:
    fetched_at = isoformat_z(now)
    found: dict[str, Book] = {}
    unique = list(dict.fromkeys(token_ids))
    for start in range(0, len(unique), _CHUNK):
        chunk = unique[start : start + _CHUNK]
        try:
            payload = http.post_json(CLOB_BOOKS, [{"token_id": token} for token in chunk])
        except WeatherbotError:
            raise
        except Exception as exc:
            raise WeatherbotError("book", str(exc)) from exc
        found.update(parse_books(payload, fetched_at=fetched_at))
    return found


def parse_books(payload: object, fetched_at: str) -> dict[str, Book]:
    if isinstance(payload, dict) and payload.get("error"):
        raise WeatherbotError("book", str(payload.get("error")))
    if not isinstance(payload, list):
        raise WeatherbotError("book", "books response was not a list")
    books: dict[str, Book] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        if item.get("error"):
            continue
        token_id = item.get("asset_id")
        if not isinstance(token_id, str) or not token_id:
            continue
        books[token_id] = parse_book(item, token_id=token_id, fetched_at=fetched_at)
    return books


def parse_book(payload: dict[str, Any], token_id: str, fetched_at: str) -> Book:
    bids = _levels(payload.get("bids"))
    asks = _levels(payload.get("asks"))
    best_bid, bid_depth = _best(bids, highest=True)
    best_ask, ask_depth = _best(asks, highest=False)
    spread = None
    if best_bid is not None and best_ask is not None:
        spread = float((best_ask - best_bid).quantize(Decimal("0.000001")))
    return Book(
        token_id=token_id,
        best_bid=None if best_bid is None else float(best_bid),
        best_ask=None if best_ask is None else float(best_ask),
        bid_depth=float(bid_depth),
        ask_depth=float(ask_depth),
        spread=spread,
        min_order_size=_optional_float(payload.get("min_order_size")),
        fetched_at=fetched_at,
    )


def _levels(value: object) -> list[tuple[Decimal, Decimal]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise WeatherbotError("book", "book side was not a list")
    levels: list[tuple[Decimal, Decimal]] = []
    for row in value:
        if not isinstance(row, dict):
            raise WeatherbotError("book", "book level was not an object")
        price = _decimal(row.get("price"), "price")
        size = _decimal(row.get("size"), "size")
        if size < 0:
            raise WeatherbotError("book", "negative size")
        levels.append((price, size))
    return levels


def _best(levels: list[tuple[Decimal, Decimal]], highest: bool) -> tuple[Decimal | None, Decimal]:
    if not levels:
        return None, Decimal("0")
    touch = max(price for price, _size in levels) if highest else min(price for price, _size in levels)
    depth = sum((size for price, size in levels if price == touch), Decimal("0"))
    return touch, depth


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise WeatherbotError("book", f"bad {name}")
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise WeatherbotError("book", f"bad {name}") from exc


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number
