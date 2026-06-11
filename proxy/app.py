"""reflecta proxy — broker LLM calls on the operator's keys.

A thin FastAPI service that the reflecta CLI calls in remote mode. It holds the
real Gemini/Groq keys, authenticates per-user reflecta tokens, meters usage
against a daily quota, and forwards prompts to the providers. It never receives
or runs user code — only prompt text.

Single endpoint::

    POST /v1/complete
    Authorization: Bearer <reflecta_token>
    {"task": "generate"|"repair", "prompt": "...", "model": "..."}
      -> 200 {"text": "..."}
         401 invalid/missing token
         400 unknown task / disallowed model
         413 prompt too large
         429 daily quota exceeded
         502 upstream provider error

Configuration (env):
  GEMINI_API_KEY, GROQ_API_KEY      provider keys (operator's)
  REFLECTA_TOKENS                   JSON {"tok": quota} or {"tok": {"daily_quota": n}},
                                    or a comma-separated list using the default quota
  REFLECTA_DEFAULT_DAILY_QUOTA      default per-token daily call cap (default 200)
  REFLECTA_MAX_PROMPT_CHARS         reject prompts larger than this (default 200000)

NOTE: metering is in-memory — fine for a single-process MVP, but a real
deployment with multiple workers needs a shared store (Redis/DB). See README.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

# task -> allowed models. Pinning the allowlist stops a caller from requesting a
# more expensive model on the operator's dime.
ALLOWLIST: dict[str, set[str]] = {
    "generate": {"gemini-2.5-flash"},
    "repair": {"llama-3.1-8b-instant", "llama-3.3-70b-versatile"},
}

DEFAULT_DAILY_QUOTA = 200
DEFAULT_MAX_PROMPT_CHARS = 200_000


class CompleteRequest(BaseModel):
    task: str
    prompt: str
    model: str


@dataclass
class ProxyConfig:
    tokens: dict[str, int]  # reflecta token -> daily call quota
    generate_fn: Callable[[str, str], str]  # (prompt, model) -> text
    repair_fn: Callable[[str, str], str]
    max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS


# ---------------------------------------------------------------------------
# Provider calls (lazy SDK imports so the app imports without keys installed)
# ---------------------------------------------------------------------------


def _gemini_generate(prompt: str, model: str) -> str:
    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    resp = client.models.generate_content(model=model, contents=prompt)
    text = resp.text
    if not text:
        raise RuntimeError("gemini returned empty response")
    return text


def _groq_repair(prompt: str, model: str) -> str:
    from groq import Groq

    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    resp = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}]
    )
    text = resp.choices[0].message.content
    if not text:
        raise RuntimeError("groq returned empty response")
    return text


# ---------------------------------------------------------------------------
# Token / quota config
# ---------------------------------------------------------------------------


def _parse_tokens(raw: str, default_quota: int) -> dict[str, int]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Comma-separated list of bare tokens, all sharing the default quota.
        return {t.strip(): default_quota for t in raw.split(",") if t.strip()}
    tokens: dict[str, int] = {}
    for tok, val in parsed.items():
        if isinstance(val, dict):
            tokens[tok] = int(val.get("daily_quota", default_quota))
        else:
            tokens[tok] = int(val)
    return tokens


def _config_from_env() -> ProxyConfig:
    default_quota = int(
        os.environ.get("REFLECTA_DEFAULT_DAILY_QUOTA", DEFAULT_DAILY_QUOTA)
    )
    return ProxyConfig(
        tokens=_parse_tokens(os.environ.get("REFLECTA_TOKENS", ""), default_quota),
        generate_fn=_gemini_generate,
        repair_fn=_groq_repair,
        max_prompt_chars=int(
            os.environ.get("REFLECTA_MAX_PROMPT_CHARS", DEFAULT_MAX_PROMPT_CHARS)
        ),
    )


# ---------------------------------------------------------------------------
# Metering (in-memory; per-token daily counter)
# ---------------------------------------------------------------------------


@dataclass
class _Meter:
    # token -> [iso-date, count]
    counts: dict[str, list] = field(default_factory=dict)

    def over_quota(self, token: str, quota: int) -> bool:
        today = date.today().isoformat()
        entry = self.counts.get(token)
        if entry is None or entry[0] != today:
            return False
        return entry[1] >= quota

    def record(self, token: str) -> None:
        today = date.today().isoformat()
        entry = self.counts.get(token)
        if entry is None or entry[0] != today:
            self.counts[token] = [today, 1]
        else:
            entry[1] += 1


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def create_app(config: ProxyConfig | None = None) -> FastAPI:
    config = config or _config_from_env()
    meter = _Meter()
    app = FastAPI(title="reflecta-proxy", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "tokens_configured": len(config.tokens)}

    @app.post("/v1/complete")
    def complete(body: CompleteRequest, authorization: str | None = Header(None)):
        token = _bearer(authorization)
        if not token or token not in config.tokens:
            raise HTTPException(
                status_code=401, detail="invalid or missing reflecta token"
            )

        if len(body.prompt) > config.max_prompt_chars:
            raise HTTPException(status_code=413, detail="prompt too large")

        if body.task not in ALLOWLIST:
            raise HTTPException(status_code=400, detail=f"unknown task: {body.task}")
        if body.model not in ALLOWLIST[body.task]:
            raise HTTPException(
                status_code=400,
                detail=f"model {body.model!r} not allowed for task {body.task!r}",
            )

        if meter.over_quota(token, config.tokens[token]):
            raise HTTPException(status_code=429, detail="daily quota exceeded")

        try:
            if body.task == "generate":
                text = config.generate_fn(body.prompt, body.model)
            else:
                text = config.repair_fn(body.prompt, body.model)
        except Exception as exc:  # upstream provider failure
            # Do NOT echo exc directly: provider SDK exceptions can include the
            # API key or full request details in their repr.  Log server-side
            # only (operator can see it); return a generic message to the caller.
            import logging as _logging
            _logging.getLogger("reflecta.proxy").warning(
                "provider error for task=%s model=%s: %s",
                body.task, body.model, exc,
            )
            raise HTTPException(status_code=502, detail="upstream provider error")

        meter.record(token)
        return {"text": text}

    return app


# Module-level app for `uvicorn proxy.app:app`.
app = create_app()
