import pytest
from calc import power


def test_power_positive_integers():
    """Test power with positive bases and exponents."""
    assert power(2, 3) == 8
    assert power(5, 2) == 25
    assert power(10, 1) == 10


def test_power_zero_exponent():
    """Test power with any non-zero base raised to the power of zero."""
    assert power(5, 0) == 1
    assert power(-3, 0) == 1
    assert power(0.5, 0) == 1
    # Python's `0**0` is 1
    assert power(0, 0) == 1


def test_power_negative_base_even_exponent():
    """Test power with a negative base and an even exponent."""
    assert power(-2, 2) == 4
    assert power(-3, 4) == 81


def test_power_negative_base_odd_exponent():
    """Test power with a negative base and an odd exponent."""
    assert power(-2, 3) == -8
    assert power(-1, 5) == -1


def test_power_negative_exponent():
    """Test power with negative exponents, resulting in fractional values."""
    assert power(2, -1) == 0.5
    assert power(4, -2) == 0.0625  # 1 / 16
    assert power(10, -3) == 0.001
    assert power(0.5, -2) == 4.0  # 1 / (0.5**2) = 1 / 0.25 = 4


def test_power_fractional_exponent():
    """Test power with fractional exponents, leading to roots."""
    assert power(4, 0.5) == pytest.approx(2.0)  # Square root
    assert power(8, 1/3) == pytest.approx(2.0)  # Cube root
    assert power(16, 0.25) == pytest.approx(2.0) # Fourth root