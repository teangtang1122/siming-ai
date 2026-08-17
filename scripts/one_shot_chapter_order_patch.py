from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}: {old[:120]!r}")
    write(path, content.replace(old, new, 1))


# 1) Chapter becomes independently ordered domain data.
replace_once(
    "backend/app/modules/story/infrastructure/entities.py",
    "    word_count = Column(Integer, default=0)\n    current_version = Column(Integer, default=1)\n",
    "    word_count = Column(Integer, default=0)\n    current_version = Column(Integer, default=1)\n"
    "    # Canonical reading order. This is intentionally independent from outline_node_id.\n"
    "    sort_order = Column(Integer, nullable=False, default=0)\n",
)

# 2) Chapter API contracts expose canonical order and a dedicated reorder command.
replace_once(
    "backend/app/schemas/chapter.py",
    "class ChapterDeAiPreviewRequest(BaseModel):\n",
    "class ChapterReorderRequest(BaseModel):\n"
    "    \"\"\"Replace the reading order of all chapters in one project.\"\"\"\n\n"
    "    ids: list[str] = Field(default_factory=list, description=\"Chapter IDs in reading order\")\n\n\n"
    "class ChapterDeAiPreviewRequest(BaseModel):\n",
)
replace_once(
    "backend/app/schemas/chapter.py",
    "    current_version: int\n    outline_title: Optional[str]\n",
    "    current_version: int\n    sort_order: int\n    outline_title: Optional[str]\n",
)

# 3) Chapter serialization includes order.
replace_once(
    "backend/app/services/chapter_service.py",
    "        current_version=chapter.current_version or 1,\n        outline_title=outline_title,\n",
    "        current_version=chapter.current_version or 1,\n        sort_order=chapter.sort_order or 0,\n        outline_title=outline_title,\n",
)

# 4) Application port supports reorder.
replace_once(
    "backend/app/modules/story/application/chapters.py",
    "    def delete(self, project_id: str, chapter_id: str) -> StoryMutation: ...\n\n",
    "    def delete(self, project_id: str, chapter_id: str) -> StoryMutation: ...\n\n"
    "    def reorder(self, project_id: str, chapter_ids: list[str]) -> StoryMutation: ...\n\n",
)

# 5) Persistence stops consulting outline order, appends new chapters, and supports reorder.
replace_once(
    "backend/app/modules/story/infrastructure/chapters.py",
    "from datetime import datetime\n",
    "",
)
replace_once(
    "backend/app/modules/story/infrastructure/chapters.py",
    "    def _validate_manifest(self, project_id: str, manifest_id: str | None) -> None:\n",
    "    def _next_sort_order(self, project_id: str) -> int:\n"
    "        last = (\n"
    "            self._session.query(Chapter)\n"
    "            .filter(Chapter.project_id == project_id)\n"
    "            .order_by(Chapter.sort_order.desc(), Chapter.created_at.desc(), Chapter.id.desc())\n"
    "            .first()\n"
    "        )\n"
    "        return ((last.sort_order or 0) if last else 0) + 1000\n\n"
    "    def _validate_manifest(self, project_id: str, manifest_id: str | None) -> None:\n",
)
old_list = '''    def list(self, project_id: str) -> dict:\n        get_project_or_404(self._session, project_id)\n        outline_context = self._outline_context(project_id)\n        chapters = (\n            self._session.query(Chapter).filter(Chapter.project_id == project_id).all()\n        )\n\n        def sort_key(chapter: Chapter) -> tuple:\n            outline_key = outline_context["sort_keys"].get(chapter.outline_node_id)\n            if outline_key is None:\n                return (1, (999999,), chapter.created_at or datetime.min)\n            return (0, outline_key, chapter.created_at or datetime.min)\n\n        chapters.sort(key=sort_key)\n        items = [chapter_to_list_item(chapter, outline_context) for chapter in chapters]\n        return {"items": items, "total": len(items)}\n'''
new_list = '''    def list(self, project_id: str) -> dict:\n        get_project_or_404(self._session, project_id)\n        outline_context = self._outline_context(project_id)\n        chapters = (\n            self._session.query(Chapter)\n            .filter(Chapter.project_id == project_id)\n            .order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())\n            .all()\n        )\n        items = [chapter_to_list_item(chapter, outline_context) for chapter in chapters]\n        return {"items": items, "total": len(items)}\n'''
replace_once("backend/app/modules/story/infrastructure/chapters.py", old_list, new_list)
replace_once(
    "backend/app/modules/story/infrastructure/chapters.py",
    "            current_version=1,\n            context_manifest_id=payload.get(\"context_manifest_id\"),\n",
    "            current_version=1,\n            sort_order=self._next_sort_order(project_id),\n            context_manifest_id=payload.get(\"context_manifest_id\"),\n",
)
replace_once(
    "backend/app/modules/story/infrastructure/chapters.py",
    "    def delete(self, project_id: str, chapter_id: str) -> StoryMutation:\n",
    '''    def reorder(self, project_id: str, chapter_ids: list[str]) -> StoryMutation:\n        get_project_or_404(self._session, project_id)\n        chapters = (\n            self._session.query(Chapter)\n            .filter(Chapter.project_id == project_id)\n            .order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())\n            .all()\n        )\n        existing_ids = [chapter.id for chapter in chapters]\n        requested_ids = [str(chapter_id) for chapter_id in chapter_ids]\n        if len(requested_ids) != len(set(requested_ids)):\n            raise ValidationError("章节排序中不能包含重复章节")\n        if set(requested_ids) != set(existing_ids):\n            raise ValidationError("章节排序必须包含当前作品的全部章节")\n\n        by_id = {chapter.id: chapter for chapter in chapters}\n        for index, chapter_id in enumerate(requested_ids, start=1):\n            by_id[chapter_id].sort_order = index * 1000\n        self._session.flush()\n        return StoryMutation(data=self.list(project_id), sync_intents=[])\n\n    def delete(self, project_id: str, chapter_id: str) -> StoryMutation:\n''',
)

