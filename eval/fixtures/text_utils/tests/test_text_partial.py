"""Partial test suite for text_utils.py.

Covers: count_words, truncate (2/5 functions).
Leaves uncovered: slugify, is_palindrome, camel_to_snake — generation targets.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from text_utils import count_words, truncate


def test_count_words_simple():
    assert count_words("hello world") == 2


def test_count_words_single():
    assert count_words("hello") == 1


def test_count_words_empty():
    assert count_words("") == 0


def test_count_words_extra_spaces():
    assert count_words("  a  b  c  ") == 3


def test_truncate_short():
    assert truncate("hi", 10) == "hi"


def test_truncate_exact():
    assert truncate("hello", 5) == "hello"


def test_truncate_long():
    assert truncate("hello world", 5) == "hello..."


def test_truncate_custom_ellipsis():
    assert truncate("hello world", 5, "!") == "hello!"
