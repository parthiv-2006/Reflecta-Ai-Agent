import time


def strip_fences(text: str) -> str:
    """Remove a leading ```lang fence and trailing ``` from an LLM response.

    Shared by the Gemini and Groq clients.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


class RateLimitError(Exception):
    """Raised by provider clients when the API returns 429."""


class BudgetExhausted(Exception):
    """Raised when retries are exhausted due to repeated rate limiting."""


class EmptyResponse(Exception):
    """Raised when a provider returns no text (e.g. a safety block or an empty
    completion). Distinguishes "the model gave us nothing" from a code bug so
    the loop can mark the target failed with a clear message instead of crashing
    on ``None.strip()``."""


def call_with_retry(fn, *args, max_retries: int = 5, base_delay: float = 1.0, **kwargs):
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except RateLimitError:
            if attempt == max_retries:
                raise BudgetExhausted(f"rate-limited after {max_retries} retries")
            time.sleep(base_delay * (2**attempt))
