from collections.abc import Callable
from dataclasses import dataclass

from weatherbot.execution.keys import idempotency_key
from weatherbot.markets.book import Book
from weatherbot.risk.sizing import contracts_for, order_notional


@dataclass(frozen=True)
class Decision:
    decision: str
    reason: str
    side: str | None
    reference_price: float | None
    edge: float | None
    notional_usd: float | None
    contracts: float | None
    idempotency_key: str | None


def decide(
    *,
    probability: float,
    book: Book,
    max_spread: float,
    min_edge: float,
    capital_usd: float,
    max_order_usd: float,
    min_order_usd: float,
    cash_usd: float,
    capital_fraction: float = 0.05,
    position_open: bool,
    order_exists_for: Callable[[str], bool],
    station_id: str,
    valid_date: str,
) -> Decision:
    """Spread is applied before any edge arithmetic. Prices stay out of probability."""
    blocked = _book_block(book, max_spread)
    if blocked is not None:
        return _rejected(blocked)

    buy_edge = probability - book.best_ask  # type: ignore[operator]
    sell_edge = book.best_bid - probability  # type: ignore[operator]
    if buy_edge >= sell_edge:
        side = "buy"
        edge = buy_edge
        price = float(book.best_ask)  # type: ignore[arg-type]
    else:
        side = "sell"
        edge = sell_edge
        price = float(book.best_bid)  # type: ignore[arg-type]

    if edge < min_edge:
        return Decision(
            decision="rejected",
            reason="edge_below_minimum",
            side=side if edge > 0 else None,
            reference_price=price if edge > 0 else None,
            edge=edge,
            notional_usd=None,
            contracts=None,
            idempotency_key=None,
        )

    key = idempotency_key(station_id, valid_date, book.token_id, side)
    if position_open:
        return _rejected("one_position_per_bucket", side=side, price=price, edge=edge, key=key)
    if order_exists_for(side):
        return _rejected("duplicate_order", side=side, price=price, edge=edge, key=key)

    notional = order_notional(
        probability,
        price,
        side,
        capital_usd,
        max_order_usd,
        capital_fraction,
    )
    if notional < min_order_usd:
        return _rejected("size_below_minimum", side=side, price=price, edge=edge, key=key)
    if cash_usd < notional:
        return _rejected(
            "insufficient_cash",
            side=side,
            price=price,
            edge=edge,
            key=key,
            notional=notional,
        )

    contracts = contracts_for(side, notional, price)
    minimum = book.min_order_size
    if minimum is not None and contracts + 1e-9 < minimum:
        return _rejected(
            "below_exchange_minimum",
            side=side,
            price=price,
            edge=edge,
            key=key,
            notional=notional,
        )

    return Decision(
        decision="accepted",
        reason="accepted",
        side=side,
        reference_price=price,
        edge=edge,
        notional_usd=notional,
        contracts=contracts,
        idempotency_key=key,
    )


def _book_block(book: Book, max_spread: float) -> str | None:
    if book.best_bid is None or book.best_ask is None or book.spread is None:
        return "book_incomplete"
    if book.best_bid > book.best_ask:
        return "crossed_book"
    if book.spread > max_spread + 1e-9:
        return "spread_too_wide"
    if not (0.0 < book.best_bid < 1.0 and 0.0 < book.best_ask < 1.0):
        return "price_out_of_range"
    return None


def _rejected(
    reason: str,
    side: str | None = None,
    price: float | None = None,
    edge: float | None = None,
    key: str | None = None,
    notional: float | None = None,
) -> Decision:
    return Decision(
        decision="rejected",
        reason=reason,
        side=side,
        reference_price=price,
        edge=edge,
        notional_usd=notional,
        contracts=None,
        idempotency_key=key,
    )
