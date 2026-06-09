import pytest
from calc import multiply

def test_multiply_positive_numbers():
    assert multiply(2, 3) == 6

def test_multiply_with_zero():
    assert multiply(5, 0) == 0

def test_multiply_negative_numbers():
    assert multiply(-2, -3) == 6

def test_multiply_positive_and_negative():
    assert multiply(2, -3) == -6