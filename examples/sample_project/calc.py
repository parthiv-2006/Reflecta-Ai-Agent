"""A few pure arithmetic helpers — the easy, single-shot targets.

These have no external dependencies, so Gemini Flash usually drafts a passing,
coverage-raising test on the first try. They demonstrate Reflecta's happy path
before the harder ``pricing.quote`` target forces a Claude escalation.
"""


def add(a: int, b: int) -> int:
    return a + b


def subtract(a: int, b: int) -> int:
    return a - b


def multiply(a: int, b: int) -> int:
    return a * b


def divide(a: int, b: int) -> float:
    if b == 0:
        raise ValueError("cannot divide by zero")
    return a / b
