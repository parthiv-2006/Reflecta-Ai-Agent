from calc import multiply


def test_multiply_positive_integers():
    assert multiply(2, 3) == 6


def test_multiply_with_zero():
    assert multiply(5, 0) == 0
    assert multiply(0, -7) == 0


def test_multiply_negative_integers():
    assert multiply(-2, 3) == -6
    assert multiply(2, -3) == -6
    assert multiply(-2, -3) == 6