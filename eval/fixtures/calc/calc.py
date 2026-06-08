# calc.py — 6 pure arithmetic functions, no imports.
# Used as an eval fixture: test_calc_partial.py covers add/subtract/multiply,
# leaving divide/power/clamp as known generation targets.


def add(a, b):
    """Return a + b."""
    return a + b


def subtract(a, b):
    """Return a - b."""
    return a - b


def multiply(a, b):
    """Return a * b."""
    return a * b


def divide(a, b):
    """Return a / b.  Raises ZeroDivisionError when b is 0."""
    if b == 0:
        raise ZeroDivisionError("division by zero")
    return a / b


def power(base, exp):
    """Return base ** exp."""
    return base**exp


def clamp(value, lo, hi):
    """Return value clamped to the closed interval [lo, hi]."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value
