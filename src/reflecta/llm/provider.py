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
    """Raised by provider clients when the API returns 429.

    Carries the human ``provider`` label (e.g. "Gemini (test generation)") and
    the raw API message so the loop can tell the user exactly who rejected the
    call and why, instead of a generic "budget exhausted".
    """

    def __init__(self, message: str, *, provider: str = "the LLM provider") -> None:
        super().__init__(message)
        self.provider = provider
        self.raw = message


class BudgetExhausted(Exception):
    """Raised when retries are exhausted due to repeated rate limiting."""


class RequestTooLarge(Exception):
    """Raised when a provider rejects a request as too large (HTTP 413).

    Distinct from ``RateLimitError`` (429): a 413 means the *single request*
    exceeds the model's per-minute token budget (TPM), so retrying with backoff
    is futile — the payload must be shrunk or sent to a higher-TPM model.
    ``call_with_retry`` deliberately does NOT catch this, so it surfaces
    immediately to the caller that can re-trim or re-route.
    """

    def __init__(self, message: str, *, provider: str = "the LLM provider") -> None:
        super().__init__(message)
        self.provider = provider
        self.raw = message


class EmptyResponse(Exception):
    """Raised when a provider returns no text (e.g. a safety block or an empty
    completion). Distinguishes "the model gave us nothing" from a code bug so
    the loop can mark the target failed with a clear message instead of crashing
    on ``None.strip()``."""


def explain_rate_limit(message: str) -> str:
    """Turn a raw 429 message into an actionable, plain-English hint."""
    low = message.lower()
    if any(k in low for k in ("per day", "perday", "requests per day", "daily", "rpd")):
        return (
            "This is a DAILY quota cap. It resets at midnight Pacific (Google) "
            "or 24h after first use (Groq) — retry later today/tomorrow."
        )
    if any(k in low for k in ("per minute", "per-minute", "rpm", "tpm", "tokens per minute")):
        return "This is a PER-MINUTE rate limit. Wait ~60 seconds and re-run."
    return (
        "Free tiers cap both per-minute and per-day usage. Wait ~60s and re-run; "
        "if it keeps happening you've hit the daily cap — retry later."
    )


def call_with_retry(fn, *args, max_retries: int = 5, base_delay: float = 1.0, **kwargs):
    last: RateLimitError | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except RateLimitError as exc:
            last = exc
            if attempt == max_retries:
                provider = getattr(exc, "provider", "the LLM provider")
                waited = int(base_delay * (2 ** (max_retries) - 1))
                raw = (getattr(exc, "raw", "") or str(exc)).strip().replace("\n", " ")
                if len(raw) > 300:
                    raw = raw[:300] + "…"
                raise BudgetExhausted(
                    f"{provider} returned HTTP 429 (rate limited) on every attempt "
                    f"({max_retries + 1} tries over ~{waited}s of backoff). "
                    f"{explain_rate_limit(raw)} | API said: {raw}"
                ) from exc
            time.sleep(base_delay * (2**attempt))
    # Unreachable (loop either returns or raises), but keeps type-checkers happy.
    raise BudgetExhausted(str(last) if last else "rate limited")
