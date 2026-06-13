"""
Clean-install smoke test: verifies all three CLI sub-commands start without
import-time crashes and exit 0 when passed --help. No LLM calls, no .env needed.
"""

import os
import re
import subprocess
import sys

# Rich/Typer renders --help into a bordered panel whose column widths depend on
# the detected terminal size, and it interleaves ANSI styling codes with the
# text. In CI (no TTY) the option name can wrap or be split by escape codes, so
# a substring check against the raw stdout is fragile. We force a wide width so
# nothing wraps and strip ANSI before asserting — the test then checks intent
# (the command renders its --path option) independent of the render environment.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _help(command: str) -> str:
    env = {**os.environ, "COLUMNS": "200"}
    r = subprocess.run(
        [sys.executable, "-m", "reflecta", command, "--help"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0, r.stderr
    return _ANSI.sub("", r.stdout)


def test_run_help():
    assert "--path" in _help("run")


def test_clean_help():
    assert "--path" in _help("clean")


def test_report_help():
    assert "--path" in _help("report")
