"""Regression checks for model-owned chapter appearance classification."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, CatalogingCandidate, Chapter, ChapterCharacter, Character, Project
from app.services.cataloging.chapter_link_ops import apply_chapter_link
from app.services.cataloging.jsonl import normalize_candidate
from app.modules.continuity.domain.cataloging_contract import (
    validate_coverage_manifest_relationships,
)
from app.services.story_granularity import inspect_candidate_coverage_items


def database():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def test_chapter_link_persists_explicit_appearance_type_and_replaces_it():
    engine, db = database()
    try:
        project = Project(id="project-1", title="章节人物语义")
        chapter = Chapter(
            id="chapter-1",
            project_id=project.id,
            title="第一章",
            content="沈砚查看档案，罗建群只出现在任职名单中。",
        )
        shen = Character(id="character-1", project_id=project.id, name="沈砚")
        luo = Character(id="character-2", project_id=project.id, name="罗建群")
        candidate = CatalogingCandidate(
            id="candidate-1",
            project_id=project.id,
            chapter_id=chapter.id,
            item_type="chapter_link",
        )
        db.add_all([project, chapter, shen, luo])
        db.flush()

        apply_chapter_link(
            db,
            candidate,
            chapter,
            {
                "characters": [
                    {"name": "沈砚", "appearance_type": "出场"},
                    {"name": "罗建群", "appearance_type": "提及"},
                ]
            },
        )
        db.flush()
        links = {
            row.character.name: row
            for row in db.query(ChapterCharacter).filter_by(chapter_id=chapter.id).all()
        }
        assert links["沈砚"].appearance_type == "出场"
        assert links["罗建群"].appearance_type == "提及"

        apply_chapter_link(
            db,
            candidate,
            chapter,
            {"characters": [{"name": "罗建群", "appearance_type": "回忆"}]},
        )
        db.flush()
        assert (
            db.query(ChapterCharacter)
            .filter_by(chapter_id=chapter.id, character_id=luo.id)
            .one()
            .appearance_type
            == "回忆"
        )
        assert db.query(ChapterCharacter).filter_by(chapter_id=chapter.id).count() == 2
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_chapter_link_normalization_requires_model_classification():
    normalized = normalize_candidate(
        {
            "type": "chapter_link",
            "characters": [{"name": "罗建群", "appearance_type": "提及"}],
        }
    )
    assert normalized["payload"]["characters"] == [
        {"name": "罗建群", "appearance_type": "提及"}
    ]

    try:
        normalize_candidate({"type": "chapter_link", "characters": ["罗建群"]})
    except ValueError as exc:
        assert "name 与 appearance_type" in str(exc)
    else:
        raise AssertionError("Unclassified chapter character link was accepted")


def test_preclassification_character_names_are_normalized_at_protocol_boundary():
    normalized = normalize_candidate(
        {"type": "chapter_link", "character_names": ["沈砚"]}
    )
    assert "character_names" not in normalized["payload"]
    assert normalized["payload"]["characters"] == [
        {"name": "沈砚", "appearance_type": "出场"}
    ]


def test_chapter_link_rejects_two_appearance_types_for_one_character():
    with __import__("pytest").raises(ValueError, match="每个角色只能出现一次"):
        normalize_candidate({
            "type": "chapter_link",
            "characters": [
                {"name": "李文华", "appearance_type": "提及"},
                {"name": "李文华", "appearance_type": "出场"},
            ],
        })


def test_manifest_rejects_near_synonym_relationships_for_same_directed_pair():
    payload = {
        "coverage_manifest": {
            "relationships": [
                {"source_name": "周芷", "target_name": "沈砚", "relationship_type": "调查合作"},
                {"source_name": "周芷", "target_name": "沈砚", "relationship_type": "合作/联合核查"},
            ]
        }
    }
    with __import__("pytest").raises(ValueError, match="同一有向角色对"):
        validate_coverage_manifest_relationships(payload)

    coverage = inspect_candidate_coverage_items([
        {"item_type": "chapter_summary", "payload": {"summary_text": "足够长的章节摘要内容用于检测同一有向角色对重复声明。", **payload}}
    ])
    assert any(
        "multiple current types for one directed pair" in item
        for item in coverage.persistence_missing
    )