# 6) HTTP endpoint is separate from normal chapter edits.
replace_once(
    "backend/app/routers/chapters.py",
    "    ChapterQualityScoreRequest,\n    ChapterUpdate,\n",
    "    ChapterQualityScoreRequest,\n    ChapterReorderRequest,\n    ChapterUpdate,\n",
)
replace_once(
    "backend/app/routers/chapters.py",
    "@router.get(\"/projects/{project_id}/chapters/{chapter_id}\")\ndef get_chapter_detail(\n",
    '''@router.put("/projects/{project_id}/chapters/reorder")\ndef reorder_chapters(\n    project_id: str,\n    payload: ChapterReorderRequest,\n    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],\n    command: Annotated[StoryCommandContext, Depends(get_story_command)],\n):\n    result = workspace.reorder(project_id, payload.ids)\n    command.queue_all(result.sync_intents)\n    command.finish()\n    return ApiResponse.success(data=result.data, message="章节顺序已更新")\n\n\n@router.get("/projects/{project_id}/chapters/{chapter_id}")\ndef get_chapter_detail(\n''',
)

# 7) Exports follow the same canonical chapter order as the writer and mobile app.
replace_once(
    "backend/app/services/export_service.py",
    "from .outline_service import load_outline_nodes, outline_sort_context\n",
    "",
)
old_export_order = '''    \"\"\"Return chapters in the same outline order used by the writing workspace.\"\"\"\n    outline_context = outline_sort_context(load_outline_nodes(db, project_id))\n    query = db.query(Chapter).filter(Chapter.project_id == project_id)\n    if chapter_ids:\n        unique_ids = list(dict.fromkeys(chapter_ids))\n        query = query.filter(Chapter.id.in_(unique_ids))\n    chapters = query.all()\n    if chapter_ids and len(chapters) != len(set(chapter_ids)):\n        raise ValidationError("导出章节必须属于当前作品")\n\n    def sort_key(chapter: Chapter):\n        outline_key = outline_context["sort_keys"].get(chapter.outline_node_id)\n        if outline_key is None:\n            return (1, (999999,), chapter.created_at or datetime.min)\n        return (0, outline_key, chapter.created_at or datetime.min)\n\n    chapters.sort(key=sort_key)\n    return chapters\n'''
new_export_order = '''    \"\"\"Return chapters in canonical reading order, independent from outline order.\"\"\"\n    query = db.query(Chapter).filter(Chapter.project_id == project_id)\n    if chapter_ids:\n        unique_ids = list(dict.fromkeys(chapter_ids))\n        query = query.filter(Chapter.id.in_(unique_ids))\n    chapters = query.order_by(\n        Chapter.sort_order.asc(),\n        Chapter.created_at.asc(),\n        Chapter.id.asc(),\n    ).all()\n    if chapter_ids and len(chapters) != len(set(chapter_ids)):\n        raise ValidationError("导出章节必须属于当前作品")\n    return chapters\n'''
replace_once("backend/app/services/export_service.py", old_export_order, new_export_order)

# 8) Old mobile clients that sync a new chapter without sort_order append safely on PC.
replace_once(
    "backend/app/services/gateway_legacy_replication.py",
    "from sqlalchemy import Date, DateTime\n",
    "from sqlalchemy import Date, DateTime, func\n",
)
replace_once(
    "backend/app/services/gateway_legacy_replication.py",
    "    for key, value in (spec.defaults or {}).items():\n        allowed.setdefault(key, value)\n    _assert_parent_project(db, spec, allowed, project_id)\n",
    '''    for key, value in (spec.defaults or {}).items():\n        allowed.setdefault(key, value)\n    if spec.model is Chapter and row is None and "sort_order" not in allowed:\n        highest = (\n            db.query(func.max(Chapter.sort_order))\n            .filter(Chapter.project_id == project_id)\n            .scalar()\n            or 0\n        )\n        allowed["sort_order"] = int(highest) + 1000\n    _assert_parent_project(db, spec, allowed, project_id)\n''',
)

