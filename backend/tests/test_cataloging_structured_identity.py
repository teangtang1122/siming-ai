"""Structured identity decisions shared by facts and candidate validation."""

from app.services.cataloging.candidate_source_expectations import (
    _fact_character_names,
    _fact_has_character_profile_evidence,
    _worldbuilding_expectation_terms,
    _non_archival_fact_names,
)
from app.services.story_granularity import inspect_candidate_coverage_items
from app.services.workspace.tools.external_cataloging import _validate_external_fact_records


def _overview(**overrides):
    payload = {
        "summary": "本章事实",
        "scenes": [],
        "cataloging_characters": [],
        "anonymous_participants": [],
        "cataloging_worldbuilding_titles": [],
        "incidental_worldbuilding_mentions": [],
    }
    payload.update(overrides)
    return {"fact_type": "chapter_overview", "payload": payload}


def test_anonymous_roles_are_excluded_by_the_models_structured_decision():
    anonymous = {
        "primary_name": "栏目负责人",
        "names": ["栏目负责人"],
        "archive_identity": "anonymous_role",
        "stable_profile_change": False,
        "role_hint": "编辑岗位",
    }

    assert _fact_character_names("character_fact", anonymous) == set()
    assert _non_archival_fact_names([("character_fact", anonymous)]) == {"栏目负责人"}
    assert _fact_has_character_profile_evidence(anonymous) is False


def test_chapter_overview_uses_only_cataloging_characters_when_present():
    overview = {
        "characters": ["周芷", "综合科记录人"],
        "cataloging_characters": ["周芷"],
        "anonymous_participants": ["综合科记录人"],
    }

    assert _fact_character_names("chapter_overview", overview) == {"周芷"}
    assert _non_archival_fact_names([("chapter_overview", overview)]) == {
        "综合科记录人"
    }


def test_explicit_no_profile_change_overrides_repeated_role_hints():
    unchanged = {
        "primary_name": "周芷",
        "archive_identity": "stable_character",
        "stable_profile_change": False,
        "role_hint": "记者",
        "aliases": ["周记者"],
    }
    changed = {**unchanged, "stable_profile_change": True}

    assert _fact_has_character_profile_evidence(unchanged) is False
    assert _fact_has_character_profile_evidence(changed) is True


def test_worldbuilding_expectations_use_structured_archive_decisions():
    stable = {
        "archive_identity": "stable_setting",
        "canonical_title_hint": "港办〔2015〕17号通知",
        "title_hint": "港办〔2015〕17号通知",
    }
    mention = {
        "archive_identity": "mention_only",
        "title_hint": "18:50字段",
    }

    assert _worldbuilding_expectation_terms("worldbuilding_fact", stable) == {
        "港办〔2015〕17号通知"
    }
    assert _worldbuilding_expectation_terms("worldbuilding_fact", mention) == set()


def test_chapter_overview_uses_only_cataloging_worldbuilding_when_present():
    overview = {
        "worldbuilding_titles": ["港办〔2015〕17号通知", "18:50字段"],
        "cataloging_worldbuilding_titles": ["港办〔2015〕17号通知"],
        "incidental_worldbuilding_mentions": ["18:50字段"],
    }

    assert _worldbuilding_expectation_terms("chapter_overview", overview) == {
        "港办〔2015〕17号通知"
    }


def test_worldbuilding_candidate_coverage_prefers_stable_title_over_database_id():
    coverage = inspect_candidate_coverage_items([
        {
            "item_type": "worldbuilding_update",
            "status": "pending",
            "payload": {
                "id": "01a316b5-50b2-401a-8c69-81e43397c1a3",
                "title": "2015年3月9日港务办公室例会纪要",
                "content": "本章确认其档案移交用途。",
            },
        }
    ])

    assert coverage.worldbuilding_candidate_identities == (
        "2015年3月9日港务办公室例会纪要",
    )


def test_fact_contract_rejects_more_than_six_story_scenes():
    _records, errors = _validate_external_fact_records([_overview(
        scenes=[{"scene_number": index} for index in range(1, 8)],
    )])

    assert errors == [
        "facts[0].payload.scenes must contain at most 6 grouped story scenes; received 7"
    ]


