from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


path = Path("backend/app/services/gateway_legacy_replication.py")
text = path.read_text(encoding="utf-8")

text = replace_once(
    text,
    '''    Character,\n    CharacterAlias,\n    CharacterRelationship,\n''',
    '''    Character,\n    CharacterAIConfig,\n    CharacterAlias,\n    CharacterRelationship,\n''',
    "CharacterAIConfig import",
)
text = replace_once(
    text,
    '''    create_character_version,\n    dumps_list,\n    sync_character_aliases,\n''',
    '''    create_character_version,\n    dumps_list,\n    loads_list,\n    sync_character_aliases,\n''',
    "loads_list import",
)
text = replace_once(
    text,
    '''    RecordSpec(Character, "character", "character", "direct", {"name": "未命名角色"}),\n    RecordSpec(CharacterVersion, "character", "character_version", "character"),\n''',
    '''    RecordSpec(Character, "character", "character", "direct", {"name": "未命名角色"}),\n    RecordSpec(\n        CharacterAIConfig,\n        "character_ai_config",\n        "character_ai_config",\n        "character",\n    ),\n    RecordSpec(CharacterVersion, "character", "character_version", "character"),\n''',
    "AI config RecordSpec",
)
text = replace_once(
    text,
    '''    "character": "character",\n    "character_alias": "character_alias",\n''',
    '''    "character": "character",\n    "character_ai_config": "character_ai_config",\n    "character_alias": "character_alias",\n''',
    "AI config default record type",
)
text = replace_once(
    text,
    '''    Character: CHARACTER_MUTATION_COLUMNS,\n    WorldbuildingEntry: frozenset(\n''',
    '''    Character: CHARACTER_MUTATION_COLUMNS,\n    CharacterRelationship: frozenset(\n        {\n            "id",\n            "project_id",\n            "character_a_id",\n            "character_b_id",\n            "relationship_type",\n            "description",\n        }\n    ),\n    CharacterAIConfig: frozenset(\n        {\n            "id",\n            "character_id",\n            "tone_style",\n            "catchphrases",\n            "verbosity",\n            "emotion_tendency",\n            "model_override",\n            "custom_system_prompt",\n        }\n    ),\n    WorldbuildingEntry: frozenset(\n''',
    "character auxiliary mutation columns",
)
text = replace_once(
    text,
    '''    WorldbuildingEntry: frozenset(\n        {"id", "project_id", "dimension", "title", "content", "sort_order"}\n    ),\n    Foreshadowing: frozenset(\n''',
    '''    WorldbuildingEntry: frozenset(\n        {"id", "project_id", "dimension", "title", "content", "sort_order"}\n    ),\n    WorldbuildingRelation: frozenset(\n        {\n            "id",\n            "project_id",\n            "source_entry_id",\n            "target_entry_id",\n            "relation_type",\n            "description",\n            "metadata_json",\n        }\n    ),\n    Foreshadowing: frozenset(\n''',
    "world relation mutation columns",
)
text = replace_once(
    text,
    '''    if spec.model is OutlineNode:\n        return {"_record_type": spec.record_type, **node_to_dict(row)}\n    payload: dict[str, Any] = {"_record_type": spec.record_type}\n''',
    '''    if spec.model is OutlineNode:\n        return {"_record_type": spec.record_type, **node_to_dict(row)}\n    if spec.model is CharacterRelationship:\n        return {\n            "_record_type": spec.record_type,\n            "id": row.id,\n            "project_id": row.project_id,\n            "from": row.character_a_id,\n            "to": row.character_b_id,\n            "relationship_type": row.relationship_type,\n            "description": row.description,\n            "created_at": _json_value(row.created_at),\n        }\n    if spec.model is CharacterAIConfig:\n        return {\n            "_record_type": spec.record_type,\n            "id": row.id,\n            "character_id": row.character_id,\n            "tone_style": row.tone_style or "neutral",\n            "catchphrases": loads_list(row.catchphrases),\n            "verbosity": row.verbosity or "moderate",\n            "emotion_tendency": row.emotion_tendency or "neutral",\n            "model_override": row.model_override,\n            "custom_system_prompt": row.custom_system_prompt,\n            "created_at": _json_value(row.created_at),\n            "updated_at": _json_value(row.updated_at),\n        }\n    payload: dict[str, Any] = {"_record_type": spec.record_type}\n''',
    "canonical auxiliary serializers",
)
anchor = '''def _prepare_character_mutation_values(\n    values: dict[str, Any],\n    row: Character | None,\n) -> tuple[dict[str, Any], list[str] | None, str | None]:\n'''
insert = '''def _canonical_character_relation_values(values: dict[str, Any]) -> dict[str, Any]:\n    source = values.pop("from", None)\n    target = values.pop("to", None)\n    if source is not None:\n        values.setdefault("character_a_id", source)\n    if target is not None:\n        values.setdefault("character_b_id", target)\n    return values\n\n\ndef _canonical_character_ai_config_values(values: dict[str, Any]) -> dict[str, Any]:\n    if "catchphrases" in values:\n        phrases = _string_list(values.get("catchphrases"), field="catchphrases")\n        values["catchphrases"] = dumps_list(phrases) if phrases is not None else None\n    return values\n\n\ndef _validate_auxiliary_relationships(\n    db: Session,\n    project_id: str,\n    model: type,\n    values: dict[str, Any],\n    row: Any | None,\n) -> None:\n    if model is CharacterRelationship:\n        source_id = str(values.get("character_a_id") or getattr(row, "character_a_id", "") or "")\n        target_id = str(values.get("character_b_id") or getattr(row, "character_b_id", "") or "")\n        if not source_id or not target_id:\n            raise ValidationError("角色关系缺少起点或终点角色")\n        if source_id == target_id:\n            raise ValidationError("角色不能与自身建立关系")\n        source = db.get(Character, source_id)\n        target = db.get(Character, target_id)\n        if not source or not target or source.project_id != project_id or target.project_id != project_id:\n            raise ValidationError("角色关系两端必须属于当前作品")\n    elif model is WorldbuildingRelation:\n        source_id = str(values.get("source_entry_id") or getattr(row, "source_entry_id", "") or "")\n        target_id = str(values.get("target_entry_id") or getattr(row, "target_entry_id", "") or "")\n        if not source_id or not target_id:\n            raise ValidationError("世界观关系缺少起点或终点条目")\n        if source_id == target_id:\n            raise ValidationError("世界观条目不能与自身建立关系")\n        source = db.get(WorldbuildingEntry, source_id)\n        target = db.get(WorldbuildingEntry, target_id)\n        if not source or not target or source.project_id != project_id or target.project_id != project_id:\n            raise ValidationError("世界观关系两端必须属于当前作品")\n\n\n'''
if anchor not in text:
    raise RuntimeError("auxiliary helper anchor missing")
