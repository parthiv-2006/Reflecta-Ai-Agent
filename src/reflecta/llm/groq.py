import os

from groq import Groq

from reflecta.llm.provider import (
    EmptyResponse,
    RateLimitError,
    call_with_retry,
    strip_fences,
)

MODEL_FAST = "llama-3.1-8b-instant"
MODEL_HARD = "llama-3.3-70b-versatile"


def repair(prompt: str, *, model: str = MODEL_FAST, client=None) -> str:
    if client is None:
        client = Groq(api_key=os.environ["GROQ_API_KEY"])

    def _call():
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content
        except Exception as exc:
            if "429" in str(exc) or "rate" in str(exc).lower():
                raise RateLimitError(str(exc)) from exc
            raise

    raw = call_with_retry(_call)
    # A None/empty completion would otherwise be written to disk verbatim or
    # crash strip_fences; surface it as a clear EmptyResponse instead.
    if not raw:
        raise EmptyResponse("Groq returned an empty response")
    return strip_fences(raw)
