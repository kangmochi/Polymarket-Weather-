import inspect
from datetime import date

import pytest

from weatherbot.markets.buckets import parse_bucket
from weatherbot.model.probability import (
    bucket_probability,
    sigma_for_lead,
    temperatures_for_market,
)

LOWER = -80.0
UPPER = 60.0


def test_probability_signature_does_not_take_a_market_price():
    names = set(inspect.signature(bucket_probability).parameters)
    assert names == {"member_temperatures", "bucket", "sigma"}


def test_boundary_member_belongs_to_the_higher_bucket():
    lower = parse_bucket("56-57°F", lower_bound_c=LOWER, upper_bound_c=UPPER)
    higher = parse_bucket("58-59°F", lower_bound_c=LOWER, upper_bound_c=UPPER)
    assert bucket_probability([57.5], lower, sigma=0) == pytest.approx(0)
    assert bucket_probability([57.5], higher, sigma=0) == pytest.approx(1)


def test_zero_sigma_is_the_member_fraction():
    bucket = parse_bucket("60-61°F", lower_bound_c=LOWER, upper_bound_c=UPPER)
    probability = bucket_probability([60, 60, 60, 70], bucket, sigma=0)
    assert probability == pytest.approx(0.75)


def test_sigma_moves_mass_out_of_a_narrow_bucket():
    bucket = parse_bucket("20°C", lower_bound_c=LOWER, upper_bound_c=UPPER)
    tight = bucket_probability([20, 20], bucket, sigma=0)
    wide = bucket_probability([20, 20], bucket, sigma=3)
    assert tight == pytest.approx(1)
    assert wide < tight


def test_sigma_widens_with_lead_time_and_converts_to_fahrenheit():
    assert sigma_for_lead(0, "celsius", 1.0, 2.0) == pytest.approx(1)
    assert sigma_for_lead(24, "celsius", 1.0, 2.0) == pytest.approx(3)
    assert sigma_for_lead(96, "celsius", 1.0, 2.0) == pytest.approx(5)
    assert sigma_for_lead(0, "fahrenheit", 1.0, 2.0) == pytest.approx(1.8)


def test_tail_does_not_receive_mass_beyond_its_explicit_cap():
    higher = parse_bucket("30°C or higher", lower_bound_c=LOWER, upper_bound_c=UPPER)
    inside = bucket_probability([40], higher, sigma=0)
    beyond_cap = bucket_probability([200], higher, sigma=0)
    assert inside == pytest.approx(1)
    assert beyond_cap == pytest.approx(0)


def test_or_lower_includes_a_cold_member_and_stops_at_the_cap():
    lower = parse_bucket("30°C or below", lower_bound_c=LOWER, upper_bound_c=UPPER)
    assert bucket_probability([10], lower, sigma=0) == pytest.approx(1)
    assert bucket_probability([-100], lower, sigma=0) == pytest.approx(0)


def test_metar_floor_applies_only_on_the_observation_date():
    today = date(2026, 10, 20)
    tomorrow = date(2026, 10, 21)
    today_values = temperatures_for_market([10, 18], "celsius", today, 15, today)
    future_values = temperatures_for_market([10, 18], "celsius", tomorrow, 15, today)
    assert today_values == [15, 18]
    assert future_values == [10, 18]
