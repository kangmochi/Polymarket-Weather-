import re
from dataclasses import dataclass
from typing import Literal

from weatherbot.units import TemperatureUnit, celsius_to, unit_from_letter

TailKind = Literal["none", "or_lower", "or_higher"]

_NUM = r"(-?\d+(?:\.\d+)?)"
_UNIT = r"\s*(?:°|º)\s*([CFcf])"
_OR_LOWER = re.compile(rf"^{_NUM}{_UNIT}\s+or\s+(?:below|lower|less)$", re.IGNORECASE)
_OR_HIGHER = re.compile(rf"^{_NUM}{_UNIT}\s+or\s+(?:higher|above|more)$", re.IGNORECASE)
_RANGE = re.compile(rf"^{_NUM}\s*[-–—]\s*{_NUM}{_UNIT}$", re.IGNORECASE)
_RANGE_BOTH = re.compile(rf"^{_NUM}{_UNIT}\s*[-–—]\s*{_NUM}{_UNIT}$", re.IGNORECASE)
_EXACT = re.compile(rf"^{_NUM}{_UNIT}$", re.IGNORECASE)


@dataclass(frozen=True)
class Bucket:
    """Finite temperature interval that settles a whole-degree market bucket.

    Whole degrees map to the half-open window [n - 0.5, n + 0.5). Adjacent
    buckets therefore do not both claim a value that lands on the boundary.
    Tail buckets stop at the configured absolute bounds instead of infinity,
    so probability beyond those bounds is not assigned to the open bucket.
    """

    label: str
    unit: TemperatureUnit
    tail: TailKind
    bound_low: float
    bound_high: float

    def __post_init__(self) -> None:
        if not (self.bound_low < self.bound_high):
            raise ValueError(f"{self.label} needs a finite low < high bound")


def parse_bucket(label: str, *, lower_bound_c: float, upper_bound_c: float) -> Bucket:
    if not lower_bound_c < upper_bound_c:
        raise ValueError("tail bounds must have lower < upper")
    text = " ".join(label.strip().split())
    if not text:
        raise ValueError("empty bucket label")

    lower_match = _OR_LOWER.match(text)
    if lower_match:
        threshold, unit = _threshold(lower_match)
        far_low = celsius_to(unit, lower_bound_c)
        return Bucket(
            label=text,
            unit=unit,
            tail="or_lower",
            bound_low=far_low,
            bound_high=threshold + 0.5,
        )

    higher_match = _OR_HIGHER.match(text)
    if higher_match:
        threshold, unit = _threshold(higher_match)
        far_high = celsius_to(unit, upper_bound_c)
        return Bucket(
            label=text,
            unit=unit,
            tail="or_higher",
            bound_low=threshold - 0.5,
            bound_high=far_high,
        )

    both = _RANGE_BOTH.match(text)
    if both:
        start = _whole(both.group(1))
        end = _whole(both.group(3))
        unit_a = unit_from_letter(both.group(2))
        unit_b = unit_from_letter(both.group(4))
        if unit_a != unit_b:
            raise ValueError(f"{text} mixes temperature units")
        return _range_bucket(text, unit_a, start, end)

    ranged = _RANGE.match(text)
    if ranged:
        return _range_bucket(
            text,
            unit_from_letter(ranged.group(3)),
            _whole(ranged.group(1)),
            _whole(ranged.group(2)),
        )

    exact = _EXACT.match(text)
    if exact:
        degree = _whole(exact.group(1))
        unit = unit_from_letter(exact.group(2))
        return Bucket(
            label=text,
            unit=unit,
            tail="none",
            bound_low=degree - 0.5,
            bound_high=degree + 0.5,
        )

    raise ValueError(f"unrecognized bucket label {label!r}")


def _range_bucket(label: str, unit: TemperatureUnit, start: int, end: int) -> Bucket:
    if end < start:
        raise ValueError(f"{label} has a descending range")
    return Bucket(
        label=label,
        unit=unit,
        tail="none",
        bound_low=start - 0.5,
        bound_high=end + 0.5,
    )


def _threshold(match: re.Match[str]) -> tuple[int, TemperatureUnit]:
    return _whole(match.group(1)), unit_from_letter(match.group(2))


def _whole(raw: str) -> int:
    value = float(raw)
    if abs(value - round(value)) > 1e-9:
        raise ValueError(f"{raw} is not a whole degree")
    return int(round(value))
