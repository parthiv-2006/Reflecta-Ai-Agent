import sys
import os
import pytest

# Adjust path to import 'calc' from the parent directory.
# This assumes a project structure like:
# project_root/
#   calc.py
#   tests/
#     test_calc_divide.py (this file)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from calc import divide


def test_divide_positive_integers_exact():
    """Test division with positive integers yielding an exact integer result."""
    assert divide(10, 2) == 5.0
    assert divide(100, 10) == 10.0


def test_divide_positive_float_result():
    """Test division with positive numbers yielding a float result."""
    assert divide(7, 2) == 3.5
    assert divide(1, 3) == pytest.approx(0.3333333333333333)


def test_divide_by_zero_raises_error():
    """Test that dividing by zero raises a ZeroDivisionError."""
    with pytest.raises(ZeroDivisionError, match="division by zero"):
        divide(10, 0)
    with pytest.raises(ZeroDivisionError, match="division by zero"):
        divide(-5, 0)
    with pytest.raises(ZeroDivisionError, match="division by zero"):
        divide(0, 0) # Even 0/0 raises ZeroDivisionError in this implementation


def test_divide_negative_numerator():
    """Test division where the numerator is negative."""
    assert divide(-10, 2) == -5.0
    assert divide(-7, 2) == -3.5


def test_divide_negative_denominator():
    """Test division where the denominator is negative."""
    assert divide(10, -2) == -5.0
    assert divide(7, -2) == -3.5


def test_divide_both_negative():
    """Test division where both numerator and denominator are negative."""
    assert divide(-10, -2) == 5.0
    assert divide(-7, -2) == 3.5


def test_divide_zero_numerator():
    """Test division where the numerator is zero and denominator is non-zero."""
    assert divide(0, 5) == 0.0
    assert divide(0, -5) == 0.0
    assert divide(0, 0.5) == 0.0


def test_divide_floats():
    """Test division with floating-point numbers."""
    assert divide(10.5, 2.5) == 4.2
    assert divide(1.0, 3.0) == pytest.approx(0.3333333333333333)
    assert divide(-10.0, 4.0) == -2.5