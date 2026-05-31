from calc import subtract


def test_subtract_positive_integers():
    assert subtract(5, 3) == 2
    assert subtract(10, 7) == 3
    assert subtract(100, 0) == 100


def test_subtract_negative_integers():
    assert subtract(-5, -3) == -2
    assert subtract(-10, -7) == -3
    assert subtract(0, -5) == 5


def test_subtract_mixed_and_equal_integers():
    assert subtract(5, -3) == 8
    assert subtract(-5, 3) == -8
    assert subtract(7, 7) == 0
    assert subtract(-4, -4) == 0
