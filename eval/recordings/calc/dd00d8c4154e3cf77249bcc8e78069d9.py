import pytest
from calc import power


def test_power_positive_integers():
    """Test power with positive integer base and exponent."""
    assert power(2, 3) == 8
    assert power(5, 2) == 25
    assert power(10, 4) == 10000


def test_power_zero_and_one_exponents():
    """Test power with exponent 0 or 1, and base 0 or 1."""
    assert power(7, 0) == 1
    assert power(0, 0) == 1  # Standard Python behavior
    assert power(0, 5) == 0
    assert power(1, 100) == 1
    assert power(10, 1) == 10


def test_power_negative_base():
    """Test power with a negative base."""
    assert power(-2, 2) == 4
    assert power(-2, 3) == -8
    assert power(-3, 0) == 1


def test_power_float_exponents():
    """Test power with floating-point exponents."""
    assert power(4, 0.5) == 2.0
    assert power(8, 1/3) == pytest.approx(2.0)
    assert power(2.5, 2) == 6.25<ctrl63>