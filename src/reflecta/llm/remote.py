"""Remote key-broker mode — run reflecta on the operator's keys, not the user's.

When a reflecta token is configured, the free-stack LLM calls (Gemini
generation, Groq repair) are routed through a hosted proxy that holds the real
provider keys, instead of calling the provider SDKs with a local key. This is
what turns reflecta into a product: end users run ``reflecta login`` once and
never obtain a Gemini/Groq key of their own.

Crucially, **only prompt text crosses the wire** — the user's code is still
generated, run, and measured entirely on the user's machine. The operator never
executes untrusted code; they only broker (and meter) LLM calls.

Proxy contract — a single endpoint::

    POST {proxy_url}/v1/complete
    Authorization: Bearer <reflecta_token>
    {"task": "generate"|"repair", "prompt": "...", "model": "..."}
      -> 200 {"text": "..."}
         401/403 auth failure
         429    quota exceeded  (mapped to RateLimitError -> backoff ->
                                 BudgetExhausted, reusing the existing wrapper)

Credential resolution (first hit wins):
  1. ``REFLECTA_TOKEN`` environment variable
  2. ``<config_dir>/credentials`` written by ``reflecta login``
where ``<config_dir>`` is ``REFLECTA_CONFIG_DIR`` or ``~/.reflecta``.
"""

import json
import os
from pathlib import Path

from reflecta.llm.provider import (
    EmptyResponse,
    RateLimitError,
    call_with_retry,
    strip_fences,
)

# Placeholder default — change this to your deployed proxy, or override per-run
# with the REFLECTA_PROXY_URL env var. Baked in so end users need zero config.
DEFAULT_PROXY_URL = "https://api.reflecta.dev"

# Wall-clock ceiling for a single proxy round-trip (generation prompts are big).
_REQUEST_TIMEOUT_S = 120.0


def config_dir() -> Path:
    """Directory holding reflecta's own config/credentials."""
    override = os.environ.get("REFLECTA_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".reflecta"


def credentials_path() -> Path:
    return config_dir() / "credentials"


def _read_credentials() -> dict:
    path = credentials_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def get_token() -> str | None:
    """Resolve the reflecta token: env var first, then the credentials file."""
    env = os.environ.get("REFLECTA_TOKEN")
    if env:
        return env.strip()
    token = _read_credentials().get("token")
    return token.strip() if token else None


def get_proxy_url() -> str:
    """Resolve the proxy URL: env var, then credentials file, then the default."""
    return (
        os.environ.get("REFLECTA_PROXY_URL")
        or _read_credentials().get("proxy_url")
        or DEFAULT_PROXY_URL
    )


def remote_enabled() -> bool:
    """True when a reflecta token is configured — remote mode takes precedence
    over any local provider keys."""
    return get_token() is not None


def save_credentials(token: str, *, proxy_url: str | None = None) -> Path:
    """Persist credentials to ``<config_dir>/credentials`` with 0600 perms."""
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    data: dict = {"token": token}
    if proxy_url:
        data["proxy_url"] = proxy_url
    path = credentials_path()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # best-effort on platforms without POSIX perms
    return path


def clear_credentials() -> bool:
    """Remove stored credentials. Returns True if a file was removed."""
    path = credentials_path()
    if path.exists():
        path.unlink()
        return True
    return False


def complete(
    prompt: str,
    *,
    task: str,
    model: str,
    http_client=None,
    token: str | None = None,
    proxy_url: str | None = None,
) -> str:
    """Route one LLM call through the proxy and return cleaned text.

    ``http_client`` is injectable for tests; it must expose
    ``post(url, json=..., headers=...) -> response`` where the response has
    ``status_code``, ``json()`` and ``text``. A 429 is mapped to
    ``RateLimitError`` so the shared backoff wrapper handles quota pushback
    exactly like a provider 429.
    """
    token = token or get_token()
    if not token:
        raise EnvironmentError(
            "No reflecta token configured. Run `reflecta login` (or set "
            "REFLECTA_TOKEN), or use your own provider keys."
        )
    url = (proxy_url or get_proxy_url()).rstrip("/") + "/v1/complete"
    headers = {
        "Authorization": f"Bearer {token}",
        "content-type": "application/json",
    }
    body = {"task": task, "prompt": prompt, "model": model}

    owns_client = http_client is None
    if owns_client:
        import httpx

        http_client = httpx.Client(timeout=httpx.Timeout(_REQUEST_TIMEOUT_S))

    def _call():
        resp = http_client.post(url, json=body, headers=headers)
        if resp.status_code == 429:
            raise RateLimitError(f"reflecta proxy quota exceeded: {resp.text[:200]}")
        if resp.status_code != 200:
            raise RuntimeError(
                f"reflecta proxy error {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json().get("text")

    try:
        raw = call_with_retry(_call)
    finally:
        if owns_client:
            http_client.close()

    if not raw:
        raise EmptyResponse("reflecta proxy returned an empty response")
    return strip_fences(raw)