def test_fact_contract_requires_all_structured_overview_scopes():
    _records, errors = _validate_external_fact_records([{
        "fact_type": "chapter_overview",
        "payload": {"summary": "缺少范围", "scenes": []},
    }])

    assert errors == [
        "facts[0].payload.cataloging_characters is required (use [] when empty)",
        "facts[0].payload.anonymous_participants is required (use [] when empty)",
        "facts[0].payload.cataloging_worldbuilding_titles is required (use [] when empty)",
        "facts[0].payload.incidental_worldbuilding_mentions is required (use [] when empty)",
    ]


def test_fact_contract_rejects_relationship_endpoint_outside_stable_scope():
    facts = [
        _overview(cataloging_characters=["周芷"], anonymous_participants=["钱立衡"]),
        {
            "fact_type": "character_fact",
            "payload": {
                "primary_name": "周芷",
                "archive_identity": "stable_character",
                "stable_profile_change": False,
            },
        },
        {
            "fact_type": "character_fact",
            "payload": {
                "primary_name": "钱立衡",
                "archive_identity": "mention_only",
                "stable_profile_change": False,
            },
        },
        {
            "fact_type": "relationship_fact",
            "payload": {
                "source_name": "周芷",
                "target_name": "钱立衡",
                "relationship_type": "受托代理",
                "description": "周芷提到其受托安排。",
            },
        },
    ]

    _records, errors = _validate_external_fact_records(facts)

    assert errors == [
        "facts[3].payload relationship endpoints must be stable_character facts listed "
        "in chapter_overview.payload.cataloging_characters; missing: 钱立衡"
    ]


def test_fact_contract_accepts_complete_consistent_structured_scope():
    facts = [
        _overview(
            cataloging_characters=["周芷", "钱立衡"],
            anonymous_participants=["栏目负责人"],
            cataloging_worldbuilding_titles=["临汐港务局"],
            incidental_worldbuilding_mentions=["18:50字段"],
        ),
        *[
            {
                "fact_type": "character_fact",
                "payload": {
                    "primary_name": name,
                    "archive_identity": identity,
                    "stable_profile_change": False,
                },
            }
            for name, identity in (
                ("周芷", "stable_character"),
                ("钱立衡", "stable_character"),
                ("栏目负责人", "anonymous_role"),
            )
        ],
        {
            "fact_type": "relationship_fact",
            "payload": {
                "source_name": "周芷",
                "target_name": "钱立衡",
                "relationship_type": "受托代理",
                "description": "周芷提到其受托安排。",
            },
        },
        {
            "fact_type": "worldbuilding_fact",
            "payload": {
                "canonical_title_hint": "临汐港务局",
                "archive_identity": "stable_setting",
                "stable_setting_change": False,
            },
        },
        {
            "fact_type": "worldbuilding_fact",
            "payload": {
                "title_hint": "18:50字段",
                "archive_identity": "mention_only",
                "stable_setting_change": False,
            },
        },
    ]

    records, errors = _validate_external_fact_records(facts)

    assert len(records) == len(facts)
    assert errors == []


def test_fact_contract_rejects_duplicate_fact_identities_and_relationship_pairs():
    facts = [
        _overview(cataloging_characters=["周芷", "沈砚"]),
        *[
            {
                "fact_type": "character_fact",
                "payload": {
                    "primary_name": name,
                    "archive_identity": "stable_character",
                    "stable_profile_change": False,
                },
            }
            for name in ("周芷", "周芷", "沈砚")
        ],
        *[
            {
                "fact_type": "relationship_fact",
                "payload": {
                    "source_name": "周芷",
                    "target_name": "沈砚",
                    "relationship_type": relation_type,
                    "description": "合作。",
                },
            }
            for relation_type in ("合作", "联合核查")
        ],
    ]

    _records, errors = _validate_external_fact_records(facts)

    assert "facts[2] duplicates character_fact identity: 周芷" in errors
    assert "facts[5] duplicates directed relationship_fact pair: 周芷 -> 沈砚" in errors
