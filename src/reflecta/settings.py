"""settings.py — optional ``reflecta.toml`` for run/ci defaults.

A CI workflow should not have to spell out eight flags. ``reflecta.toml`` at the
repo root lets a project pin its preferences once; CLI flags still override it,
and the file is entirely optional (missing file → empty settings → built-in
defaults apply).

Layout — flat keys under ``[tool.reflecta]`` (pyproject-style) or at the top
level::

    [tool.reflecta]
    max_iters = 30
    mutation = true
    min_mutation_score = 0.6
    base_branch = "main"
    head_branch = "reflecta/auto-tests"

Only recognised keys are read; unknown keys are ignored so a typo can't crash a
run. This module loads and type-coerces — it never applies the values; the CLI
layers them under explicit flags.
"""

import tomllib
from pathlib import Path

# Recognised keys and their coercers. Mirrors run_loop / ci.submit parameters.
_KEYS = {
    "max_iters": int,
    "max_repairs": int,
    "max_llm_calls": int,
    "target_coverage": float,
    "stall_k": int,
    "skip_entrypoints": bool,
    "attempt_risky": bool,
    "mutation": bool,
    "min_mutation_score": float,
    "max_mutants": int,
    "python": str,
    # ci-only
    "base_branch": str,
    "head_branch": str,
}


class SettingsError(RuntimeError):
    """reflecta.toml exists but could not be parsed."""


def load_settings(repo_path: Path, filename: str = "reflecta.toml") -> dict:
    """Return the recognised settings from ``<repo_path>/<filename>``.

    Missing file → ``{}``. Values are coerced to their declared types; an
    unparseable file raises ``SettingsError`` (a silent empty dict would hide a
    real config mistake). Keys may live under ``[tool.reflecta]`` or at the top
    level; the table takes precedence when both are present.
    """
    path = Path(repo_path) / filename
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"could not parse {path}: {exc}") from exc

    table = data.get("tool", {}).get("reflecta")
    if not isinstance(table, dict):
        table = data

    out: dict = {}
    for key, cast in _KEYS.items():
        if key not in table:
            continue
        value = table[key]
        # bool must be checked before int (bool is a subclass of int).
        if cast is bool:
            out[key] = bool(value)
        else:
            out[key] = cast(value)
    return out


def resolve(cli_value, key: str, settings: dict, default):
    """Three-tier precedence: explicit CLI flag > reflecta.toml > built-in default.

    ``cli_value`` is the value typer parsed; ``None`` means "flag not given" for
    options whose typer default is ``None``. For such options we fall back to the
    file then the hard default.
    """
    if cli_value is not None:
        return cli_value
    return settings.get(key, default)
