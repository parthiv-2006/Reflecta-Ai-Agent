import importlib.metadata
import subprocess
import sys


def test_package_metadata():
    meta = importlib.metadata.metadata("reflecta")
    assert meta["Name"] == "reflecta"
    assert meta["Version"] == "0.1.0"
    assert "Parthiv Paul" in (meta["Author-email"] or "")


def test_entry_point_exists():
    eps = importlib.metadata.entry_points(group="console_scripts")
    names = [ep.name for ep in eps]
    assert "reflecta" in names


def test_help_exits_clean():
    result = subprocess.run(
        [sys.executable, "-m", "reflecta", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()


def test_core_import_does_not_pull_in_escalation(tmp_path):
    """AUDIT H3: importing the core loop must not import the escalation module
    (and its opt-in httpx dependency). Escalation is imported lazily, only when
    --escalate is actually used."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\n"
        "import reflecta.loop  # noqa: F401\n"
        "assert 'reflecta.escalate' not in sys.modules, (\n"
        "    'reflecta.loop must not eagerly import reflecta.escalate'\n"
        ")\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, str(probe)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
