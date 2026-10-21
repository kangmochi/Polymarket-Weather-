from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

FORBIDDEN_KEYS = frozenset(
    {
        "private_key",
        "wallet_private_key",
        "polygon_private_key",
        "api_key",
        "api_secret",
        "api_passphrase",
        "passphrase",
        "telegram_token",
        "telegram_bot_token",
        "bot_token",
        "secret",
    }
)


@dataclass(frozen=True)
class Settings:
    capital_usd: float
    max_order_usd: float
    min_order_usd: float
    min_edge: float
    max_spread: float
    forecast_days: int
    tail_lower_bound_c: float
    tail_upper_bound_c: float
    sigma_floor_c: float
    sigma_per_sqrt_day_c: float
    ensemble_model: str
    database: str


def load_settings(path: str) -> Settings:
    raw = _read_yaml(path)
    reject_forbidden_keys(raw)
    capital = _positive(raw, "capital_usd")
    max_order = _positive(raw, "max_order_usd")
    min_order = _non_negative(raw, "min_order_usd")
    min_edge = _non_negative(raw, "min_edge")
    max_spread = _positive(raw, "max_spread")
    if max_spread > 1:
        raise ValueError("max_spread must be at most 1")
    if min_edge > 1:
        raise ValueError("min_edge must be at most 1")
    forecast_days = raw.get("forecast_days")
    if not isinstance(forecast_days, int) or isinstance(forecast_days, bool) or forecast_days < 1:
        raise ValueError("forecast_days must be an integer >= 1")
    lower = _number(raw, "tail_lower_bound_c")
    upper = _number(raw, "tail_upper_bound_c")
    if not lower < upper:
        raise ValueError("tail bounds must be finite with lower < upper")
    model = raw.get("ensemble_model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("ensemble_model is required")
    database = raw.get("database")
    if not isinstance(database, str) or not database.strip():
        raise ValueError("database path is required")
    return Settings(
        capital_usd=capital,
        max_order_usd=max_order,
        min_order_usd=min_order,
        min_edge=min_edge,
        max_spread=max_spread,
        forecast_days=forecast_days,
        tail_lower_bound_c=lower,
        tail_upper_bound_c=upper,
        sigma_floor_c=_non_negative(raw, "sigma_floor_c"),
        sigma_per_sqrt_day_c=_non_negative(raw, "sigma_per_sqrt_day_c"),
        ensemble_model=model.strip(),
        database=database.strip(),
    )


def _read_yaml(path: str) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must be a mapping")
    return loaded


def reject_forbidden_keys(value: object) -> None:
    _reject_forbidden(value)


def _reject_forbidden(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().lower() in FORBIDDEN_KEYS:
                raise ValueError(f"refusing forbidden setting {key}")
            _reject_forbidden(child)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden(child)


def _number(raw: dict[str, Any], key: str) -> float:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    return float(value)


def _positive(raw: dict[str, Any], key: str) -> float:
    value = _number(raw, key)
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _non_negative(raw: dict[str, Any], key: str) -> float:
    value = _number(raw, key)
    if value < 0:
        raise ValueError(f"{key} must be >= 0")
    return value