# 9) Preserve the old PC-visible order exactly once during migration, then stop inferring order.
migration = '''\"\"\"Give chapters an independent canonical reading order.\n\nRevision ID: 300a17_chapter_sort_order\nRevises: 300a16_character_role_type_enum\n\"\"\"\n\nfrom __future__ import annotations\n\nfrom datetime import datetime\n\nfrom alembic import op\nimport sqlalchemy as sa\n\nrevision = "300a17_chapter_sort_order"\ndown_revision = "300a16_character_role_type_enum"\nbranch_labels = None\ndepends_on = None\n\n\ndef _time_key(value):\n    return value or datetime.min\n\n\ndef upgrade() -> None:\n    op.add_column(\n        "chapters",\n        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),\n    )\n    bind = op.get_bind()\n    chapters = sa.table(\n        "chapters",\n        sa.column("id", sa.String()),\n        sa.column("project_id", sa.String()),\n        sa.column("outline_node_id", sa.String()),\n        sa.column("sort_order", sa.Integer()),\n        sa.column("created_at", sa.DateTime()),\n    )\n    outlines = sa.table(\n        "outline_nodes",\n        sa.column("id", sa.String()),\n        sa.column("project_id", sa.String()),\n        sa.column("parent_id", sa.String()),\n        sa.column("sort_order", sa.Integer()),\n        sa.column("created_at", sa.DateTime()),\n    )\n\n    project_ids = [\n        row[0]\n        for row in bind.execute(sa.select(chapters.c.project_id).distinct()).all()\n        if row[0]\n    ]\n    for project_id in project_ids:\n        outline_rows = bind.execute(\n            sa.select(\n                outlines.c.id,\n                outlines.c.parent_id,\n                outlines.c.sort_order,\n                outlines.c.created_at,\n            ).where(outlines.c.project_id == project_id)\n        ).mappings().all()\n        children: dict[str | None, list] = {}\n        for row in outline_rows:\n            children.setdefault(row["parent_id"], []).append(row)\n        for siblings in children.values():\n            siblings.sort(\n                key=lambda row: (\n                    row["sort_order"] or 0,\n                    _time_key(row["created_at"]),\n                    str(row["id"]),\n                )\n            )\n\n        outline_keys: dict[str, tuple[int, ...]] = {}\n\n        def walk(parent_id: str | None, prefix: tuple[int, ...]) -> None:\n            for index, row in enumerate(children.get(parent_id, [])):\n                key = (*prefix, index)\n                outline_keys[str(row["id"])] = key\n                walk(str(row["id"]), key)\n\n        walk(None, ())\n        chapter_rows = bind.execute(\n            sa.select(\n                chapters.c.id,\n                chapters.c.outline_node_id,\n                chapters.c.created_at,\n            ).where(chapters.c.project_id == project_id)\n        ).mappings().all()\n\n        def old_pc_sort_key(row):\n            outline_key = outline_keys.get(str(row["outline_node_id"])) if row["outline_node_id"] else None\n            if outline_key is None:\n                return (1, (999999,), _time_key(row["created_at"]), str(row["id"]))\n            return (0, outline_key, _time_key(row["created_at"]), str(row["id"]))\n\n        for index, row in enumerate(sorted(chapter_rows, key=old_pc_sort_key), start=1):\n            bind.execute(\n                chapters.update()\n                .where(chapters.c.id == row["id"])\n                .values(sort_order=index * 1000)\n            )\n\n\ndef downgrade() -> None:\n    op.drop_column("chapters", "sort_order")\n'''
write("backend/alembic/versions/300a17_chapter_sort_order.py", migration)

