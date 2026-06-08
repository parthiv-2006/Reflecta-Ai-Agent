"""Partial test suite for calc.py.

Covers: add, subtract, multiply (3/6 functions).
Leaves uncovered: divide, power, clamp — these are the generation targets.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calc import add, subtract, multiply


def test_add_integers():
    assert add(2, 3) == 5


def test_add_negative():
    assert add(-1, -4) == -5


def test_add_zero():
    assert add(0, 7) == 7


def test_subtract_basic():
    assert subtract(10, 4) == 6


def test_subtract_negative_result():
    assert subtract(3, 7) == -4


def test_multiply_positive():
    assert multiply(4, 5) == 20


def test_multiply_by_zero():
    assert multiply(9, 0) == 0


def test_multiply_negative():
    assert multiply(-3, 4) == -12
