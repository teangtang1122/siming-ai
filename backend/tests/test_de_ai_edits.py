"""The de-AI preview may change only explicitly matched source spans."""

import json

import pytest

from app.core.exceptions import LLMError
from app.services.de_ai_edits import apply_de_ai_edits


def test_applies_exact_edits_and_preserves_every_other_character():
    source = "周砚不由得抬头。\n\n陈禾把三封信交给周砚，九点前要送到城南。"
    result = apply_de_ai_edits(source, json.dumps({"edits": [{
        "old": "周砚不由得抬头",
        "new": "周砚听到动静，抬起头",
    }]}, ensure_ascii=False))

    assert result == (
        "周砚听到动静，抬起头。\n\n陈禾把三封信交给周砚，九点前要送到城南。",
        1,
        0,
    )


def test_rejects_whole_scene_compression():
    source = "周砚检查封口，又核对了信上的时间。陈禾把三封信交给他，九点前要送到城南。"
    with pytest.raises(LLMError, match="原文未变"):
        apply_de_ai_edits(source, json.dumps({"edits": [{
            "old": source,
            "new": "周砚拿到了信。",
        }]}, ensure_ascii=False))


def test_skips_unmatched_and_overlapping_edits():
    source = "周砚不由得抬头。陈禾把三封信交给周砚。"
    revised, applied, rejected = apply_de_ai_edits(source, json.dumps({"edits": [
        {"old": "周砚不由得抬头", "new": "周砚听见声响，抬起头"},
        {"old": "不由得抬头", "new": "抬起头"},
        {"old": "原文没有的句子", "new": "替换句"},
    ]}, ensure_ascii=False))

    assert revised == "周砚听见声响，抬起头。陈禾把三封信交给周砚。"
    assert (applied, rejected) == (1, 2)


def test_malformed_json_fails_without_revision():
    with pytest.raises(LLMError, match="合法的局部修订 JSON"):
        apply_de_ai_edits("原文足够长。", "原文足够长。")