# 10) Backend regression: outline and chapter order are explicitly independent.
replace_once(
    "backend/tests/test_chapters.py",
    "  - Chapter CRUD and outline-ordered list\n",
    "  - Chapter CRUD and independently ordered chapter list\n",
)
replace_once(
    "backend/tests/test_chapters.py",
    "        self.assertEqual(chapter[\"current_version\"], 1)\n        self.assertEqual(chapter[\"snapshot_count\"], 1)\n",
    "        self.assertEqual(chapter[\"current_version\"], 1)\n        self.assertEqual(chapter[\"sort_order\"], 1000)\n        self.assertEqual(chapter[\"snapshot_count\"], 1)\n",
)
old_test = '''    def test_list_chapters_ordered_by_outline_tree(self):\n        project_id = self.create_project()\n        volume = self.create_outline_node(project_id, "Volume One", "volume")\n        second_outline = self.create_outline_node(\n            project_id,\n            "Second Outline",\n            "chapter",\n            parent_id=volume["id"],\n            sort_order=1,\n        )\n        first_outline = self.create_outline_node(\n            project_id,\n            "First Outline",\n            "chapter",\n            parent_id=volume["id"],\n            sort_order=0,\n        )\n        self.create_chapter(project_id, "Second Chapter", second_outline["id"])\n        self.create_chapter(project_id, "Unlinked Chapter")\n        self.create_chapter(project_id, "First Chapter", first_outline["id"])\n\n        response = self.client.get(f"{API_PREFIX}/projects/{project_id}/chapters")\n        self.assertEqual(response.status_code, 200)\n\n        titles = [item["title"] for item in response.json()["data"]["items"]]\n        self.assertEqual(titles, ["First Chapter", "Second Chapter", "Unlinked Chapter"])\n        first = response.json()["data"]["items"][0]\n        self.assertEqual(first["outline_path"], ["Volume One", "First Outline"])\n'''
new_test = '''    def test_list_chapters_keeps_reading_order_independent_from_outline_tree(self):\n        project_id = self.create_project()\n        volume = self.create_outline_node(project_id, "Volume One", "volume")\n        second_outline = self.create_outline_node(\n            project_id,\n            "Second Outline",\n            "chapter",\n            parent_id=volume["id"],\n            sort_order=1,\n        )\n        first_outline = self.create_outline_node(\n            project_id,\n            "First Outline",\n            "chapter",\n            parent_id=volume["id"],\n            sort_order=0,\n        )\n        second = self.create_chapter(project_id, "Second Chapter", second_outline["id"])\n        unlinked = self.create_chapter(project_id, "Unlinked Chapter")\n        first = self.create_chapter(project_id, "First Chapter", first_outline["id"])\n\n        response = self.client.get(f"{API_PREFIX}/projects/{project_id}/chapters")\n        self.assertEqual(response.status_code, 200)\n        items = response.json()["data"]["items"]\n        self.assertEqual(\n            [item["title"] for item in items],\n            ["Second Chapter", "Unlinked Chapter", "First Chapter"],\n        )\n        self.assertEqual([item["sort_order"] for item in items], [1000, 2000, 3000])\n        self.assertEqual(items[2]["outline_path"], ["Volume One", "First Outline"])\n\n        reordered = self.client.put(\n            f"{API_PREFIX}/projects/{project_id}/chapters/reorder",\n            json={"ids": [first["id"], second["id"], unlinked["id"]]},\n        )\n        self.assertEqual(reordered.status_code, 200, reordered.text)\n        reordered_items = reordered.json()["data"]["items"]\n        self.assertEqual(\n            [item["title"] for item in reordered_items],\n            ["First Chapter", "Second Chapter", "Unlinked Chapter"],\n        )\n        self.assertEqual(\n            [item["sort_order"] for item in reordered_items],\n            [1000, 2000, 3000],\n        )\n\n        # Changing the outline hierarchy after writing must not reorder正文.\n        response = self.client.put(\n            f"{API_PREFIX}/projects/{project_id}/outline/{first_outline['id']}",\n            json={"sort_order": 9},\n        )\n        self.assertEqual(response.status_code, 200, response.text)\n        response = self.client.get(f"{API_PREFIX}/projects/{project_id}/chapters")\n        self.assertEqual(\n            [item["title"] for item in response.json()["data"]["items"]],\n            ["First Chapter", "Second Chapter", "Unlinked Chapter"],\n        )\n'''
replace_once("backend/tests/test_chapters.py", old_test, new_test)

