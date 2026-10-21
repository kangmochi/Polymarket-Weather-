"""Local wallet file. Paper mode does not read the signer key."""

from dataclasses import dataclass
from pathlib import Path

from weatherbot.notify.telegram import parse_env_file

_DEFAULT_HOST = "https://clob.polymarket.com"


class LiveConfigError(Exception):
    """Live mode stopped before any order was sent."""


@dataclass(frozen=True)
class Wallet:
    private_key: str
    funder_address: str
    signature_type: int
    chain_id: int
    clob_host: str

    def __repr__(self) -> str:
        return (
            "Wallet(private_key='***', "
            f"funder_address={self.funder_address!r}, "
            f"signature_type={self.signature_type}, "
            f"chain_id={self.chain_id}, "
            f"clob_host={self.clob_host!r})"
        )


def trading_mode(path: str) -> str:
    """Return paper or live. A missing file or a blank mode stays paper."""
    raw = _values(path).get("TRADING_MODE", "paper").strip().lower()
    if not raw:
        return "paper"
    if raw not in {"paper", "live"}:
        raise ValueError("TRADING_MODE must be paper or live")
    return raw


def require_live_wallet(path: str) -> Wallet:
    """Load the signer. Raise before any order when the key or funder is missing."""
    values = _values(path)
    private_key = values.get("POLYMARKET_PRIVATE_KEY", "").strip()
    funder = values.get("POLYMARKET_FUNDER_ADDRESS", "").strip()
    if not private_key or not funder:
        raise LiveConfigError(
            "live mode needs POLYMARKET_PRIVATE_KEY and POLYMARKET_FUNDER_ADDRESS; "
            "no order was sent"
        )
    signature_raw = values.get("POLYMARKET_SIGNATURE_TYPE", "2").strip() or "2"
    if signature_raw not in {"0", "1", "2"}:
        raise LiveConfigError(
            "POLYMARKET_SIGNATURE_TYPE must be 0, 1, or 2; no order was sent"
        )
    chain_raw = values.get("POLYMARKET_CHAIN_ID", "137").strip() or "137"
    try:
        chain_id = int(chain_raw)
    except ValueError as exc:
        raise LiveConfigError(
            "POLYMARKET_CHAIN_ID must be an integer; no order was sent"
        ) from exc
    host = values.get("POLYMARKET_CLOB_HOST", _DEFAULT_HOST).strip() or _DEFAULT_HOST
    if not host.startswith("https://"):
        raise LiveConfigError("POLYMARKET_CLOB_HOST must be an https URL; no order was sent")
    return Wallet(
        private_key=private_key,
        funder_address=funder,
        signature_type=int(signature_raw),
        chain_id=chain_id,
        clob_host=host.rstrip("/"),
    )


def _values(path: str) -> dict[str, str]:
    file = Path(path)
    if not file.is_file():
        return {}
    return parse_env_file(file.read_text(encoding="utf-8"))
