import os

from google import genai

from reflecta.llm.provider import RateLimitError, call_with_retry

MODEL = "gemini-2.5-flash"


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


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
    return _strip_fences(raw)


def generate_test_source(
    source_code: str, qualified_name: str, missing_lines: list[int]
) -> str:
    prompt = (
        "Write a pytest test file for the Python source below.\n\n"
        "RULES:\n"
        "- Output ONLY valid Python code. No markdown. No code fences. No explanation.\n"
        "- The first line must be an import statement.\n"
        f"- Import the target function directly: `from calc import {qualified_name}`\n"
        "- Write at least two test functions that exercise the target function with real assertions.\n"
        "- Do NOT use `assert True` or trivially-true assertions.\n\n"
        f"Source file (calc.py):\n{source_code}\n\n"
        f"Target function to cover: `{qualified_name}`\n"
        f"Missing line numbers in that function: {missing_lines}\n"
    )
    return generate(prompt)
