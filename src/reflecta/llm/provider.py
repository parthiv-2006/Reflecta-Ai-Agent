import re
import time


_FENCE_RE = re.compile(r"```[ \t]*[a-zA-Z0-9_+-]*[ \t]*\r?\n(.*?)```", re.DOTALL)


def strip_fences(text: str) -> str:
    """Reassemble the code from an LLM response, dropping prose and fences.

    Shared by the Gemini and Groq clients. The model often wraps its answer in
    markdown and interleaves explanatory prose between *several* fenced blocks
    (e.g. one fence for imports/fixtures, another for the test bodies). A naive
    "first fence only" extraction silently truncated the file — dropping the
    imports and leaving dangling references like ``@mock.patch`` with no
    ``import``. We concatenate every fenced block, in order, so the file stays
    whole.
    """
    text = text.strip()

    blocks = [m.group(1).strip() for m in _FENCE_RE.finditer(text)]
    blocks = [b for b in blocks if b]
    if blocks:
        return "\n\n".join(blocks)

    # No closed fence matched. If the text opens with a fence (unterminated or
    # malformed), drop the opening/closing fence lines and keep the body.
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
