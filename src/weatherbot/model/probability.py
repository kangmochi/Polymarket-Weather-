import math
from datetime import date

from weatherbot.markets.buckets import Bucket
from weatherbot.units import TemperatureUnit, celsius_to

_SIGMA_FLOOR = 1e-12


def sigma_for_lead(
    lead_hours: float,
    unit: TemperatureUnit,
    floor_c: float,
    per_sqrt_day_c: float,
) -> float:
    """Forecast scale that widens with the square root of lead time.

    The two Celsius coefficients are configuration, not a fitted model.
    """
    if floor_c < 0 or per_sqrt_day_c < 0:
        raise ValueError("sigma coefficients must be non-negative")
    days = max(lead_hours, 0.0) / 24.0
    sigma_c = floor_c + per_sqrt_day_c * math.sqrt(days)
    if unit == "celsius":
        return sigma_c
    if unit == "fahrenheit":
        return sigma_c * 9.0 / 5.0
    raise ValueError(f"unknown unit {unit}")


def temperatures_for_market(
    member_celsius: list[float],
    unit: TemperatureUnit,
    market_date: date,
    observed_max_c: float | None,
    observed_date: date | None,
) -> list[float]:
    """Convert ensemble members. Today's METAR is a floor, never a forecast.

    An observation is applied only when its local date is the market date.
    Future dates keep the raw ensemble value even if today's high is warmer.
    """
    values = [celsius_to(unit, temp) for temp in member_celsius]
    if (
        observed_max_c is None
        or observed_date is None
        or observed_date != market_date
    ):
        return values
    floor = celsius_to(unit, observed_max_c)
    return [max(temp, floor) for temp in values]


def bucket_probability(member_temperatures: list[float], bucket: Bucket, sigma: float) -> float:
    """Mean ensemble mass inside the bucket. No market price is an argument."""
    if not member_temperatures:
        raise ValueError("bucket probability needs at least one ensemble member")
    if sigma < 0:
        raise ValueError("sigma must be non-negative")
    total = 0.0
    for temperature in member_temperatures:
        total += _interval_mass(temperature, sigma, bucket.bound_low, bucket.bound_high)
    return total / len(member_temperatures)


def _interval_mass(center: float, sigma: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    if sigma <= _SIGMA_FLOOR:
        return 1.0 if low <= center < high else 0.0
    return _normal_cdf((high - center) / sigma) - _normal_cdf((low - center) / sigma)


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
