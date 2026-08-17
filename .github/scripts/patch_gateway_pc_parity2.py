from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


# Canonical domain projection and write allowlists.
path = Path("backend/app/services/gateway_legacy_replication.py")
text = path.read_text(encoding="utf-8")
old = '''from app.core.exceptions import ValidationError
from app.services.chapter_ordering import next_chapter_sort_order
from app.services.character_service import character_to_dict, dumps_list, sync_character_aliases
'''
new = '''from app.core.exceptions import ValidationError
from app.core.utils import count_words
from app.services.chapter_ordering import next_chapter_sort_order
from app.services.chapter_service import chapter_to_detail
from app.services.character_service import character_to_dict, dumps_list, sync_character_aliases
from app.services.outline_service import (
    ensure_no_cycle,
    load_outline_nodes,
    node_to_dict,
    outline_sort_context,
    replace_character_links,
)
'''
text = replace_once(text, old, new, "gateway parity imports")
anchor = '''CHARACTER_MUTATION_COLUMNS = frozenset(
    {
        "id",
        "project_id",
        "name",
        "appearance",
        "role_type",
        "personality",
        "background",
        "abilities",
        "age",
        "is_evolution_tracked",
        "life_status",
        "current_location",
        "realm_or_level",
        "physical_state",
        "mental_state",
        "current_goal",
        "active_conflict",
        "abilities_state",
        "items_or_assets",
        "profile_json",
    }
)
'''
addition = anchor + '''
MUTATION_COLUMNS_BY_MODEL: dict[type, frozenset[str]] = {
    Project: frozenset(
        {
            "id",
            "title",
            "description",
            "tags",
            "narrative_perspective",
            "writing_style",
            "forbidden_sentence_patterns",
            "rhetoric_guidelines",
            "short_sentences",
            "custom_style_prompt",
            "daily_word_goal",
        }
    ),
    Chapter: frozenset(
        {"id", "project_id", "title", "outline_node_id", "content", "context_manifest_id"}
    ),
    OutlineNode: frozenset(
        {
            "id",
            "project_id",
            "parent_id",
            "node_type",
            "title",
            "summary",
            "status",
            "sort_order",
            "metadata_json",
        }
    ),
    Character: CHARACTER_MUTATION_COLUMNS,
    WorldbuildingEntry: frozenset(
        {"id", "project_id", "dimension", "title", "content", "sort_order"}
    ),
    Foreshadowing: frozenset(
        {
            "id",
            "project_id",
            "title",
            "description",
            "status",
            "importance",
            "source_chapter_id",
            "target_chapter_id",
            "target_chapter_number",
            "resolved_chapter_id",
            "evidence",
            "resolution_note",
            "resolution_evidence",
            "verification_note",
            "closed_by",
            "storyline",
            "dedupe_key",
            "source",
        }
    ),
    NarrativeDebt: frozenset(
        {
            "id",
            "project_id",
            "debt_type",
            "title",
            "description",
            "status",
            "priority",
            "source_chapter_id",
            "target_chapter_id",
            "target_chapter_number",
            "resolved_chapter_id",
            "linked_foreshadowing_id",
            "linked_causal_edge_id",
            "evidence",
            "resolution_note",
            "resolution_evidence",
            "verification_note",
            "closed_by",
            "dedupe_key",
            "source",
        }
    ),
}
'''
text = replace_once(text, anchor, addition, "mutation allowlists")
old = '''    if spec.model is Character:
        # Android and the web UI consume the same Character contract. Do not
        # leak DB-only shapes such as abilities JSON text or profile_json into
        # sync snapshots, otherwise bootstrap can replace a canonical PC API
        # response with an incompatible payload.
        return {"_record_type": spec.record_type, **character_to_dict(row)}
    payload: dict[str, Any] = {"_record_type": spec.record_type}
'''
new = '''    if spec.model is Character:
        # Android and the web UI consume the same Character contract. Do not
        # leak DB-only shapes such as abilities JSON text or profile_json into
        # sync snapshots, otherwise bootstrap can replace a canonical PC API
        # response with an incompatible payload.
        return {"_record_type": spec.record_type, **character_to_dict(row)}
    if spec.model is OutlineNode:
        return {"_record_type": spec.record_type, **node_to_dict(row)}
    payload: dict[str, Any] = {"_record_type": spec.record_type}
'''
text = replace_once(text, old, new, "outline canonical serialization")
old = '''def project_snapshots(db: Session, project_id: str) -> list[tuple[RecordSpec, Any, dict[str, Any]]]:
    snapshots: list[tuple[RecordSpec, Any, dict[str, Any]]] = []
    for spec in RECORD_SPECS:
        for row in _rows_for_spec(db, project_id, spec):
            snapshots.append((spec, row, serialize_record(row, spec)))
    snapshots.sort(key=lambda item: (item[0].entity_type, str(item[1].id)))
    return snapshots
'''
new = '''def _serialize_domain_row(db: Session, row: Any, spec: RecordSpec) -> dict[str, Any]:
    if spec.model is Chapter:
        context = outline_sort_context(load_outline_nodes(db, str(row.project_id)))
        return {"_record_type": spec.record_type, **chapter_to_detail(row, context)}
    return serialize_record(row, spec)


def domain_snapshot_for_entity(
    db: Session,
    *,
    project_id: str,
    entity_type: str,
    entity_id: str,
) -> dict[str, Any] | None:
    """Return the authoritative PC-shaped snapshot after a sync mutation."""
    for spec in RECORD_SPECS:
        if spec.entity_type != entity_type:
            continue
        row = db.get(spec.model, entity_id)
        if row is None:
            continue
        if project_id_for_record(db, row, spec) != project_id:
            continue
        return _serialize_domain_row(db, row, spec)
    return None


def project_snapshots(db: Session, project_id: str) -> list[tuple[RecordSpec, Any, dict[str, Any]]]:
    snapshots: list[tuple[RecordSpec, Any, dict[str, Any]]] = []
    for spec in RECORD_SPECS:
        for row in _rows_for_spec(db, project_id, spec):
            snapshots.append((spec, row, _serialize_domain_row(db, row, spec)))
    snapshots.sort(key=lambda item: (item[0].entity_type, str(item[1].id)))
    return snapshots
'''
text = replace_once(text, old, new, "canonical project snapshots")
# Add project/outline request-shape translators before Character translator.
anchor = '''def _canonical_character_values(values: dict[str, Any]) -> tuple[dict[str, Any], list[str] | None]:
'''
helpers = '''def _canonical_project_values(values: dict[str, Any]) -> dict[str, Any]:
    tags = values.get("tags")
    if isinstance(tags, list):
        values["tags"] = json.dumps([str(item) for item in tags], ensure_ascii=False)
    elif tags is not None and not isinstance(tags, str):
        raise ValidationError("作品 tags 必须是字符串数组")
    return values


def _canonical_outline_values(
    values: dict[str, Any],
) -> tuple[dict[str, Any], list[tuple[str, str | None]] | None]:
    if "metadata" in values:
        metadata = values.pop("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValidationError("大纲 metadata 必须是对象")
        values["metadata_json"] = metadata

    characters = values.pop("characters", None)
    character_ids = values.pop("character_ids", None)
    links: list[tuple[str, str | None]] | None = None
    if characters is not None:
        if not isinstance(characters, list):
            raise ValidationError("大纲 characters 必须是数组")
        links = []
        seen: set[str] = set()
        for item in characters:
            if not isinstance(item, dict):
                raise ValidationError("大纲 characters 元素必须是对象")
            character_id = str(item.get("character_id") or "").strip()
            if not character_id or character_id in seen:
                continue
            seen.add(character_id)
            role = str(item.get("role_in_scene") or "").strip() or None
            links.append((character_id, role))
    elif character_ids is not None:
        if not isinstance(character_ids, list):
            raise ValidationError("大纲 character_ids 必须是数组")
        links = []
        seen: set[str] = set()
        for raw in character_ids:
            character_id = str(raw or "").strip()
            if not character_id or character_id in seen:
                continue
            seen.add(character_id)
            links.append((character_id, None))
    return values, links


''' + anchor
text = replace_once(text, anchor, helpers, "public request translators")
# Harden the mutation entry point and translate public PC shapes.
old = '''    row = db.get(spec.model, entity_id)
    if operation == "delete":
        if spec.model is Project:
'''
new = '''    if spec.model not in MUTATION_COLUMNS_BY_MODEL:
        raise ValidationError("该同步记录由 PC 管理，移动端只读")
    row = db.get(spec.model, entity_id)
    if operation == "delete":
        if spec.model is Project:
'''
text = replace_once(text, old, new, "server-managed mutation guard")
old = '''    values = dict(payload or {})
    values.pop("_record_type", None)
    character_aliases: list[str] | None = None
    if spec.model is Character:
        values, character_aliases = _canonical_character_values(values)
'''
new = '''    values = dict(payload or {})
    values.pop("_record_type", None)
    character_aliases: list[str] | None = None
    outline_links: list[tuple[str, str | None]] | None = None
    if spec.model is Project:
        values = _canonical_project_values(values)
    if spec.model is Character:
        values, character_aliases = _canonical_character_values(values)
    if spec.model is OutlineNode:
        values, outline_links = _canonical_outline_values(values)
'''
text = replace_once(text, old, new, "public mutation translation")
old = '''    columns = {column.key: column for column in sa_inspect(spec.model).columns}
    allowed = {
        key: _coerce_column_value(columns[key], value)
        for key, value in values.items()
        if key in columns
        and key not in LOCAL_ONLY_COLUMNS
        and (spec.model is not Character or key in CHARACTER_MUTATION_COLUMNS)
    }
'''
new = '''    if spec.model is OutlineNode and "parent_id" in values:
        ensure_no_cycle(db, project_id, entity_id, values.get("parent_id"))

    columns = {column.key: column for column in sa_inspect(spec.model).columns}
    mutation_columns = MUTATION_COLUMNS_BY_MODEL[spec.model]
    allowed = {
        key: _coerce_column_value(columns[key], value)
        for key, value in values.items()
        if key in columns and key in mutation_columns
    }
'''
text = replace_once(text, old, new, "per-model mutation allowlist")
old = '''    db.flush()
    if spec.model is Character and character_aliases is not None:
        sync_character_aliases(db, row, character_aliases)
        db.flush()
'''
new = '''    db.flush()
    if spec.model is Chapter and "content" in allowed:
        row.word_count = count_words(row.content or "")
        db.flush()
    if spec.model is OutlineNode and outline_links is not None:
        replace_character_links(db, project_id, row, outline_links)
        db.flush()
    if spec.model is Character and character_aliases is not None:
        sync_character_aliases(db, row, character_aliases)
        db.flush()
'''
text = replace_once(text, old, new, "derived/core relation projection")
old = '''    "apply_domain_mutation",
    "project_id_for_record",
'''
new = '''    "apply_domain_mutation",
    "domain_snapshot_for_entity",
    "project_id_for_record",
'''
text = replace_once(text, old, new, "gateway exports")
path.write_text(text, encoding="utf-8")


