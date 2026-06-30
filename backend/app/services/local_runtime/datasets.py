"""Build user-owned SFT datasets from Siming project content."""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

from sqlalchemy.orm import Session

from ...database.models import Chapter, ChapterSnapshot, Project, TrainingDataset
from ...services.content_store import load_chapter_from_file
from .paths import training_root


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def build_training_dataset(
    db: Session,
    *,
    name: str,
    project_id: str | None,
    chapter_ids: list[str],
    include_outline_pairs: bool,
    include_revision_pairs: bool,
    include_character_dialogue: bool,
    eval_ratio: float,
    rights_confirmed: bool,
) -> TrainingDataset:
    if not rights_confirmed:
        raise ValueError("创建训练集前必须确认你拥有这些文本的训练使用权")
    query = db.query(Chapter)
    if project_id:
        query = query.filter(Chapter.project_id == project_id)
    if chapter_ids:
        query = query.filter(Chapter.id.in_(chapter_ids))
    chapters = query.order_by(Chapter.created_at.asc()).all()
    if not chapters:
        raise ValueError("没有可用于训练的章节")
    project = db.query(Project).filter(Project.id == project_id).first() if project_id else None

    records: list[dict] = []
    seen: set[str] = set()
    lengths: list[int] = []
    for chapter in chapters:
        if project:
            load_chapter_from_file(project, chapter)
        content = (chapter.content or "").strip()
        if len(content) < 200:
            continue
        instruction = f"根据既定小说设定与章节标题，创作《{chapter.title}》正文。"
        if include_outline_pairs and chapter.outline_node_id:
            instruction = f"根据大纲节点和前文连续性，创作《{chapter.title}》正文。"
        key = _normalize(content)[:500]
        if key not in seen:
            seen.add(key)
            records.append({"instruction": instruction, "input": "", "output": content, "source": chapter.id})
            lengths.append(len(content))

        if include_revision_pairs:
            snapshots = (
                db.query(ChapterSnapshot)
                .filter(ChapterSnapshot.chapter_id == chapter.id)
                .order_by(ChapterSnapshot.version_number.asc())
                .all()
            )
            if len(snapshots) >= 2:
                before, after = snapshots[-2].content or "", snapshots[-1].content or ""
                if before.strip() and after.strip() and _normalize(before) != _normalize(after):
                    records.append({
                        "instruction": "在不改变剧情事实的前提下，重写并提升这段小说正文。",
                        "input": before,
                        "output": after,
                        "source": f"{chapter.id}:revision",
                    })

        if include_character_dialogue:
            dialogue = "\n".join(
                line.strip()
                for line in content.splitlines()
                if ("“" in line or '"' in line) and len(line.strip()) >= 12
            )
            if len(dialogue) >= 100:
                records.append({
                    "instruction": "根据角色关系与语气，续写自然、有潜台词的对话场景。",
                    "input": dialogue[:2000],
                    "output": dialogue[2000:6000] or dialogue,
                    "source": f"{chapter.id}:dialogue",
                })

    if len(records) < 2:
        raise ValueError("有效训练样本不足，至少需要 2 条")
    random.Random(20250623).shuffle(records)
    eval_count = max(1, int(len(records) * eval_ratio))
    for index, record in enumerate(records):
        record["split"] = "eval" if index < eval_count else "train"

    dataset = TrainingDataset(
        project_id=project_id,
        name=name,
        file_path="",
        sample_count=len(records),
        train_count=len(records) - eval_count,
        eval_count=eval_count,
        stats_json={
            "min_chars": min(lengths) if lengths else 0,
            "max_chars": max(lengths) if lengths else 0,
            "avg_chars": round(sum(lengths) / len(lengths), 1) if lengths else 0,
            "deduplicated": len(seen),
        },
        source_config_json={
            "chapter_ids": chapter_ids,
            "include_outline_pairs": include_outline_pairs,
            "include_revision_pairs": include_revision_pairs,
            "include_character_dialogue": include_character_dialogue,
            "eval_ratio": eval_ratio,
        },
        rights_confirmed=True,
    )
    db.add(dataset)
    db.flush()
    path = training_root() / "datasets" / f"{dataset.id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    dataset.file_path = str(path)
    return dataset
