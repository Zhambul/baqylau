"""Small example test module."""


def add(left: int, right: int) -> int:
    """Return the sum of two integers."""
    return left + right


def is_even(value: int) -> bool:
    """Return whether an integer is even."""
    return value % 2 == 0


def test_add_positive_numbers() -> None:
    assert add(2, 3) == 5


def test_add_negative_numbers() -> None:
    assert add(-2, -3) == -5


def test_is_even() -> None:
    assert is_even(4)
    assert not is_even(5)
