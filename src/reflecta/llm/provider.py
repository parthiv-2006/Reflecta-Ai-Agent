import time


class RateLimitError(Exception):
    """Raised by provider clients when the API returns 429."""


class BudgetExhausted(Exception):
    """Raised when retries are exhausted due to repeated rate limiting."""


def call_with_retry(fn, *args, max_retries: int = 5, base_delay: float = 1.0, **kwargs):
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except RateLimitError:
            if attempt == max_retries:
                raise BudgetExhausted(f"rate-limited after {max_retries} retries")
            time.sleep(base_delay * (2**attempt))
