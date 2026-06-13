from reflecta.validation import validate_test_source


def test_empty_file_is_invalid():
    ok, reason = validate_test_source("")
    assert not ok
    assert "empty" in reason
    ok, _ = validate_test_source("   \n\t\n")
    assert not ok


def test_syntax_error_is_invalid():
    ok, reason = validate_test_source("def test_x(:\n    assert 1\n")
    assert not ok
    assert "syntax" in reason


def test_no_test_function_is_invalid():
    ok, reason = validate_test_source("from calc import add\nx = add(1, 2)\n")
    assert not ok
    assert "test" in reason


def test_placeholder_text_is_invalid():
    src = (
        "from unittest import mock\n"
        "def test_thing():\n"
        "    # rest of the function remains the same\n"
        "    assert True\n"
    )
    ok, reason = validate_test_source(src)
    assert not ok
    assert "placeholder" in reason


def test_dangling_decorator_name_is_invalid():
    # The exact leaseguard failure: a fragment starting with @mock.patch but no
    # `from unittest import mock` above it. Parses fine, NameError at collection.
    src = (
        "@mock.patch('datetime.datetime')\n"
        "def test_seed(mock_datetime):\n"
        "    assert mock_datetime is not None\n"
    )
    ok, reason = validate_test_source(src)
    assert not ok
    assert "mock" in reason


def test_well_formed_test_is_valid():
    src = (
        "from unittest import mock\n"
        "from calc import add\n\n"
        "@mock.patch('calc.helper')\n"
        "def test_add(mock_helper):\n"
        "    assert add(1, 2) == 3\n\n"
        "def test_add_negative():\n"
        "    assert add(-1, -1) == -2\n"
    )
    ok, reason = validate_test_source(src)
    assert ok, reason


def test_test_class_methods_count_as_tests():
    src = "class TestThing:\n    def test_a(self):\n        assert 1 == 1\n"
    ok, reason = validate_test_source(src)
    assert ok, reason


def test_imported_mock_via_import_unittest_is_valid():
    src = (
        "import unittest.mock as mock\n"
        "@mock.patch('x.y')\n"
        "def test_x(m):\n"
        "    assert m is not None\n"
    )
    ok, reason = validate_test_source(src)
    assert ok, reason
