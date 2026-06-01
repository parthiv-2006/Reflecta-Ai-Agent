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
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


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
