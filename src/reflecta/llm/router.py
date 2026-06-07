"""Generation router: cache → Gemini → Claude Haiku overflow.

All generation calls in generate.py go through here instead of calling
gemini.generate directly. This adds two capabilities without touching the
loop or repair layers:

1. Disk cache (llm/cache.py): avoids re-spending Gemini RPD on repeated
   runs of the same repo.
2. Claude Haiku overflow (llm/claude_generate.py): when Gemini exhausts its
   daily quota (BudgetExhausted), falls back to Claude so the run continues
   rather than stopping mid-repo.
"""

from __future__ import annotations

from pathlib import Path

from reflecta.llm import cache, claude_generate, gemini
from reflecta.llm.provider import BudgetExhausted


def generate(
    prompt: str,
    *,
    cache_dir: Path | None = None,
    client=None,
    claude_client=None,
) -> str:
    """Return generated test code for ``prompt``.

    Lookup order:
      1. Disk cache (``cache_dir``) — zero quota spent on a hit.
      2. Gemini Flash — primary provider.
      3. Claude Haiku — overflow when Gemini raises BudgetExhausted.

    ``client`` is forwarded to gemini.generate as ``client=`` (injectable for
    tests). ``claude_client`` is forwarded to claude_generate.generate.
    """
    cached = cache.get(prompt, cache_dir)
    if cached is not None:
        return cached

    try:
        result = gemini.generate(prompt, client=client)
    except BudgetExhausted:
        if claude_generate._overflow_used >= claude_generate.MAX_OVERFLOW:
            raise BudgetExhausted(
                f"Claude Haiku overflow cap reached ({claude_generate.MAX_OVERFLOW} calls). "
                "Increase REFLECTA_CLAUDE_OVERFLOW or re-run tomorrow when Gemini resets."
            )
        result = claude_generate.generate(prompt, client=claude_client)
        claude_generate._overflow_used += 1

    cache.put(prompt, result, cache_dir)
    return result
