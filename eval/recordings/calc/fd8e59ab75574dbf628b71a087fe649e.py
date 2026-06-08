import pytest
from calc import divide


def test_divide_basic():
    """Test basic division with positive numbers."""
    assert divide(10, 2) == 5.0


def test_divide_result_float():
    """Test that division returns a float."""
    assert divide(5, 2) == 2.5


def test_divide_negative_dividend():
    """Test division with negative dividend."""
    assert divide(-10, 2) == -5.0


def test_divide_negative_divisor():
    """Test division with negative divisor."""
    assert divide(10, -2) == -5.0


def test_divide_both_negative():
    """Test division with both negative."""
    assert divide(-10, -2) == 5.0


def test_divide_by_zero_raises_error():
    """Test that division by zero raises ZeroDivisionError."""
    with pytest.raises(ZeroDivisionError, match="division by zero"):
        divide(5, 0)


def test_divide_zero_dividend():
    """Test division of zero."""
    assert divide(0, 5) == 0.0


def test_divide_fractional_result():
    """Test division resulting in a fraction."""
    assert divide(1, 3) == pytest.approx(0.3333333333, rel=1e-9)