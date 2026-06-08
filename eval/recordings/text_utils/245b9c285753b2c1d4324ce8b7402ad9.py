import pytest
from text_utils import is_palindrome


def test_is_palindrome_simple_cases():
    """Test with simple palindromes and non-palindromes."""
    # Palindromes
    assert is_palindrome("madam") is True
    assert is_palindrome("racecar") is True
    assert is_palindrome("level") is True
    assert is_palindrome("noon") is True
    assert is_palindrome("a") is True
    assert is_palindrome("") is True  # Empty string is a palindrome

    # Non-palindromes
    assert is_palindrome("hello") is False
    assert is_palindrome("world") is False
    assert is_palindrome("python") is False
    assert is_palindrome("ab") is False


def test_is_palindrome_with_mixed_case_and_punctuation():
    """Test with strings containing mixed case, spaces, and punctuation."""
    # Palindromes with mixed case and special characters
    assert is_palindrome("Madam") is True
    assert is_palindrome("Racecar") is True
    assert is_palindrome("A man, a plan, a canal: Panama") is True
    assert is_palindrome("No lemon, no melon.") is True
    assert is_palindrome("Was it a car or a cat I saw?") is True
    assert is_palindrome("Eva, can I stab bats in a cave?") is True
    assert is_palindrome("Rise to vote, sir.") is True
    assert is_palindrome("Do geese see God?") is True
    assert is_palindrome("12321") is True
    assert is_palindrome("$$$1_2-3/2@1+++") is True  # Should ignore symbols and numbers

    # Non-palindromes with mixed case and special characters
    assert is_palindrome("Hello, world!") is False
    assert is_palindrome("Madam, I'm Adam") is False  # Cleans to madmimadam
    assert is_palindrome("Race a car") is False  # Cleans to raceacar
    assert is_palindrome("1 eye for of an eye 1") is False # Cleans to 1eyeforofaneye1
    assert is_palindrome("Python_snake") is False