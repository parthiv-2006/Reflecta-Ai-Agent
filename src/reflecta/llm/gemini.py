import os

from google import genai

from reflecta.llm.provider import (
    EmptyResponse,
    RateLimitError,
    call_with_retry,
    strip_fences,
)

MODEL = "gemini-2.5-flash"


def generate(prompt: str, *, client=None) -> str:
    if client is None:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    def _call():
        try:
            response = client.models.generate_content(model=MODEL, contents=prompt)
            return response.text
        except Exception as exc:
            if (
                "429" in str(exc)
                or "quota" in str(exc).lower()
                or "rate" in str(exc).lower()
            ):
                raise RateLimitError(str(exc)) from exc
            raise

    raw = call_with_retry(_call)
    # Gemini returns text=None on a safety block or empty candidate; guard
    # before strip_fences so we raise a clear error rather than AttributeError.
    if not raw:
        raise EmptyResponse("Gemini returned an empty response")
    return strip_fences(raw)
