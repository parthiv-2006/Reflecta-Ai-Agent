# text_utils.py — 5 string-processing functions, standard-library only.
# Used as an eval fixture: test_text_partial.py covers count_words/truncate (2/5),
# leaving slugify/is_palindrome/camel_to_snake as generation targets.
import re


def slugify(text):
    """Convert text to a URL-friendly slug (lowercase, hyphens, no special chars)."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def truncate(text, n, ellipsis="..."):
    """Return text truncated to n characters, appending ellipsis if cut."""
    if len(text) <= n:
        return text
    return text[:n] + ellipsis


def count_words(text):
    """Return the number of whitespace-delimited words in text."""
    return len(text.split())


def is_palindrome(s):
    """Return True if s reads the same forwards and backwards (case-insensitive)."""
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", s).lower()
    return cleaned == cleaned[::-1]


def camel_to_snake(name):
    """Convert CamelCase identifier to snake_case."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
