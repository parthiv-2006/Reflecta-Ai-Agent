import pytest
from text_utils import slugify


def test_slugify_basic_transformations():
    """
    Tests basic slugify transformations: lowercasing, space-to-hyphen, stripping.
    Covers lines 9, 11, and 13.
    """
    assert slugify("Hello World") == "hello-world"
    assert slugify("  Leading and Trailing Spaces  ") == "leading-and-trailing-spaces"
    assert slugify("Already-a-slug") == "already-a-slug"
    assert slugify("") == ""
    assert slugify("ONLY SPACES HERE") == "only-spaces-here"
    assert slugify("Numbers 123 Here") == "numbers-123-here"


def test_slugify_special_characters_and_multiple_separators():
    """
    Tests slugify's handling of special characters, multiple spaces/underscores,
    and multiple hyphens, ensuring correct removal and consolidation.
    Covers lines 10, 11, 12, and 13.
    """
    assert slugify("Hello, World! How are you? -- (123)") == "hello-world-how-are-you-123"
    assert slugify("My__Fun_Text---Here!!!") == "my-fun-text-here"
    assert slugify("Service Name (with ID: 456) & More") == "service-name-with-id-456-more"
    assert slugify("  ---Test Value_With-Extra---  ") == "test-value-with-extra"
    assert slugify("  Special @ Chars # In $ Text  ") == "special-chars-in-text"