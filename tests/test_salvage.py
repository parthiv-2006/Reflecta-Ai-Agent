"""Salvage pass: strip failing test functions, keep the passing remainder.

Motivating run (Weave/python-service, 2026-06-11): 3 of 4 FAILED targets had
drafts where most tests passed (2/4, 1/2, 1/2) — repair exhausted on the bad
half and the whole file was deleted, discarding every passing test with it.
"""

import textwrap

from reflecta.salvage import failing_test_names, strip_failing_tests


def test_failing_names_from_quiet_summary():
    out = textwrap.dedent("""\
        ..FF                                                       [100%]
        FAILED tests/_reflecta/test_x.py::test_bad_one - AssertionError: nope
        ERROR tests/_reflecta/test_x.py::test_bad_two - TypeError
        2 failed, 2 passed in 0.26s
    """)
    assert failing_test_names(out) == {"test_bad_one", "test_bad_two"}


def test_failing_names_parametrized_and_class_paths():
    out = (
        "FAILED tests/t.py::test_p[case-1] - x\n"
        "FAILED tests/t.py::TestThing::test_method - y\n"
    )
    assert failing_test_names(out) == {"test_p", "test_method"}


def test_collection_error_without_node_id_yields_nothing():
    out = "ERROR tests/_reflecta/test_x.py\n!!!! Interrupted: 1 error during collection !!!!\n"
    assert failing_test_names(out) == set()


SOURCE = textwrap.dedent("""\
    import pytest
    from unittest import mock


    @pytest.fixture
    def thing():
        return 41


    def helper():
        return 1


    def test_good(thing):
        assert thing + helper() == 42


    @mock.patch("os.getcwd")
    def test_bad(m):
        assert False
""")


def test_strip_removes_only_failing_function_and_its_decorators():
    trimmed = strip_failing_tests(SOURCE, {"test_bad"})
    assert trimmed is not None
    assert "def test_bad" not in trimmed
    assert "@mock.patch" not in trimmed  # decorator goes with the function
    assert "def test_good" in trimmed
    assert "@pytest.fixture" in trimmed  # fixtures and helpers survive
    assert "def helper" in trimmed


def test_strip_declines_when_no_test_would_survive():
    assert strip_failing_tests(SOURCE, {"test_good", "test_bad"}) is None


def test_strip_declines_when_name_not_found():
    assert strip_failing_tests(SOURCE, {"test_absent"}) is None


def test_strip_declines_on_empty_failing_set():
    assert strip_failing_tests(SOURCE, set()) is None


def test_strip_class_methods_and_whole_dead_classes():
    src = textwrap.dedent("""\
        class TestA:
            def test_ok(self):
                assert 1 == 1

            def test_bad(self):
                assert False


        class TestB:
            def test_also_bad(self):
                assert False
    """)
    trimmed = strip_failing_tests(src, {"test_bad", "test_also_bad"})
    assert trimmed is not None
    assert "test_ok" in trimmed
    assert "test_bad" not in trimmed
    assert "class TestB" not in trimmed  # fully-failing class removed entirely


def test_trimmed_source_still_parses_and_runs_standalone():
    trimmed = strip_failing_tests(SOURCE, {"test_bad"})
    compile(trimmed, "<trimmed>", "exec")
