import pytest
from calc import divide

def test_divide_positive_numbers():
    """
    Test division with positive integers, ensuring a float result.
    Covers the successful division path (line 16).
    """
    assert divide(10, 2) == 5.0
    assert divide(7, 2) == 3.5

def test_divide_negative_numbers():
    """
    Test division with negative integers.
    Covers the successful division path (line 16).
    """
    assert divide(-10, 2) == -5.0
    assert divide(10, -2) == -5.0
    assert divide(-10, -2) == 5.0

def test_divide_by_zero_raises_value_error():
    """
    Test division by zero, expecting a ValueError with a specific message.
    Covers the conditional check (line 14) and the error raising path (line 15).
    """
    with pytest.raises(ValueError, match="cannot divide by zero"):
        divide(5, 0)
    with pytest.raises(ValueError, match="cannot divide by zero"):
        divide(-10, 0)