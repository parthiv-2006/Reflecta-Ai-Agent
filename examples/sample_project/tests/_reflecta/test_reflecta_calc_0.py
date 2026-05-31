from calc import multiply


def test_multiply_positive_numbers():
    assert multiply(5, 4) == 20


def test_multiply_mixed_signs():
    assert multiply(-3, 6) == -18


def test_multiply_by_zero():
    assert multiply(7, 0) == 0
