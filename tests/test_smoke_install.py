"""
Clean-install smoke test: verifies all three CLI sub-commands start without
import-time crashes and exit 0 when passed --help. No LLM calls, no .env needed.
"""

import subprocess
import sys


def test_run_help():
    r = subprocess.run(
        [sys.executable, "-m", "reflecta", "run", "--help"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "--path" in r.stdout


def test_clean_help():
    r = subprocess.run(
        [sys.executable, "-m", "reflecta", "clean", "--help"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "--path" in r.stdout


def test_report_help():
    r = subprocess.run(
        [sys.executable, "-m", "reflecta", "report", "--help"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "--path" in r.stdout
