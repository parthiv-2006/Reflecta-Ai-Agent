"""Disk-based generation cache keyed on sha256(prompt).

Avoids re-spending Gemini RPD on identical prompts across re-runs of the
same repo. Cache entries expire after 7 days (checked via mtime).

Layout: ``<cache_dir>/<first-32-hex-chars-of-sha256>.py``
"""

import hashlib
import time
from pathlib import Path

_TTL_SECONDS = 7 * 24 * 3600  # 7 days


def _key(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:32]


def get(prompt: str, cache_dir: Path | None) -> str | None:
    """Return cached result for ``prompt``, or None on miss / expiry."""
    if cache_dir is None:
        return None
    path = Path(cache_dir) / f"{_key(prompt)}.py"
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > _TTL_SECONDS:
        path.unlink(missing_ok=True)
        return None
    return path.read_text(encoding="utf-8")


def put(prompt: str, result: str, cache_dir: Path | None) -> None:
    """Store ``result`` for ``prompt`` in ``cache_dir``."""
    if cache_dir is None:
        return
    d = Path(cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{_key(prompt)}.py").write_text(result, encoding="utf-8")
