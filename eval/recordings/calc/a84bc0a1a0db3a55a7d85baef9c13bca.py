import pytest
from calc import clamp


@pytest.mark.parametrize("value, lo, hi, expected", [
    (1, 5, 10, 5),
    (-10, -5, 0, -5),
    (0.1, 0.5, 1.0, 0.5),
    (-100, -50, -25, -50),
])
def test_clamp_value_below_lower_bound(value, lo, hi, expected):
    """Test cases where the value is less than the lower bound (lo)."""
    assert clamp(value, lo, hi) == expected


@pytest.mark.parametrize("value, lo, hi, expected", [
    (15, 5, 10, 10),
    (5, -5, 0, 0),
    (1.5, 0.5, 1.0, 1.0),
    (100, 25, 50, 50),
])
def test_clamp_value_above_upper_bound(value, lo, hi, expected):
    """Test cases where the value is greater than the upper bound (hi)."""
    assert clamp(value, lo, hi) == expected


@pytest.mark.parametrize("value, lo, hi, expected", [
    (7, 5, 10, 7),
    (-7, -10, -5, -7),
    (0.5, 0.0, 1.0, 0.5),
    (25, 10, 30, 25),
    (0, -5, 5, 0),
])
def test_clamp_value_within_bounds(value, lo, hi, expected):
    """Test cases where the value is strictly between lo and hi."""
    assert clamp(value, lo, hi) == expected


@pytest.mark.parametrize("value, lo, hi, expected", [
    (5, 5, 10, 5),  # value == lo
    (-10, -10, -5, -10),  # value == lo (negative)
    (0.0, 0.0, 1.0, 0.0),  # value == lo (float)
])
def test_clamp_value_equal_to_lower_bound(value, lo, hi, expected):
    """Test cases where the value is equal to the lower bound (lo)."""
    assert clamp(value, lo, hi) == expected


@pytest.mark.parametrize("value, lo, hi, expected", [
    (10, 5, 10, 10),  # value == hi
    (-5, -10, -5, -5),  # value == hi (negative)
    (1.0, 0.0, 1.0, 1.0),  # value == hi (float)
])
def test_clamp_value_equal_to_upper_bound(value, lo, hi, expected):
    """Test cases where the value is equal to the upper bound (hi)."""
    assert clamp(value, lo, hi) == expected


@pytest.mark.parametrize("value, lo, hi, expected", [
    (5, 5, 5, 5),  # value == lo == hi
    (3, 5, 5, 5),  # value < lo (lo == hi)
    (7, 5, 5, 5),  # value > hi (lo == hi)
    (0.0, 0.0, 0.0, 0.0), # value == lo == hi (float)
    (-2, -2, -2, -2), # value == lo == hi (negative)
])
def test_clamp_with_equal_lo_hi(value, lo, hi, expected):
    """Test cases where lo and hi are equal, covering all three value positions."""
    assert clamp(value, lo, hi) == expected