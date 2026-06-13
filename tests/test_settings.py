"""Tests for settings.py — reflecta.toml loading + precedence resolution."""

import pytest

from reflecta import settings


def test_missing_file_returns_empty(tmp_path):
    assert settings.load_settings(tmp_path) == {}


def test_reads_tool_reflecta_table(tmp_path):
    (tmp_path / "reflecta.toml").write_text(
        "[tool.reflecta]\n"
        "max_iters = 30\n"
        "mutation = true\n"
        "min_mutation_score = 0.6\n"
        'head_branch = "reflecta/bot"\n'
    )
    s = settings.load_settings(tmp_path)
    assert s == {
        "max_iters": 30,
        "mutation": True,
        "min_mutation_score": 0.6,
        "head_branch": "reflecta/bot",
    }


def test_reads_top_level_keys(tmp_path):
    (tmp_path / "reflecta.toml").write_text("max_iters = 5\nattempt_risky = true\n")
    s = settings.load_settings(tmp_path)
    assert s == {"max_iters": 5, "attempt_risky": True}


def test_unknown_keys_ignored(tmp_path):
    (tmp_path / "reflecta.toml").write_text(
        "[tool.reflecta]\nmax_iters = 7\nbogus_key = 99\n"
    )
    assert settings.load_settings(tmp_path) == {"max_iters": 7}


def test_types_are_coerced(tmp_path):
    (tmp_path / "reflecta.toml").write_text(
        "[tool.reflecta]\nmin_mutation_score = 1\nmax_mutants = 10\n"
    )
    s = settings.load_settings(tmp_path)
    assert isinstance(s["min_mutation_score"], float)
    assert s["min_mutation_score"] == 1.0
    assert s["max_mutants"] == 10


def test_invalid_toml_raises(tmp_path):
    (tmp_path / "reflecta.toml").write_text("this is = = not toml")
    with pytest.raises(settings.SettingsError):
        settings.load_settings(tmp_path)


def test_resolve_precedence(tmp_path):
    s = {"max_iters": 30}
    # explicit CLI value wins
    assert settings.resolve(5, "max_iters", s, 20) == 5
    # None CLI → file value
    assert settings.resolve(None, "max_iters", s, 20) == 30
    # None CLI + absent in file → default
    assert settings.resolve(None, "stall_k", s, 7) == 7
