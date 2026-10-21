"""Sign a live order with the wallet and post it to the CLOB host."""

from typing import Any

from weatherbot.execution.paper import ExecutionResult
from weatherbot.signals.decide import Decision
from weatherbot.storage.db import Database
from weatherbot.wallet import Wallet


class ClobSession:
    """Holds one authenticated CLOB client. The order is signed, then posted."""

    def __init__(self, wallet: Wallet) -> None:
        from py_clob_client_v2 import ClobClient, OrderArgs, OrderType, Side

        self._OrderArgs = OrderArgs
        self._OrderType = OrderType
        self._buy = Side.BUY
        self._sell = Side.SELL
        self._client = ClobClient(
            host=wallet.clob_host,
            chain_id=wallet.chain_id,
            key=wallet.private_key,
            signature_type=wallet.signature_type,
            funder=wallet.funder_address,
        )
        self._client.set_api_creds(self._client.create_or_derive_api_key())

    def create_order(self, order: dict[str, object]) -> object:
        side = self._buy if order["side"] == "BUY" else self._sell
        return self._client.create_order(
            self._OrderArgs(
                token_id=str(order["token_id"]),
                price=float(order["price"]),  # type: ignore[arg-type]
                size=float(order["size"]),  # type: ignore[arg-type]
                side=side,
            )
        )

    def post_order(self, signed: object) -> object:
        return self._client.post_order(signed, self._OrderType.GTC)


def build_live_client(wallet: Wallet) -> ClobSession:
    return ClobSession(wallet)


def execute_live(
    db: Database,
    signal_id: int,
    decision: Decision,
    client: Any,
    *,
    bucket_key: str,
    station_id: str,
    valid_date: str,
    token_id: str,
    depth: float,
    created_at: str,
) -> ExecutionResult:
    """Sign and post one accepted order. Short depth is stored and not sent."""
    if decision.decision != "accepted":
        raise ValueError("live execution only accepts research-accepted decisions")
    if (
        decision.side is None
        or decision.reference_price is None
        or decision.notional_usd is None
        or decision.contracts is None
        or decision.idempotency_key is None
    ):
        raise ValueError("accepted decision is missing order fields")
    if db.live_order_exists(decision.idempotency_key):
        db.mark_execution(signal_id, "skipped", "duplicate_order")
        return ExecutionResult(status="skipped", reason="duplicate_order")
    if decision.contracts > depth:
        db.record_live_order(
            signal_id=signal_id,
            idempotency_key=decision.idempotency_key,
            bucket_key=bucket_key,
            station_id=station_id,
            valid_date=valid_date,
            side=decision.side,
            limit_price=decision.reference_price,
            notional_usd=decision.notional_usd,
            contracts=decision.contracts,
            status="unfilled",
            reason="insufficient_depth",
            created_at=created_at,
        )
        return ExecutionResult(status="unfilled", reason="insufficient_depth")
    order = {
        "token_id": token_id,
        "price": decision.reference_price,
        "size": decision.contracts,
        "side": "BUY" if decision.side == "buy" else "SELL",
    }
    try:
        signed = client.create_order(order)
        client.post_order(signed)
    except Exception:
        db.mark_execution(signal_id, "skipped", "live_order_failed")
        print("live order failed; no key was printed", flush=True)
        return ExecutionResult(status="skipped", reason="live_order_failed")
    db.record_live_order(
        signal_id=signal_id,
        idempotency_key=decision.idempotency_key,
        bucket_key=bucket_key,
        station_id=station_id,
        valid_date=valid_date,
        side=decision.side,
        limit_price=decision.reference_price,
        notional_usd=decision.notional_usd,
        contracts=decision.contracts,
        status="filled",
        reason="sent",
        created_at=created_at,
    )
    return ExecutionResult(status="filled", reason="sent")