# 11) Android consumes canonical sort_order. Title parsing survives only as a legacy fallback.
write(
    "mobile/android/app/src/main/java/com/siming/mobile/data/local/ReplicaOrdering.kt",
    '''package com.siming.mobile.data.local\n\nimport java.text.Normalizer\nimport kotlinx.serialization.json.Json\nimport kotlinx.serialization.json.JsonObject\nimport kotlinx.serialization.json.contentOrNull\nimport kotlinx.serialization.json.intOrNull\nimport kotlinx.serialization.json.jsonPrimitive\n\n/**\n * Return chapter replicas in canonical reading order.\n *\n * New PC/Gateway snapshots carry Chapter.sort_order, which is the only\n * authoritative cross-device ordering signal. Title parsing remains strictly\n * as a compatibility fallback for old/offline replicas that predate that field.\n */\nfun orderReplicaEntities(entityType: String, records: List<ReplicaEntity>): List<ReplicaEntity> {\n    if (entityType != "chapter" || records.size < 2) return records\n\n    val items = records.map { record -> ChapterOrder(record, payload(record)) }\n    val canonical = items.filter { it.sortOrder != null }\n    if (canonical.isNotEmpty()) {\n        val legacy = legacyOrder(items.filter { it.sortOrder == null })\n        return canonical.sortedWith(canonicalChapterOrder).map(ChapterOrder::record) +\n            legacy.map(ChapterOrder::record)\n    }\n    return legacyOrder(items).map(ChapterOrder::record)\n}\n\nprivate data class ChapterOrder(\n    val record: ReplicaEntity,\n    val payload: JsonObject?,\n) {\n    val sortOrder = payload?.get("sort_order")?.jsonPrimitive?.let { value ->\n        value.intOrNull ?: value.contentOrNull?.toIntOrNull()\n    }\n    val createdAt = payload?.string("created_at")?.takeIf(String::isNotBlank)\n    val titleNumber = payload?.string("title")?.let(::chapterNumber)\n}\n\nprivate val canonicalChapterOrder =\n    compareBy<ChapterOrder> { it.sortOrder ?: Int.MAX_VALUE }\n        .thenBy { it.createdAt == null }\n        .thenBy { it.createdAt.orEmpty() }\n        .thenBy { it.record.entityId }\n\nprivate fun legacyOrder(items: List<ChapterOrder>): List<ChapterOrder> {\n    val fallbackOrdered = items.sortedWith(chapterFallbackOrder)\n    if (fallbackOrdered.count { it.titleNumber != null } < 2) return fallbackOrdered\n    val numbered = fallbackOrdered\n        .filter { it.titleNumber != null }\n        .sortedWith(numberedChapterOrder)\n        .iterator()\n    return fallbackOrdered.map { item ->\n        if (item.titleNumber == null) item else numbered.next()\n    }\n}\n\nprivate val chapterFallbackOrder =\n    compareBy<ChapterOrder> { it.createdAt == null }\n        .thenBy { it.createdAt.orEmpty() }\n        .thenBy { it.record.localModifiedAt }\n        .thenBy { it.record.entityId }\n\nprivate val numberedChapterOrder =\n    compareBy<ChapterOrder> { it.titleNumber ?: Int.MAX_VALUE }\n        .thenBy { it.createdAt == null }\n        .thenBy { it.createdAt.orEmpty() }\n        .thenBy { it.record.localModifiedAt }\n        .thenBy { it.record.entityId }\n\nprivate const val MAX_CHAPTER_NUMBER = 99_999\nprivate const val CHINESE_NUMBER_CHARS = "零〇○一二两三四五六七八九十百千万"\nprivate val chineseDigits = mapOf(\n    '零' to 0,\n    '〇' to 0,\n    '○' to 0,\n    '一' to 1,\n    '二' to 2,\n    '两' to 2,\n    '三' to 3,\n    '四' to 4,\n    '五' to 5,\n    '六' to 6,\n    '七' to 7,\n    '八' to 8,\n    '九' to 9,\n)\nprivate val chineseUnits = mapOf(\n    '十' to 10,\n    '百' to 100,\n    '千' to 1_000,\n    '万' to 10_000,\n)\nprivate val chapterNumberToken =\n    """[0-9０-９$CHINESE_NUMBER_CHARS](?:[0-9０-９$CHINESE_NUMBER_CHARS]|\\s)*?"""\nprivate val chapterNumberPatterns = listOf(\n    Regex("""第\\s*($chapterNumberToken)\\s*[章节回]"""),\n    Regex("""(?:^|\\s)($chapterNumberToken)\\s*[章节回]"""),\n    Regex("""(?:chapter|chap\\.?)\\s*(\\d{1,5})""", RegexOption.IGNORE_CASE),\n    Regex("""^\\s*(\\d{1,5})\\b"""),\n)\n\nprivate fun chapterNumber(title: String): Int? {\n    val normalized = Normalizer.normalize(title, Normalizer.Form.NFKC)\n    return chapterNumberPatterns.firstNotNullOfOrNull { pattern ->\n        pattern.find(normalized)\n            ?.groupValues\n            ?.getOrNull(1)\n            ?.let(::parseChapterNumber)\n    }\n}\n\nprivate fun parseChapterNumber(text: String): Int? {\n    val number = chineseNumberToInt(text) ?: return null\n    return number.takeIf { it in 1..MAX_CHAPTER_NUMBER }\n}\n\nprivate fun chineseNumberToInt(text: String): Int? {\n    val value = Regex("""\\s+""").replace(\n        Normalizer.normalize(text, Normalizer.Form.NFKC),\n        "",\n    )\n    if (value.isBlank() || value.length > 32) return null\n    value.toIntOrNull()?.let { return it }\n    if (value.any { it !in chineseDigits && it !in chineseUnits }) return null\n\n    if (value.none { it in chineseUnits }) {\n        return value\n            .map { chineseDigits[it] ?: return null }\n            .joinToString("")\n            .toIntOrNull()\n    }\n\n    var total = 0L\n    var section = 0L\n    var number: Int? = null\n    var lastSmallUnit = 10_000\n    var seenWan = false\n    for (char in value) {\n        val digit = chineseDigits[char]\n        if (digit != null) {\n            if (number != null && (number != 0 || digit == 0)) return null\n            if (digit == 0 && total == 0L && section == 0L && number == null) return null\n            number = digit\n            continue\n        }\n\n        val unit = chineseUnits[char] ?: return null\n        if (unit == 10_000) {\n            if (seenWan) return null\n            var base = section + (number ?: 0)\n            if (base == 0L) base = 1L\n            total += base * unit\n            section = 0L\n            number = null\n            lastSmallUnit = 10_000\n            seenWan = true\n        } else {\n            if (unit >= lastSmallUnit || number == 0) return null\n            section += (number ?: 1) * unit\n            number = null\n            lastSmallUnit = unit\n        }\n    }\n    if (number == 0) return null\n    val result = total + section + (number ?: 0)\n    return result.takeIf { it <= Int.MAX_VALUE }?.toInt()\n}\n\nprivate fun payload(record: ReplicaEntity): JsonObject? = record.payloadJson?.let { raw ->\n    runCatching { replicaJson.parseToJsonElement(raw) as? JsonObject }.getOrNull()\n}\n\nprivate fun JsonObject.string(name: String): String = get(name)?.jsonPrimitive?.contentOrNull.orEmpty()\n\nprivate val replicaJson = Json {\n    ignoreUnknownKeys = true\n}\n''',
)
replace_once(
    "mobile/android/app/src/test/java/com/siming/mobile/data/local/ReplicaOrderingTest.kt",
    "class ReplicaOrderingTest {\n",
    '''class ReplicaOrderingTest {\n    @Test\n    fun canonicalSortOrderBeatsTitleAndCreationTime() {\n        val records = listOf(\n            chapter("third", "第一章（改名）", "2026-08-16T10:00:01.000000Z", 1_000, 3_000),\n            chapter("first", "尾声", "2026-08-16T10:00:03.000000Z", 3_000, 1_000),\n            chapter("second", "第99章", "2026-08-16T10:00:02.000000Z", 2_000, 2_000),\n        )\n\n        assertEquals(\n            listOf("first", "second", "third"),\n            orderReplicaEntities("chapter", records).map(ReplicaEntity::entityId),\n        )\n    }\n\n''',
)
replace_once(
    "mobile/android/app/src/test/java/com/siming/mobile/data/local/ReplicaOrderingTest.kt",
    '''    private fun chapter(id: String, title: String, createdAt: String?, localModifiedAt: Long): ReplicaEntity {\n        val createdAtField = createdAt?.let { "\\\"created_at\\\":\\\"$it\\\"," } ?: ""\n''',
    '''    private fun chapter(\n        id: String,\n        title: String,\n        createdAt: String?,\n        localModifiedAt: Long,\n        sortOrder: Int? = null,\n    ): ReplicaEntity {\n        val createdAtField = createdAt?.let { "\\\"created_at\\\":\\\"$it\\\"," } ?: ""\n        val sortOrderField = sortOrder?.let { "\\\"sort_order\\\":$it," } ?: ""\n''',
)
replace_once(
    "mobile/android/app/src/test/java/com/siming/mobile/data/local/ReplicaOrderingTest.kt",
    "            payloadJson = \"{$createdAtField\\\"title\\\":\\\"$title\\\"}\",\n",
    "            payloadJson = \"{$createdAtField$sortOrderField\\\"title\\\":\\\"$title\\\"}\",\n",
)

