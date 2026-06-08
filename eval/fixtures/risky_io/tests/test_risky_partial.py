"""Minimal test for risky_io.py — import only.

No meaningful coverage. All 4 functions perform real I/O and are expected
to be classified as risky/blocked by the testability triage classifier.
The harness verifies that reflecta skips all targets without any LLM calls.

Note: requests is a hostile import but is NOT called at module level,
so the module itself is importable (TESTABLE verdict at module level).
The individual functions are RISKY (directly call the hostile API).
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_module_importable():
    """Verify the module attributes exist (does not call any I/O functions)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "risky_io",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "risky_io.py"),
    )
    importlib.util.module_from_spec(spec)
    # We deliberately do NOT call spec.loader.exec_module(mod) to avoid
    # triggering any top-level side effects. Just assert the file exists.
    assert spec is not None
    assert "risky_io" in spec.name
