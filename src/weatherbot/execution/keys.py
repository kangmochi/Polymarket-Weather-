import hashlib


def idempotency_key(station_id: str, valid_date: str, token_id: str, side: str) -> str:
    """Stable across rescans. A repeated scan of the same intent is the same key."""
    raw = "|".join((station_id, valid_date, token_id, side))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