# 12) PC writer lets authors reorder chapters directly; outline links remain metadata only.
replace_once(
    "frontend/src/pages/WriterPage.tsx",
    "  AuditOutlined,\n",
    "  ArrowDownOutlined,\n  ArrowUpOutlined,\n  AuditOutlined,\n",
)
replace_once(
    "frontend/src/pages/WriterPage.tsx",
    "  current_version: number\n  outline_title?: string | null\n",
    "  current_version: number\n  sort_order: number\n  outline_title?: string | null\n",
)
replace_once(
    "frontend/src/pages/WriterPage.tsx",
    "  const [chapters, setChapters] = useState<ChapterItem[]>([])\n",
    "  const [chapters, setChapters] = useState<ChapterItem[]>([])\n"
    "  const [draggedChapterId, setDraggedChapterId] = useState<string | null>(null)\n"
    "  const [dragOverChapterId, setDragOverChapterId] = useState<string | null>(null)\n"
    "  const [reordering, setReordering] = useState(false)\n",
)
replace_once(
    "frontend/src/pages/WriterPage.tsx",
    "  const restoreSnapshot = async (snapshotId: string) => {\n",
    '''  const saveChapterOrder = async (nextChapters: ChapterItem[]) => {\n    if (reordering) return\n    const previous = chapters\n    const optimistic = nextChapters.map((chapter, index) => ({\n      ...chapter,\n      sort_order: (index + 1) * 1000,\n    }))\n    setChapters(optimistic)\n    setReordering(true)\n    try {\n      const res = await apiClient.put<ApiResponse<{ items: ChapterItem[]; total: number }>>(\n        `/projects/${projectId}/chapters/reorder`,\n        { ids: optimistic.map((chapter) => chapter.id) },\n      )\n      setChapters(res.data.data.items)\n      message.success('正文顺序已更新')\n    } catch (err: any) {\n      setChapters(previous)\n      message.error(err.message || '调整正文顺序失败')\n    } finally {\n      setReordering(false)\n    }\n  }\n\n  const moveChapterByOffset = (chapterId: string, offset: -1 | 1) => {\n    const index = chapters.findIndex((chapter) => chapter.id === chapterId)\n    const target = index + offset\n    if (index < 0 || target < 0 || target >= chapters.length || reordering) return\n    const next = [...chapters]\n    ;[next[index], next[target]] = [next[target], next[index]]\n    void saveChapterOrder(next)\n  }\n\n  const placeChapterBefore = (sourceId: string, targetId: string) => {\n    if (sourceId === targetId || reordering) return\n    const next = [...chapters]\n    const sourceIndex = next.findIndex((chapter) => chapter.id === sourceId)\n    if (sourceIndex < 0) return\n    const [moved] = next.splice(sourceIndex, 1)\n    const targetIndex = next.findIndex((chapter) => chapter.id === targetId)\n    if (targetIndex < 0) return\n    next.splice(targetIndex, 0, moved)\n    void saveChapterOrder(next)\n  }\n\n  const restoreSnapshot = async (snapshotId: string) => {\n''',
)
old_render = '''            renderItem={(chapter) => (\n              <List.Item\n                className={`writer-chapter-item${chapter.id === selectedId ? ' writer-chapter-item-active' : ''}`}\n                role="button"\n                tabIndex={0}\n                aria-label={`打开章节：${chapter.title}`}\n                onClick={() => confirmLeave(() => setSelectedId(chapter.id))}\n                onKeyDown={(event) => {\n                  if (event.key !== 'Enter' && event.key !== ' ') return\n                  event.preventDefault()\n                  confirmLeave(() => setSelectedId(chapter.id))\n                }}\n              >\n                <List.Item.Meta\n                  title={<span className="writer-chapter-title" title={chapter.title}>{chapter.title}</span>}\n                  description={\n                    <div className="writer-chapter-meta">\n                      <Text type="secondary" ellipsis title={chapter.outline_path.join(' / ')}>{chapter.outline_path.length > 0 ? chapter.outline_path.join(' / ') : '未关联大纲'}</Text>\n                      <div className="writer-chapter-facts">\n                        <span>{chapter.word_count} 字</span>\n                        <span>v{chapter.current_version}</span>\n                        {chapter.outline_status && <Tag color={STATUS_COLOR[chapter.outline_status] || 'default'}>{chapterStatusLabel(chapter.outline_status)}</Tag>}\n                      </div>\n                    </div>\n                  }\n                />\n              </List.Item>\n            )}\n'''
new_render = '''            renderItem={(chapter, index) => (\n              <List.Item\n                className={`writer-chapter-item${chapter.id === selectedId ? ' writer-chapter-item-active' : ''}${chapter.id === dragOverChapterId ? ' writer-chapter-item-drag-over' : ''}`}\n                role="button"\n                tabIndex={0}\n                aria-label={`打开章节：${chapter.title}`}\n                title="拖动章节卡片，或使用上下按钮调整正文顺序"\n                draggable={!loading && !reordering}\n                onDragStart={(event) => {\n                  setDraggedChapterId(chapter.id)\n                  event.dataTransfer.effectAllowed = 'move'\n                  event.dataTransfer.setData('text/plain', chapter.id)\n                }}\n                onDragOver={(event) => {\n                  if (!draggedChapterId || draggedChapterId === chapter.id) return\n                  event.preventDefault()\n                  event.dataTransfer.dropEffect = 'move'\n                  setDragOverChapterId(chapter.id)\n                }}\n                onDrop={(event) => {\n                  event.preventDefault()\n                  const sourceId = draggedChapterId || event.dataTransfer.getData('text/plain')\n                  setDraggedChapterId(null)\n                  setDragOverChapterId(null)\n                  if (sourceId) placeChapterBefore(sourceId, chapter.id)\n                }}\n                onDragEnd={() => {\n                  setDraggedChapterId(null)\n                  setDragOverChapterId(null)\n                }}\n                onClick={() => confirmLeave(() => setSelectedId(chapter.id))}\n                onKeyDown={(event) => {\n                  if (event.key !== 'Enter' && event.key !== ' ') return\n                  event.preventDefault()\n                  confirmLeave(() => setSelectedId(chapter.id))\n                }}\n              >\n                <div className="writer-chapter-order-controls" aria-label={`调整章节顺序：${chapter.title}`}>\n                  <Tooltip title="上移">\n                    <Button\n                      type="text"\n                      size="small"\n                      icon={<ArrowUpOutlined />}\n                      aria-label={`上移章节：${chapter.title}`}\n                      disabled={index === 0 || reordering}\n                      onClick={(event) => {\n                        event.stopPropagation()\n                        moveChapterByOffset(chapter.id, -1)\n                      }}\n                    />\n                  </Tooltip>\n                  <Tooltip title="下移">\n                    <Button\n                      type="text"\n                      size="small"\n                      icon={<ArrowDownOutlined />}\n                      aria-label={`下移章节：${chapter.title}`}\n                      disabled={index === chapters.length - 1 || reordering}\n                      onClick={(event) => {\n                        event.stopPropagation()\n                        moveChapterByOffset(chapter.id, 1)\n                      }}\n                    />\n                  </Tooltip>\n                </div>\n                <List.Item.Meta\n                  title={<span className="writer-chapter-title" title={chapter.title}>{chapter.title}</span>}\n                  description={\n                    <div className="writer-chapter-meta">\n                      <Text type="secondary" ellipsis title={chapter.outline_path.join(' / ')}>{chapter.outline_path.length > 0 ? chapter.outline_path.join(' / ') : '未关联大纲'}</Text>\n                      <div className="writer-chapter-facts">\n                        <span>{chapter.word_count} 字</span>\n                        <span>v{chapter.current_version}</span>\n                        {chapter.outline_status && <Tag color={STATUS_COLOR[chapter.outline_status] || 'default'}>{chapterStatusLabel(chapter.outline_status)}</Tag>}\n                      </div>\n                    </div>\n                  }\n                />\n              </List.Item>\n            )}\n'''
replace_once("frontend/src/pages/WriterPage.tsx", old_render, new_render)

