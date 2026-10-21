import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from weatherbot.errors import WeatherbotError

_MESSAGE_LIMIT = 3800


@dataclass(frozen=True)
class _QueuedOrder:
    city: str
    valid_date: str
    bucket_label: str
    side: str
    price: float
    notional_usd: float
    status: str


@dataclass(frozen=True)
class _QueuedSettlement:
    city: str
    valid_date: str
    bucket_label: str
    side: str
    result: str
    pnl_usd: float


@dataclass(frozen=True)
class TelegramNotifier:
    """Sends one digest after a scan. It does not choose orders or hold a wallet key."""

    token: str
    chat_id: str
    http: Any
    _pending: list[_QueuedOrder] = field(default_factory=list, compare=False, repr=False)
    _settled: list[_QueuedSettlement] = field(default_factory=list, compare=False, repr=False)

    def notify_order(
        self,
        *,
        city: str,
        valid_date: str,
        bucket_label: str,
        side: str,
        price: float,
        notional_usd: float,
        status: str,
    ) -> None:
        if status not in {"filled", "unfilled"}:
            return
        self._pending.append(
            _QueuedOrder(
                city=city,
                valid_date=valid_date,
                bucket_label=bucket_label,
                side=side,
                price=price,
                notional_usd=notional_usd,
                status=status,
            )
        )

    def notify_settlement(
        self,
        *,
        city: str,
        valid_date: str,
        bucket_label: str,
        side: str,
        result: str,
        pnl_usd: float,
    ) -> None:
        if result not in {"win", "loss"}:
            return
        self._settled.append(
            _QueuedSettlement(
                city=city,
                valid_date=valid_date,
                bucket_label=bucket_label,
                side=side,
                result=result,
                pnl_usd=pnl_usd,
            )
        )

    def flush(self) -> None:
        if self._pending:
            text = _digest(self._pending)
            self._pending.clear()
            for chunk in _chunks(text, _MESSAGE_LIMIT):
                self._send(chunk)
        if self._settled:
            text = _settlement_digest(self._settled)
            self._settled.clear()
            for chunk in _chunks(text, _MESSAGE_LIMIT):
                self._send(chunk)

    def _send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            self.http.post_json(
                url,
                {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
            )
        except WeatherbotError:
            print("telegram failed to send the order notice", flush=True)


def _digest(orders: list[_QueuedOrder]) -> str:
    filled = sum(1 for order in orders if order.status == "filled")
    unfilled = sum(1 for order in orders if order.status == "unfilled")
    lines = [f"Paper scan: {filled} filled, {unfilled} unfilled"]
    grouped: dict[tuple[str, str], list[_QueuedOrder]] = {}
    for order in orders:
        grouped.setdefault((order.city, order.valid_date), []).append(order)
    for (city, valid_date), city_orders in grouped.items():
        lines.append(f"{city} {valid_date}")
        for order in city_orders:
            verb = "buy" if order.side == "buy" else "sell"
            state = "filled" if order.status == "filled" else "unfilled"
            lines.append(
                f"{order.bucket_label} {verb} {order.price:.4f} ${order.notional_usd:.2f} {state}"
            )
    return "\n".join(lines)


def _settlement_digest(rows: list[_QueuedSettlement]) -> str:
    wins = sum(1 for row in rows if row.result == "win")
    losses = len(rows) - wins
    pnl = sum(row.pnl_usd for row in rows)
    lines = [f"Paper settlement: {wins} win, {losses} loss, pnl {pnl:+.2f}"]
    grouped: dict[tuple[str, str], list[_QueuedSettlement]] = {}
    for row in rows:
        grouped.setdefault((row.city, row.valid_date), []).append(row)
    for (city, valid_date), city_rows in grouped.items():
        lines.append(f"{city} {valid_date}")
        for row in city_rows:
            verb = "buy" if row.side == "buy" else "sell"
            outcome = "win" if row.result == "win" else "loss"
            lines.append(f"{row.bucket_label} {verb} {outcome} {row.pnl_usd:+.2f}")
    return "\n".join(lines)


def _chunks(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        extra = len(line) + (1 if current else 0)
        if current and size + extra > limit:
            chunks.append("\n".join(current))
            current = [line]
            size = len(line)
            continue
        current.append(line)
        size += extra
    if current:
        chunks.append("\n".join(current))
    return chunks


def load_notifier(path: str, http: Any) -> TelegramNotifier | None:
    file_values = _read_env_file(path)
    token = _first(os.environ.get("TELEGRAM_BOT_TOKEN", ""), file_values.get("TELEGRAM_BOT_TOKEN", ""))
    chat_id = _first(os.environ.get("TELEGRAM_CHAT_ID", ""), file_values.get("TELEGRAM_CHAT_ID", ""))
    if not token or not chat_id:
        return None
    return TelegramNotifier(token=token, chat_id=chat_id, http=http)


def parse_env_file(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _read_env_file(path: str) -> dict[str, str]:
    file = Path(path)
    if not file.is_file():
        return {}
    return parse_env_file(file.read_text(encoding="utf-8"))


def _first(*values: str) -> str:
    for value in values:
        text = value.strip()
        if text:
            return text
    return ""
