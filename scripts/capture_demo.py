#!/usr/bin/env python3
"""Capture a real Reflecta run + triage as plain text for the demo renderer.

Runs the pipeline in-process against ``examples/sample_project`` and writes the
transcripts to ``docs/_demo/{run,triage}.txt``. ``scripts/render_demo.py`` then
turns those into ``docs/demo.{png,gif,mp4}`` and ``docs/triage.png``.

Notes
-----
* All provider calls (Gemini generation, Groq repair, Claude escalation) are
  live, so the exact coverage deltas — and whether escalation succeeds in a
  given run — can vary. The committed ``run.txt`` is one real golden capture
  (Gemini → Groq 8B → Groq 70B → Claude Sonnet escalation succeeds;
  40%% → 100%%); re-running regenerates it from a fresh real run.
* ``TMP``/``TEMP`` are pointed at a repo-local folder. On Windows the default
  user temp dir is scanned by Defender, which intermittently locks the files
  ``run_test_isolated`` copies there and makes runs flake; a local temp dir
  avoids it.

Requires ``GEMINI_API_KEY`` + ``GROQ_API_KEY`` + ``ANTHROPIC_API_KEY`` (see
``.env``). Run from the repo root: ``python scripts/capture_demo.py``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

TMP = ROOT / ".tmp_reflecta"
TMP.mkdir(exist_ok=True)
os.environ["TMP"] = os.environ["TEMP"] = str(TMP)
os.environ.setdefault("COLUMNS", "110")

from reflecta.config import load_dotenv  # noqa: E402
from reflecta.loop import run_loop, triage_repo  # noqa: E402
from reflecta.report import write_report  # noqa: E402
from reflecta.ui import ReflectaUI  # noqa: E402

SAMPLE = ROOT / "examples" / "sample_project"
OUT = ROOT / "docs" / "_demo"


class Tee:
    """Write to a file and stdout at once so the capture is also visible live."""

    def __init__(self, path: Path):
        self._f = open(path, "w", encoding="utf-8")
        self._stdout = sys.stdout

    def write(self, s):
        self._f.write(s)
        self._stdout.write(s)

    def flush(self):
        self._f.flush()
        self._stdout.flush()

    def close(self):
        self._f.close()


def _clean_generated():
    rdir = SAMPLE / "tests" / "_reflecta"
    if rdir.exists():
        for f in rdir.glob("test_reflecta_*.py"):
            f.unlink()


def capture_triage():
    _clean_generated()
    tee = Tee(OUT / "triage.txt")
    old = sys.stdout
    sys.stdout = tee
    try:
        ui = ReflectaUI()
        ui.banner()
        plan = triage_repo(SAMPLE.resolve())
        ui.print_triage(plan, attempt_risky=False)
    finally:
        sys.stdout = old
        tee.close()


def capture_run():
    _clean_generated()
    load_dotenv()
    tee = Tee(OUT / "run.txt")
    old = sys.stdout
    sys.stdout = tee
    try:
        ui = ReflectaUI()
        ui.banner()
        report = run_loop(SAMPLE.resolve(), max_iters=10, escalate=True, ui=ui)
        write_report(report, SAMPLE / "reflecta-report.json")
        ui.summary(report, SAMPLE / "reflecta-report.json")
    finally:
        sys.stdout = old
        tee.close()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    capture_triage()
    capture_run()
    print(f"\nWrote {OUT / 'triage.txt'} and {OUT / 'run.txt'}")
    print(
        "Next: python scripts/render_demo.py --run docs/_demo/run.txt "
        "--triage docs/_demo/triage.txt --out docs"
    )


if __name__ == "__main__":
    main()