replace_once(
    "frontend/src/pages/WriterPage.css",
    ".writer-chapter-item .ant-list-item-meta {\n  min-width: 0;\n}\n",
    '''.writer-chapter-item .ant-list-item-meta {\n  min-width: 0;\n}\n\n.writer-chapter-order-controls {\n  display: flex;\n  flex: 0 0 auto;\n  flex-direction: column;\n  margin-right: 4px;\n}\n\n.writer-chapter-order-controls .ant-btn {\n  color: var(--ant-color-text-tertiary);\n  height: 22px;\n  width: 24px;\n}\n\n.writer-chapter-item[draggable=\"true\"] {\n  cursor: grab;\n}\n\n.writer-chapter-item[draggable=\"true\"]:active {\n  cursor: grabbing;\n}\n\n.writer-chapter-item-drag-over {\n  box-shadow: inset 0 2px 0 var(--siming-accent, var(--ant-color-primary));\n}\n''',
)

replace_once(
    "frontend/src/__tests__/WriterPage.test.tsx",
    "  current_version: 1,\n  outline_title: null,\n",
    "  current_version: 1,\n  sort_order: 1000,\n  outline_title: null,\n",
)
replace_once(
    "frontend/src/__tests__/WriterPage.test.tsx",
    "  it('previews without writing, then saves an explicitly applied candidate as de_ai', async () => {\n",
    '''  it('reorders正文 independently from outline links', async () => {\n    const secondChapter = {\n      ...chapter,\n      id: 'chapter-2',\n      title: '第二章',\n      sort_order: 2000,\n      content: '第二章正文。',\n    }\n    api.get.mockImplementation((url: string) => {\n      if (url.endsWith('/outline')) return Promise.resolve(response({ items: [], flat: [], total: 0 }))\n      if (url.endsWith('/chapters')) return Promise.resolve(response({ items: [chapter, secondChapter], total: 2 }))\n      if (url.endsWith('/snapshots')) return Promise.resolve(response({ items: [], total: 0 }))\n      if (url.endsWith('/chapters/chapter-1')) return Promise.resolve(response(chapter))\n      throw new Error(`Unexpected GET ${url}`)\n    })\n    api.put.mockImplementation((url: string, payload: { ids?: string[] }) => {\n      if (url.endsWith('/chapters/reorder')) {\n        expect(payload.ids).toEqual(['chapter-2', 'chapter-1'])\n        return Promise.resolve(response({\n          items: [\n            { ...secondChapter, sort_order: 1000 },\n            { ...chapter, sort_order: 2000 },\n          ],\n          total: 2,\n        }))\n      }\n      throw new Error(`Unexpected PUT ${url}`)\n    })\n\n    render(<WriterPage projectId="project-1" />)\n    const up = await screen.findByRole('button', { name: '上移章节：第二章' })\n    fireEvent.click(up)\n\n    await waitFor(() => expect(api.put).toHaveBeenCalledWith(\n      '/projects/project-1/chapters/reorder',\n      { ids: ['chapter-2', 'chapter-1'] },\n    ))\n    expect(await screen.findByText('正文顺序已更新')).toBeInTheDocument()\n  })\n\n  it('previews without writing, then saves an explicitly applied candidate as de_ai', async () => {\n''',
)

# Temporary one-shot machinery should not remain in the PR diff.
for transient in [
    ROOT / "scripts/one_shot_chapter_order_patch.py",
    ROOT / ".github/workflows/one-shot-chapter-order.yml",
]:
    if transient.exists():
        transient.unlink()

print("Independent chapter ordering patch applied.")