text = text.replace(anchor, insert + anchor, 1)
text = replace_once(
    text,
    '''    if spec.model is Character:\n        values, character_aliases, character_change_summary = _prepare_character_mutation_values(\n            values,\n            row,\n        )\n    if spec.model is OutlineNode:\n''',
    '''    if spec.model is Character:\n        values, character_aliases, character_change_summary = _prepare_character_mutation_values(\n            values,\n            row,\n        )\n    if spec.model is CharacterRelationship:\n        values = _canonical_character_relation_values(values)\n    if spec.model is CharacterAIConfig:\n        values = _canonical_character_ai_config_values(values)\n        if row is not None:\n            values.setdefault("character_id", row.character_id)\n    if spec.model is OutlineNode:\n''',
    "auxiliary value normalization",
)
text = replace_once(
    text,
    '''    if spec.model is Chapter and values.get("outline_node_id"):\n        get_outline_node_or_404(db, project_id, values.get("outline_node_id"))\n    if spec.model is OutlineNode and "parent_id" in values:\n''',
    '''    if spec.model is Chapter and values.get("outline_node_id"):\n        get_outline_node_or_404(db, project_id, values.get("outline_node_id"))\n    if spec.model in {CharacterRelationship, WorldbuildingRelation}:\n        _validate_auxiliary_relationships(db, project_id, spec.model, values, row)\n    if spec.model is OutlineNode and "parent_id" in values:\n''',
    "auxiliary relationship validation",
)
text = replace_once(
    text,
    '''    if operation == "delete":\n        if spec.model is Project:\n            raise ValidationError("请在作品管理页确认删除，移动端不会直接删除整部作品")\n''',
    '''    if operation == "delete":\n        if spec.model is Project:\n            raise ValidationError("请在作品管理页确认删除，移动端不会直接删除整部作品")\n        if spec.model is CharacterAIConfig:\n            raise ValidationError("角色 AI 配置不单独删除，请通过角色配置页修改")\n''',
    "AI config delete guard",
)

path.write_text(text, encoding="utf-8")
