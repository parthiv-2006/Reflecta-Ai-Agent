"""Tooling preflight: a target venv without coverage/pytest must be detected
and repaired (or reported), never allowed to read as a silent 0.0% baseline.

Root cause (Weave/python-service, 2026-06-11): the repo's venv had pytest but
no ``coverage`` package. Every ``<venv-python> -m coverage`` call failed with
ModuleNotFoundError into a swallowed stderr, no coverage.json appeared, and the
run reported baseline 0.0% with zero targets — looking like a clean empty repo.
"""

import sys
from unittest.mock import patch

import pytest

from reflecta.environment import preflight_tooling
from reflecta.loop import CoverageMeasurementError, _ensure_target_tooling


def test_own_interpreter_short_circuits():
    # coverage+pytest are reflecta deps — no subprocess check needed.
    installed, missing = preflight_tooling(sys.executable)
    assert installed == []
    assert missing == []


def test_missing_tool_is_installed_into_target_venv():
    calls = []

    def fake_preflight_imports(interpreter, roots):
        # Missing on first check, present after the (faked) pip install.
        return ["coverage"] if not calls else []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class P:
            returncode = 0
            stdout = b""
            stderr = b""

        return P()

    with (
        patch("reflecta.environment.preflight_imports", fake_preflight_imports),
        patch("reflecta.environment.subprocess.run", fake_run),
    ):
        installed, missing = preflight_tooling(r"C:\fake\venv\Scripts\python.exe")
    assert installed == ["coverage"]
    assert missing == []
    assert any("pip" in part for part in calls[0])


def test_no_auto_install_reports_missing():
    with patch(
        "reflecta.environment.preflight_imports", return_value=["coverage", "pytest"]
    ):
        installed, missing = preflight_tooling(
            r"C:\fake\venv\Scripts\python.exe", auto_install=False
        )
    assert installed == []
    assert missing == ["coverage", "pytest"]


def test_failed_install_raises_actionable_error():
    with patch(
        "reflecta.environment.preflight_tooling",
        return_value=([], ["coverage"]),
    ):
        with pytest.raises(EnvironmentError) as exc:
            _ensure_target_tooling(r"C:\fake\venv\Scripts\python.exe")
    assert "coverage" in str(exc.value)
    assert "pip install" in str(exc.value)


def test_measure_coverage_real_raises_when_no_report(tmp_path):
    # An interpreter without coverage produces no json report — that must be a
    # loud CoverageMeasurementError, never a silent (0.0, ...) baseline.
    from reflecta.loop import measure_coverage_real

    (tmp_path / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    bad_python = tmp_path / "not_python.exe"
    with pytest.raises(CoverageMeasurementError):
        with patch("reflecta.loop.subprocess.run") as run:

            class P:
                returncode = 1
                stdout = b""
                stderr = b"No module named coverage"

            run.return_value = P()
            measure_coverage_real(tmp_path, python_exe=str(bad_python))


def test_stale_json_does_not_mask_failure(tmp_path):
    # A coverage.json left by a previous successful run must not be re-read
    # when the current measurement fails.
    from reflecta.loop import coverage_paths, measure_coverage_real

    (tmp_path / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _, json_file = coverage_paths(tmp_path)
    json_file.write_text('{"totals": {"percent_covered": 88.0}}', encoding="utf-8")

    with pytest.raises(CoverageMeasurementError):
        with patch("reflecta.loop.subprocess.run") as run:

            class P:
                returncode = 1
                stdout = b""
                stderr = b"boom"

            run.return_value = P()
            measure_coverage_real(tmp_path, python_exe="bad")
    assert not json_file.exists()
