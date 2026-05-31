"""Environment / secrets loading. HARDENING-0-9 §2.2.

Keys live in ``.env`` (gitignored) per CLAUDE.md hard rule 5. Nothing else in
the package loaded it, so a user who followed the README got a raw KeyError
from deep in an SDK constructor. ``load_dotenv`` + ``require_api_keys`` turn
that into an actionable message that names the missing variable.
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


def require_api_keys(keys: tuple[str, ...] = REQUIRED_KEYS) -> None:
    """Raise ``EnvironmentError`` naming the first missing key.

    Called before the loop so the failure is a clear preflight error rather than
    an opaque SDK traceback once generation starts.
    """
    for key in keys:
        if not os.environ.get(key):
            raise EnvironmentError(
                f"{key} is not set. Add it to your .env file (see .env.example) "
                "or export it in your shell before running reflecta."
            )
