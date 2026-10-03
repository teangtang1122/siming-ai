"""Current source-anchored de-AI prompt and review contract."""

from app.prompts.anti_ai_prompts import (
    analyze_de_ai_fingerprints,
    build_de_ai_edit_prompt,
    build_de_ai_fidelity_audit_prompt,
    build_de_ai_style_audit_prompt,
)
from app.services.de_ai_validation import (
    assess_de_ai_revision,
    parse_de_ai_fidelity_audit,
    parse_de_ai_style_audit,
)


def test_edit_prompt_keeps_source_and_targets_only_actual_fingerprints():
    source = "周砚不由得站住。\n\n陈禾把三封信交给他，他把信收进外套。"
    prompt = build_de_ai_edit_prompt(source)

    assert prompt.count(source) == 1
    assert "不由得" in prompt
    assert "至多 12 处" in prompt
    assert '"edits"' in prompt
    assert "整章重写" in prompt
    assert "宏观账本" not in prompt
    assert "嘴角勾起" not in prompt


def test_edit_prompt_does_not_treat_unmatched_words_as_banned():
    source = "周砚把三封信交给陈禾。她检查封口，发现第三封被动过。"
    prompt = build_de_ai_edit_prompt(source)
    report = analyze_de_ai_fingerprints(source)

    assert source in prompt
    assert "合理时可以保留" in prompt
    assert report["character_count"] > 20
    assert "不由得" not in prompt


def test_fidelity_and_style_audits_receive_complete_candidate():
    original = "周砚把三封信交给陈禾。"
    candidate = "三封信从周砚手里交到陈禾手中。"

    fidelity = build_de_ai_fidelity_audit_prompt(original, [candidate])
    style = build_de_ai_style_audit_prompt([candidate])

    assert original in fidelity and candidate in fidelity
    assert candidate in style
    assert original not in style
    assert parse_de_ai_fidelity_audit(
        '{"passed":true,"issues":[]}', chunk_count=1
    )["passed"]
    assert parse_de_ai_style_audit(
        '{"passed":true,"issues":[]}', chunk_count=1
    )["passed"]


def test_length_guard_rejects_large_scene_loss():
    source = "周砚把三封信交给陈禾，并说明九点前必须送到城南邮局。" * 25
    short = "周砚送信。" * 25
    assessment = assess_de_ai_revision(source, short)

    assert not assessment["accepted"]
    assert any(issue["code"] == "excessive_shrinkage" for issue in assessment["issues"])
