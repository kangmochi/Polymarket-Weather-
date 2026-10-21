"""Paper portfolio marked at the current exit touch. No orders are sent."""

from datetime import datetime

from weatherbot.errors import WeatherbotError
from weatherbot.markets.book import Book, fetch_books
from weatherbot.storage.db import Database


def show_portfolio(http: object, db: Database, now: datetime) -> str:
    rows = db.position_rows()
    books: dict[str, Book] = {}
    book_error: str | None = None
    if rows:
        try:
            books = fetch_books(http, [str(row["token_id"]) for row in rows], now)
        except WeatherbotError as exc:
            book_error = exc.detail
    return render_portfolio(db, books, book_error)


def render_portfolio(
    db: Database,
    books: dict[str, Book],
    book_error: str | None = None,
) -> str:
    account = db.conn.execute(
        "SELECT starting_capital_usd, cash_usd FROM paper_account WHERE id = 1"
    ).fetchone()
    rows = sorted(
        db.position_rows(),
        key=lambda row: (str(row["city"]), str(row["valid_date"]), str(row["bucket_label"]), int(row["id"])),
    )
    lines = ["portfolio"]
    if account is None:
        lines.append("  no account yet")
        return "\n".join(lines)
    starting = float(account["starting_capital_usd"])
    cash = float(account["cash_usd"])
    if book_error:
        lines.append(f"  book failed: {book_error}")
    pnl_total = 0.0
    value_total = 0.0
    unmarked = 0
    rendered: list[str] = []
    for row in rows:
        book = books.get(str(row["token_id"]))
        pnl, value, mark = _mark(
            str(row["side"]),
            float(row["contracts"]),
            float(row["price"]),
            float(row["notional_usd"]),
            book,
        )
        if mark is None:
            unmarked += 1
        pnl_total += pnl
        value_total += value
        rendered.append(_line(row, mark, pnl if mark is not None else None))
    equity = cash + value_total
    lines.append(
        f"  capital {starting:.2f}  cash {cash:.2f}  equity {equity:.2f}  pnl {equity - starting:+.2f}"
    )
    if not rows:
        lines.append("  no positions")
    else:
        note = f"  {len(rows)} positions"
        if unmarked:
            note += f", {unmarked} unmarked (valued at entry notional)"
        lines.append(note)
        lines.extend(rendered)
        lines.append(f"  open pnl {pnl_total:+.2f}")
    _append_history(lines, db)
    return "\n".join(lines)


def _append_history(lines: list[str], db: Database) -> None:
    rows = db.settlement_rows()
    if not rows:
        return
    wins = sum(1 for row in rows if row["result"] == "win")
    realized = sum(float(row["pnl_usd"]) for row in rows)
    lines.append(f"history {wins} win, {len(rows) - wins} loss, pnl {realized:+.2f}")
    for row in rows:
        verb = "buy" if row["side"] == "buy" else "sell"
        outcome = "win" if row["result"] == "win" else "loss"
        lines.append(
            f"  {row['city']} {row['valid_date']} {row['bucket_label']} {verb} "
            f"{outcome} {float(row['pnl_usd']):+.2f}"
        )


def _mark(
    side: str,
    contracts: float,
    entry: float,
    notional: float,
    book: Book | None,
) -> tuple[float, float, float | None]:
    """Return pnl, current value, and the exit price.

    A buy is marked at the bid. A sell is marked at the ask. Missing touch
    keeps the locked notional and contributes no pnl.
    """
    if side == "buy":
        mark = None if book is None else book.best_bid
        if mark is None:
            return 0.0, notional, None
        return contracts * (mark - entry), contracts * mark, mark
    if side == "sell":
        mark = None if book is None else book.best_ask
        if mark is None:
            return 0.0, notional, None
        return contracts * (entry - mark), contracts * (1.0 - mark), mark
    return 0.0, notional, None


def _line(row: object, mark: float | None, pnl: float | None) -> str:
    side = "buy" if row["side"] == "buy" else "sell"  # type: ignore[index]
    mark_text = "-" if mark is None else f"{mark:.4f}"
    pnl_text = "n/a" if pnl is None else f"{pnl:+.2f}"
    return (
        f"  {row['city']} {row['valid_date']} {row['bucket_label']} {side} "  # type: ignore[index]
        f"{float(row['contracts']):.2f} @ {float(row['price']):.4f} "  # type: ignore[index]
        f"mark {mark_text} pnl {pnl_text}"
    )
