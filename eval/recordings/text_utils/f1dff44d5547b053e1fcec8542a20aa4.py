import pytest
import re
from text_utils import camel_to_snake


def test_camel_to_snake_simple():
    """Test conversion of simple CamelCase to snake_case."""
    assert camel_to_snake("HelloWorld") == "hello_world"


def test_camel_to_snake_single_word():
    """Test that a single word remains lowercase."""
    assert camel_to_snake("Hello") == "hello"


def test_camel_to_snake_already_snake():
    """Test that snake_case input is preserved."""
    assert camel_to_snake("hello_world") == "hello_world"


def test_camel_to_snake_multiple_transitions():
    """Test conversion with multiple case transitions."""
    assert camel_to_snake("CamelCaseString") == "camel_case_string"


def test_camel_to_snake_with_numbers():
    """Test conversion with numbers in identifiers."""
    assert camel_to_snake("MyClass2Name") == "my_class2_name"


def test_camel_to_snake_consecutive_capitals():
    """Test conversion with consecutive uppercase letters."""
    assert camel_to_snake("HTTPServer") == "h_t_t_p_server"


def test_camel_to_snake_empty_string():
    """Test conversion of empty string."""
    assert camel_to_snake("") == ""


def test_camel_to_snake_single_char():
    """Test conversion of single character."""
    assert camel_to_snake("A") == "a"


def test_camel_to_snake_lowercase_start():
    """Test conversion starting with lowercase."""
    assert camel_to_snake("myVariableName") == "my_variable_name"


def test_camel_to_snake_with_acronym():
    """Test conversion with acronym pattern."""
    assert camel_to_snake("XMLParser") == "x_m_l_parser"