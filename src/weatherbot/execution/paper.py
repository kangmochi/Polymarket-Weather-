from dataclasses import dataclass

from weatherbot.signals.decide import Decision
from weatherbot.storage.db import Database


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    reason: str


def execute_paper(
    db: Database,
    signal_id: int,
    decision: Decision,
    *,
    bucket_key: str,
    station_id: str,
    city: str,
    valid_date: str,
    token_id: str,
    bucket_label: str,
    depth: float,
    created_at: str,
) -> ExecutionResult:
    """Apply an already accepted decision. Does not recompute probability or edge.

    Paper mode stores the result in SQLite and does not hold a signing key.
    """
    if decision.decision != "accepted":
        raise ValueError("paper execution only accepts research-accepted decisions")
    if (
        decision.side is None
        or decision.reference_price is None
        or decision.notional_usd is None
        or decision.contracts is None
        or decision.idempotency_key is None
    ):
        raise ValueError("accepted decision is missing order fields")
    try:
        status = db.fill_order(
            signal_id=signal_id,
            idempotency_key=decision.idempotency_key,
            bucket_key=bucket_key,
            station_id=station_id,
            city=city,
            valid_date=valid_date,
            token_id=token_id,
            bucket_label=bucket_label,
            side=decision.side,
            limit_price=decision.reference_price,
            notional_usd=decision.notional_usd,
            contracts=decision.contracts,
            depth=depth,
            created_at=created_at,
        )
    except LookupError as exc:
        reason = str(exc)
        db.mark_execution(signal_id, "skipped", reason)
        return ExecutionResult(status="skipped", reason=reason)
    return ExecutionResult(status=status, reason="insufficient_depth" if status == "unfilled" else "filled")


def touch_depth(side: str, bid_depth: float, ask_depth: float) -> float:
    if side == "buy":
        return ask_depth
    if side == "sell":
        return bid_depth
    raise ValueError(f"unknown side {side}")
