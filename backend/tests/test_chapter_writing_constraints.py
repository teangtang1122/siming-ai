from types import SimpleNamespace

import pytest

from app.core.utils import count_han_characters
from app.services.chapter_writing_constraints import (
    check_chapter_length,
    normalize_writing_arguments,
    recommended_han_character_target,
)


def test_count_han_characters_excludes_punctuation_latin_and_whitespace():
    assert count_han_characters("潮痕线 A-1，海。\n") == 4


def test_structured_minimum_is_normalized_and_bound_to_manifest():
    arguments = normalize_writing_arguments({"minimum_han_characters": "5"})
    manifest = SimpleNamespace(query_json={"arguments": arguments})

    short = check_chapter_length("潮痕三字", manifest)
    accepted = check_chapter_length("潮痕三字够", manifest)

    assert short.actual_han_characters == 4
    assert not short.accepted
    assert accepted.actual_han_characters == 5
    assert accepted.accepted


def test_retry_target_adds_bounded_margin():
    assert recommended_han_character_target(5) == 15
    assert recommended_han_character_target(3_400) == 3_740
    assert recommended_han_character_target(10_000) == 10_400


@pytest.mark.parametrize("value", [True, 0, -1, 100001, 1.5, "unknown"])
def test_invalid_structured_minimum_is_rejected(value):
    with pytest.raises(ValueError):
        normalize_writing_arguments({"minimum_han_characters": value})
