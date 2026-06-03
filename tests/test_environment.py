import sys

from reflecta.environment import (
    collect_third_party_roots,
    detect_interpreter,
    preflight_imports,
)


def test_detect_falls_back_to_sys_executable(tmp_path):
    # No virtualenv in the repo → use the interpreter running reflecta.
    assert detect_interpreter(tmp_path) == sys.executable


def test_detect_prefers_repo_venv(tmp_path):
    # Simulate a Windows-layout venv interpreter.
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    exe = scripts / "python.exe"
    exe.write_text("")
    assert detect_interpreter(tmp_path) == str(exe)


def test_detect_prefers_repo_venv_posix(tmp_path):
    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    exe = bin_dir / "python"
    exe.write_text("")
    assert detect_interpreter(tmp_path) == str(exe)


def test_collect_third_party_roots_excludes_stdlib_and_local(tmp_path):
    # repo provides a local package `mypkg` and a local module `helper`
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "helper.py").write_text("x = 1\n")

    src = tmp_path / "mod.py"
    src.write_text(
        "import os\n"            # stdlib → excluded
        "import requests\n"      # third-party → kept
        "from bs4 import BeautifulSoup\n"  # third-party → kept
        "import mypkg\n"         # local package → excluded
        "import helper\n"        # local module → excluded
        "from . import sibling\n"  # relative → excluded
    )

    roots = collect_third_party_roots([src], tmp_path)

    assert "requests" in roots
    assert "bs4" in roots
    assert "os" not in roots
    assert "mypkg" not in roots
    assert "helper" not in roots


def test_preflight_reports_missing_module():
    missing = preflight_imports(
        sys.executable, {"this_pkg_does_not_exist_zzz", "sys"}
    )
    assert "this_pkg_does_not_exist_zzz" in missing
    assert "sys" not in missing  # sys is always importable


def test_preflight_empty_set_returns_empty():
    assert preflight_imports(sys.executable, set()) == []
