"""Human-written tests — deliberately partial.

Covers ``add`` and ``multiply`` only. ``subtract``, ``divide`` and the whole
of ``pricing`` are left uncovered so Reflecta has real gaps to close.
"""

from calc import add, multiply


def test_add():
    assert add(2, 3) == 5


def test_add_negatives():
    assert add(-1, 1) == 0


def test_multiply():
    assert multiply(4, 5) == 20
