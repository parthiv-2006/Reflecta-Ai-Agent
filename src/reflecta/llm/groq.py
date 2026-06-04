import os

from reflecta.llm import remote
from reflecta.llm.provider import (
    EmptyResponse,
    RateLimitError,
    RequestTooLarge,
    call_with_retry,
    strip_fences,
)

MODEL_FAST = "llama-3.1-8b-instant"
MODEL_HARD = "llama-3.3-70b-versatile"


def repair(prompt: str, *, model: str = MODEL_FAST, client=None) -> str:
    # Remote key-broker mode: route repair through the proxy when a reflecta
    # token is configured and no explicit SDK client was injected for testing.
    if client is None and remote.remote_enabled():
        return remote.complete(prompt, task="repair", model=model)

    if client is None:
        # Imported lazily so remote-mode users don't need the provider SDK.
        from groq import Groq

        client = Groq(api_key=os.environ["GROQ_API_KEY"])

    def _call():
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content
        except Exception as exc:
            s = str(exc)
            low = s.lower()
            # 413 "request too large" must be checked FIRST: Groq's 413 body
            # mentions "tokens per minute" and carries a rate_limit-ish code, so
            # the 429 heuristic below would otherwise misclassify it as a
            # retryable rate limit and waste the whole backoff budget on a
            # request that can never fit.
            if (
                "413" in s
                or "request too large" in low
                or "reduce your message size" in low
            ):
                raise RequestTooLarge(
                    s, provider=f"Groq (test repair, {model})"
                ) from exc
            if "429" in s or "rate" in low:
                raise RateLimitError(
                    s, provider=f"Groq (test repair, {model})"
                ) from exc
            raise

    raw = call_with_retry(_call)
    # A None/empty completion would otherwise be written to disk verbatim or
    # crash strip_fences; surface it as a clear EmptyResponse instead.
    if not raw:
        raise EmptyResponse("Groq returned an empty response")
    return strip_fences(raw)
