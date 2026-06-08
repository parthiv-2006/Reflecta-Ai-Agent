import pytest
from calc import clamp


def test_clamp_value_below_lower_bound():
    """Test when the value is less than the lower bound."""
    assert clamp(1, 5, 10) == 5
    assert clamp(-10, -5, 0) == -5
    assert clamp(0.5, 1.0, 2.0) == 1.0


def test_clamp_value_above_upper_bound():
    """Test when the value is greater than the upper bound."""
    assert clamp(12, 5, 10) == 10
    assert clamp(5, -5, 0) == 0
    assert clamp(2.5, 1.0, 2.0) == 2.0


def test_clamp_value_within_bounds():
    """Test when the value is strictly between the lower and upper bounds."""
    assert clamp(7, 5, 10) == 7
    assert clamp(-2, -5, 0) == -2
    assert clamp(1.5, 1.0, 2.0) == 1.5


def test_clamp_value_at_lower_bound():
    """Test when the value is exactly the lower bound."""
    assert clamp(5, 5, 10) == 5
    assert clamp(-5, -5, 0) == -5
    assert clamp(1.0, 1.0, 2.0) == 1.0


def test_clamp_value_at_upper_bound():
    """Test when the value is exactly the upper bound."""
    assert clamp(10, 5, 10) == 10
    assert clamp(0, -5, 0) == 0
    assert clamp(2.0, 1.0, 2.0) == 2.0


def test_clamp_with_equal_bounds():
    """Test clamping when the lower and upper bounds are the same."""
    assert clamp(3, 5, 5) == 5
    assert clamp(5, 5, 5) == 5
    assert clamp(7, 5, 5) == 5
    assert clamp(-1, 0, 0) == 0
    assert clamp(0, 0, 0) == 0
    assert clamp(1, 0, 0) == 0


def test_clamp_with_negative_and_positive_bounds():
    """Test with mixed negative and positive bounds."""
    assert clamp(-10, -5, 5) == -5
    assert clamp(0, -5, 5) == 0
    assert clamp(10, -5, 5) == 5
    assert clamp(-3.5, -2.0, 2.0) == -2.0
    assert clamp(3.5, -2.0, 2.0) == 2.0