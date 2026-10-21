import math

import pytest

from weatherbot.markets.buckets import parse_bucket

LOWER = -80.0
UPPER = 60.0


def _parse(label: str):
    return parse_bucket(label, lower_bound_c=LOWER, upper_bound_c=UPPER)


def test_exact_celsius_degree_is_a_one_degree_window():
    bucket = _parse("8°C")
    assert bucket.tail == "none"
    assert bucket.unit == "celsius"
    assert bucket.bound_low == pytest.approx(7.5)
    assert bucket.bound_high == pytest.approx(8.5)


def test_fahrenheit_range_includes_both_whole_degrees():
    bucket = _parse("56-57°F")
    assert bucket.tail == "none"
    assert bucket.unit == "fahrenheit"
    assert bucket.bound_low == pytest.approx(55.5)
    assert bucket.bound_high == pytest.approx(57.5)


def test_or_lower_and_or_higher_have_finite_bounds():
    lower = _parse("55°F or below")
    higher = _parse("74°F or higher")
    assert lower.tail == "or_lower"
    assert higher.tail == "or_higher"
    assert math.isfinite(lower.bound_low) and math.isfinite(lower.bound_high)
    assert math.isfinite(higher.bound_low) and math.isfinite(higher.bound_high)
    assert lower.bound_high == pytest.approx(55.5)
    assert lower.bound_low == pytest.approx(-112.0)
    assert higher.bound_low == pytest.approx(73.5)
    assert higher.bound_high == pytest.approx(140.0)


def test_metric_tail_labels_from_live_wording():
    below = _parse("13°C or below")
    above = _parse("35°C or higher")
    assert below.bound_high == pytest.approx(13.5)
    assert below.bound_low == pytest.approx(-80.0)
    assert above.bound_low == pytest.approx(34.5)
    assert above.bound_high == pytest.approx(60.0)


def test_degree_sign_on_both_ends_of_a_range():
    bucket = _parse("76°F-77°F")
    assert bucket.bound_low == pytest.approx(75.5)
    assert bucket.bound_high == pytest.approx(77.5)


def test_unknown_label_is_rejected():
    with pytest.raises(ValueError):
        _parse("warm")
