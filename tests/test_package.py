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