# Sync state must store the canonical domain snapshot, never the client request body.
path = Path("backend/app/modules/gateway/infrastructure/mutation_service.py")
text = path.read_text(encoding="utf-8")
old = '''from app.services.gateway_legacy_replication import apply_domain_mutation
'''
new = '''from app.services.gateway_legacy_replication import apply_domain_mutation, domain_snapshot_for_entity
'''
text = replace_once(text, old, new, "mutation service import")
old = '''        now = utcnow()
        digest = payload_hash(mutation.payload)
        change = SyncChange(
'''
new = '''        now = utcnow()
        effective_payload = mutation.payload
        if mutation.operation != "delete":
            effective_payload = domain_snapshot_for_entity(
                self.db,
                project_id=mutation.project_id,
                entity_type=mutation.entity_type,
                entity_id=mutation.entity_id,
            ) or mutation.payload
        digest = payload_hash(effective_payload)
        change = SyncChange(
'''
text = replace_once(text, old, new, "canonical effective payload")
text = replace_once(
    text,
    '''            payload_json=mutation.payload,
            content_hash=digest,
''',
    '''            payload_json=effective_payload,
            content_hash=digest,
''',
    "canonical change payload",
)
old = '''            digest=digest,
            device_id=device_id,
            now=now,
        )
'''
new = '''            digest=digest,
            payload=effective_payload,
            device_id=device_id,
            now=now,
        )
'''
text = replace_once(text, old, new, "state payload argument")
old = '''        digest: str,
        device_id: str | None,
        now: datetime,
    ) -> None:
        values = {
            "revision": revision,
            "payload_json": mutation.payload,
'''
new = '''        digest: str,
        payload: dict | None,
        device_id: str | None,
        now: datetime,
    ) -> None:
        values = {
            "revision": revision,
            "payload_json": payload,
'''
text = replace_once(text, old, new, "state canonical payload")
path.write_text(text, encoding="utf-8")
