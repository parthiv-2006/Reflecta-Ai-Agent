"""Environment / secrets loading.

Keys live in ``.env`` (gitignored). ``load_dotenv`` + ``require_api_keys``
turn a missing key into an actionable message that names the missing variable
rather than a raw KeyError from deep in an SDK constructor.
"""

import os
from pathlib import Path

REQUIRED_KEYS = ("GEMINI_API_KEY", "GROQ_API_KEY")


def load_dotenv(path: Path | None = None) -> None:
    """Load ``KEY=VALUE`` pairs from ``<path>/.env`` into ``os.environ``.

    Existing environment variables win (``setdefault``), so an explicitly
    exported key is never clobbered by the file. Missing file is a no-op.
    """
    env_file = (Path(path) if path is not None else Path.cwd()) / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        k = key.strip()
        v = value.strip().strip('"').strip("'")
        if not os.environ.get(k):  # missing or empty — .env wins
            os.environ[k] = v


def require_api_keys(
    keys: tuple[str, ...] = REQUIRED_KEYS, *, escalate: bool = False
) -> None:
    """Raise ``EnvironmentError`` naming the first missing key.

    Called before the loop so the failure is a clear preflight error rather than
    an opaque SDK traceback once generation starts. When ``escalate=True``,
    also checks for ANTHROPIC_API_KEY.
    """
    all_keys = keys + ("ANTHROPIC_API_KEY",) if escalate else keys
    for key in all_keys:
        if not os.environ.get(key):
            raise EnvironmentError(
                f"{key} is not set. Add it to your .env file (see .env.example) "
                "or export it in your shell before running reflecta."
            )


def require_credentials(*, escalate: bool = False) -> None:
    """Preflight credentials, accounting for remote key-broker mode.

    In remote mode (a reflecta token is configured) no provider keys are needed;
    the proxy holds them. Escalation still runs locally against Claude, so it
    requires ANTHROPIC_API_KEY regardless of mode. Otherwise fall back to the
    classic bring-your-own-key check for GEMINI/GROQ.
    """
    # Imported here to keep the provider/remote layer out of config import time.
    from reflecta.llm import remote

    if remote.remote_enabled():
        if escalate and not os.environ.get("ANTHROPIC_API_KEY"):
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is required for --escalate. Escalation runs "
                "locally against Claude and is not brokered by the reflecta proxy."
            )
        return
    require_api_keys(escalate=escalate)
