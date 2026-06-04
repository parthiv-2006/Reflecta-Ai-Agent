"""
limits.py — free-tier rate-limit facts + token budgeting. Single source of truth.

The free tiers cap usage on four axes: requests-per-minute (RPM),
requests-per-day (RPD), tokens-per-minute (TPM), and tokens-per-day (TPD). You
hit whichever ceiling comes first. The one that bites code-generation is **TPM**:
a single repair prompt (source + failing test + traceback) can exceed a small
model's per-minute token budget, and Groq rejects it with **HTTP 413 "request
too large"** — distinct from 429, and NOT fixable by waiting. The fix is to size
every request to the chosen model's TPM before sending.

Numbers verified 2026-06 from https://console.groq.com/docs/rate-limits and
https://ai.google.dev/gemini-api/docs/rate-limits — keep this the only place
they live so routing/trimming logic stays consistent.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelLimits:
    tpm: int  # tokens per minute
    rpm: int  # requests per minute
    rpd: int  # requests per day
    tpd: int  # tokens per day


# Groq free tier ("on_demand" service tier).
GROQ_FREE_LIMITS: dict[str, ModelLimits] = {
    "llama-3.1-8b-instant": ModelLimits(tpm=6_000, rpm=30, rpd=14_400, tpd=500_000),
    "llama-3.3-70b-versatile": ModelLimits(tpm=12_000, rpm=30, rpd=1_000, tpd=100_000),
}

# Gemini free tier. TPM is huge, so generation hits the *daily request* cap (RPD)
# long before TPM — that's the "wait until tomorrow" case, not a size problem.
GEMINI_FREE_LIMITS: dict[str, ModelLimits] = {
    "gemini-2.5-flash": ModelLimits(tpm=250_000, rpm=10, rpd=250, tpd=0),
}

# Conservative fallback TPM for an unknown model — assume the smallest tier.
_DEFAULT_TPM = 6_000

# Code/JSON tokenizes denser than prose; ~3.5 chars/token is a safe estimate
# (slightly *over*-counting tokens → we trim a little more → we stay under TPM).
_CHARS_PER_TOKEN = 3.5

# Fraction of a model's TPM we allow a single *request* to occupy. The remainder
# leaves room for the completion, which also counts against the same per-minute
# budget. 0.5 keeps request+response comfortably under TPM.
_REQUEST_TPM_FRACTION = 0.5


def estimate_tokens(text: str) -> int:
    """Rough, deliberately slightly-high token count for a string."""
    return int(len(text) / _CHARS_PER_TOKEN) + 1


def model_tpm(model: str) -> int:
    if model in GROQ_FREE_LIMITS:
        return GROQ_FREE_LIMITS[model].tpm
    if model in GEMINI_FREE_LIMITS:
        return GEMINI_FREE_LIMITS[model].tpm
    return _DEFAULT_TPM


def request_token_budget(model: str, *, fraction: float = _REQUEST_TPM_FRACTION) -> int:
    """Max tokens a single request to ``model`` should occupy on the free tier."""
    return int(model_tpm(model) * fraction)


def request_char_budget(model: str, *, fraction: float = _REQUEST_TPM_FRACTION) -> int:
    """Same budget expressed in characters, for trimming source/text directly."""
    return int(request_token_budget(model, fraction=fraction) * _CHARS_PER_TOKEN)
