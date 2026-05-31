from calc import subtract

def test_subtract_positive_integers():
    assert subtract(5, 3) == 2
    assert subtract(10, 7) == 3
    assert subtract(100, 50) == 50
    assert subtract(1, 0) == 1

def test_subtract_negative_and_mixed_integers():
    assert subtract(-5, -3) == -2
    assert subtract(5, -3) == 8
    assert subtract(-5, 3) == -8
    assert subtract(0, 0) == 0
    assert subtract(0, 10) == -10
    assert subtract(-10, 0) == -10