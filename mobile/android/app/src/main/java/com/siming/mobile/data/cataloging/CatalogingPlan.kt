package com.siming.mobile.data.cataloging

import java.util.UUID
import kotlinx.serialization.json.*

internal data class CatalogCandidate(val id: String, val payload: JsonObject) {
    val kind: String get() = payload.text("type")
    fun toJson() = buildJsonObject {
        put("id", id); put("item_type", kind); put("status", "pending"); put("payload", payload)
    }
    companion object {
        fun fromJson(value: JsonObject) = CatalogCandidate(value.text("id"), value.getValue("payload").jsonObject)
    }
}

/** A staged plan. Nothing in this class writes a formal record. */
internal class CatalogingPlan(
    val contract: CatalogingContract,
    val projectId: String,
    val chapterId: String,
    val records: List<CatalogRecord>,
    initial: List<CatalogCandidate> = emptyList(),
) {
    val chapter: JsonObject = record("chapter", chapterId).payload
    val candidates = initial.toMutableList()
    private val summary: JsonObject get() = candidates.singleOrNull { it.kind == "chapter_summary" }?.payload
        ?: error("先提交唯一的 chapter_summary 建档计划")

    init {
        require(chapter.text("project_id") == projectId && chapter.text("content").isNotBlank()) { "建档目标必须是本作品已保存且非空的章节" }
        require(chapter.number("current_version") > 0) { "章节缺少有效的当前版本" }
    }

    fun record(type: String, id: String): CatalogRecord = records.firstOrNull { it.recordType == type && it.id == id }
        ?: error("$type ID=$id 不存在、不属于本作品或类型不正确")

    fun binding(name: String, world: Boolean = false): JsonObject = summary.objects(if (world) "worldbuilding_bindings" else "character_bindings")
        .firstOrNull { it.text("name") == name } ?: error("计划未绑定实体：$name")

    fun submit(args: JsonObject): JsonObject {
        val errors = mutableListOf<JsonObject>()
        val rejected = args.strings("reject_candidate_ids")
        require(rejected.toSet().size == rejected.size && rejected.all { id -> candidates.any { it.id == id } }) {
            "reject_candidate_ids 必须引用本章尚未应用的候选 ID"
        }
        candidates.removeAll { it.id in rejected }
        var saved = 0
        args.objects("candidates").forEach { raw ->
            try {
                contract.validateCandidate(raw)
                if (raw.text("type") == "scene_outline_replace") replaceScenes(raw) else stage(raw)
                saved++
            } catch (e: IllegalArgumentException) { errors += issue(e.message.orEmpty(), raw) }
            catch (e: IllegalStateException) { errors += issue(e.message.orEmpty(), raw) }
        }
        val finalize = args["finalize"] == JsonPrimitive(true)
        val missing = if (finalize) diagnostics() else emptyList()
        return buildJsonObject {
            put("candidates_saved", saved)
            put("candidate_errors", JsonArray(errors))
            put("missing_required_items", jsonStrings(missing))
            put("candidate_set_complete", finalize && errors.isEmpty() && missing.isEmpty())
            put("candidates", JsonArray(candidates.map { buildJsonObject {
                put("id", it.id); put("item_type", it.kind); put("target_id", it.payload.text("id").ifBlank { it.payload.text("client_id") })
            } }))
        }
    }

    private fun stage(raw: JsonObject) {
        val key = slot(raw)
        val old = candidates.firstOrNull { slot(it.payload) == key }
        val merged = JsonObject(old?.payload.orEmpty() + raw).let {
            if (raw.text("type") == "outline_create" && it.text("client_id").isBlank())
                JsonObject(it + ("client_id" to JsonPrimitive(UUID.randomUUID().toString()))) else it
        }
        validate(merged)
        val staged = CatalogCandidate(old?.id ?: UUID.randomUUID().toString(), merged)
        if (old == null) candidates += staged else candidates[candidates.indexOf(old)] = staged
    }

    private fun replaceScenes(raw: JsonObject) {
        val current = candidates.filter { it.kind in OUTLINE_TYPES && it.payload.text("node_type") == "section" }
        require(raw.strings("expected_candidate_ids").toSet() == current.map { it.id }.toSet()) { "场景替换必须引用当前全部场景候选 ID" }
        val replacements = raw.objects("sections")
        require(replacements.map { it.number("scene_number") }.sorted() == (1..summary.objectsOrScenesCount()).toList()) {
            "场景替换必须完整覆盖 1..${summary.objectsOrScenesCount()}"
        }
        replacements.forEach { contract.validateCandidate(it); validate(it) }
        candidates.removeAll(current.toSet())
        replacements.forEach(::stage)
    }

    private fun validate(raw: JsonObject) {
        contract.validateCandidate(raw)
        val kind = raw.text("type")
        val plan = if (kind == "chapter_summary") raw else summary
        val characters = validateBindings(plan, false)
        val world = validateBindings(plan, true)
        fun references(values: List<String>, allowed: Map<String, JsonObject>) {
            require(values.all { it in allowed }) { "存在未在计划中按真实 ID 绑定的引用：${values.filter { it !in allowed }}" }
        }
        when (kind) {
            "chapter_summary" -> {
                val manifest = raw.obj("coverage_manifest")
                require(raw.numberOfScenes() == manifest.number("scene_count")) { "coverage_manifest.scene_count 必须等于 scenes 长度" }
                references(manifest.strings("characters") + manifest.strings("character_profiles") + raw.strings("characters"), characters)
                references(manifest.strings("worldbuilding") + raw.strings("worldbuilding"), world)
                val relations = manifest.objects("relationships")
                relations.forEach { references(listOf(it.text("source_name"), it.text("target_name")), characters) }
                require(relations.map { it.text("source_name") to it.text("target_name") }.distinct().size == relations.size) {
                    "同一有向角色对只能声明一个当前关系"
                }
            }
            "character_relationship" -> {
                references(listOf(raw.text("source_name"), raw.text("target_name")), characters)
                require(raw.text("source_name") != raw.text("target_name")) { "角色关系不能指向自己" }
            }
            "character_merge_candidate" -> {
                references(listOf(raw.text("primary_name"), raw.text("secondary_name")), characters)
                require(raw.text("primary_name") != raw.text("secondary_name")) { "不能把角色合并到自身" }
            }
            "chapter_link" -> {
                val links = raw.objects("characters")
                references(links.map { it.text("name") }, characters)
                require(links.map { it.text("name") }.distinct().size == links.size) { "章节关联中每个角色只能出现一次" }
                references(raw.strings("worldbuilding_titles"), world)
            }
            in OUTLINE_TYPES -> validateOutline(raw, characters.values.map { it.text("id") }.toSet())
            else -> {
                val isWorld = kind.startsWith("worldbuilding_")
                val bindings = if (isWorld) world else characters
                val id = raw.text(if (kind.endsWith("_create")) "client_id" else "id")
                val selected = bindings.values.firstOrNull { it.text("id") == id }
                    ?: error("$kind 的 id/client_id 必须来自当前计划的实体绑定")
                val nameKey = if (isWorld) "title" else "name"
                require(nameKey !in raw || raw.text(nameKey) == selected.text("name")) { "$kind.$nameKey 与所选 ID 的正式名称不一致" }
                if (kind.endsWith("_create")) {
                    require(selected.text("decision") == "new") { "已有实体必须 update" }
                    require(records.none { it.id == id }) { "client_id 已占用，不能重复创建" }
                    require(records.none { it.recordType == (if (isWorld) "world_entry" else "character") &&
                        it.payload.text(nameKey) == selected.text("name") }) { "同名正式档案已存在，请读取真实 ID 后使用 update" }
                }
                val current = records.firstOrNull { it.id == id && it.recordType == if (isWorld) "world_entry" else "character" }?.payload
                if (kind == "character_update" && current != null) preserve(raw, current, "background")
                if (kind == "character_state_update" && current != null) {
                    preserve(raw, current, "items_or_assets")
                    listOf("appearance", "age").forEach { field ->
                        val incoming = raw.text(field)
                        if (incoming.isNotBlank() && incoming != current.text(field)) {
                            require(current.text(field).isBlank() || raw.text("${field}_before") == current.text(field)) { "$field 修改必须逐字确认 ${field}_before" }
                            val evidence = raw.text("${field}_evidence").trim()
                            require(evidence.isNotEmpty() && evidence in chapter.text("content")) { "${field}_evidence 必须是本章正文的逐字引用" }
                        }
                    }
                }
                if (kind == "character_state_update") require(contract.stateFields.any { raw.text(it).isNotBlank() }) {
                    "角色剧情状态更新至少需要一个非空状态字段"
                }
                if (kind in setOf("character_update", "character_create")) require(raw.any { (key, value) ->
                    key in (contract.stateFields + listOf("aliases", "role_type", "personality", "background", "abilities", "profile") + CatalogingProjection.CONFIG_FIELDS) &&
                        value != JsonNull && value != JsonPrimitive("") && value != JsonArray(emptyList()) && value != JsonObject(emptyMap())
                }) { "角色候选没有可写入的档案、状态或写作约束" }
                if (isWorld && kind != "worldbuilding_timeline") require(
                    listOf("content", "description", "evidence").any { raw.text(it).isNotBlank() },
                ) { "世界观内容不能为空" }
            }
        }
    }

    private fun validateBindings(plan: JsonObject, world: Boolean): Map<String, JsonObject> {
        val field = if (world) "worldbuilding_bindings" else "character_bindings"
        val values = plan.objects(field)
        require(values.map { it.text("name") }.distinct().size == values.size && values.map { it.text("id") }.distinct().size == values.size) {
            "$field 的名称与 ID 必须唯一"
        }
        values.forEach { value ->
            val id = value.text("id")
            require(value.text("reason").isNotBlank()) { "$field.reason 不能为空" }
            if (value.text("decision") == "existing") {
                val record = record(if (world) "world_entry" else "character", id)
                require(record.payload.text(if (world) "title" else "name") == value.text("name")) { "$field 的 ID 与正式名称不一致" }
                require(!world || record.payload.text("status").ifBlank { "active" } == "active") { "不能绑定已停用设定" }
            } else {
                require(runCatching { UUID.fromString(id).toString() == id }.getOrDefault(false)) { "新实体 ID 必须是规范 UUID" }
                require(records.none { it.id == id }) { "新实体 ID 已被占用" }
            }
        }
        return values.associateBy { it.text("name") }
    }

    private fun validateOutline(raw: JsonObject, characterIds: Set<String>) {
        require(raw.text("title").isNotBlank() && raw.text("summary").isNotBlank()) { "大纲标题和摘要不能为空" }
        require(raw.strings("character_ids").all { it in characterIds }) { "大纲 character_ids 必须来自计划绑定" }
        val type = raw.text("node_type")
        val linkedId = chapter.text("outline_node_id")
        val linked = linkedId.takeIf(String::isNotBlank)?.let { record("outline_node", it).payload }
        require(linked == null || linked.text("node_type") == "chapter") { "正式章节必须绑定章级大纲" }
        val id = raw.text("id")
        if (raw.text("client_id").isNotBlank()) require(records.none { it.id == raw.text("client_id") }) {
            "大纲 client_id 已被占用"
        }
        if (id.isNotBlank()) {
            val target = record("outline_node", id).payload
            require(target.text("node_type") == type) { "大纲目标类型不匹配" }
            require(type == "volume" || target.text("source_chapter_id") in setOf("", chapterId)) { "大纲已属于其他章节" }
            require(records.none { it.recordType == "chapter" && it.id != chapterId && it.payload.text("outline_node_id") == id }) { "大纲已被其他章节绑定" }
            if (type == "chapter") require(linkedId.isBlank() || linkedId == id) { "必须更新本章已绑定的大纲 ID" }
            if (type == "section") {
                require(linkedId.isNotBlank() && target.text("parent_id") == linkedId) { "场景大纲必须属于本章" }
                val number = target.obj("metadata_json").number("scene_number", target.obj("metadata").number("scene_number"))
                require(number == 0 || number == raw.number("scene_number")) { "场景 ID 与 scene_number 不一致" }
            }
        }
        val parent = raw.text("parent_id")
        if (parent.isNotBlank()) {
            val value = record("outline_node", parent).payload
            require(value.text("node_type") == if (type == "chapter") "volume" else "chapter") { "大纲父级类型错误" }
            if (type == "chapter" && linked != null) require(parent == linked.text("parent_id")) { "已绑定章级大纲必须保持原父级" }
            if (type == "section") require(linkedId.isNotBlank() && parent == linkedId) { "场景父级必须是本章大纲" }
        }
        if (type == "chapter" && linked == null && parent.isBlank()) {
            val volumes = records.filter { it.recordType == "outline_node" && it.payload.text("node_type") == "volume" }
            require(volumes.isEmpty() || volumes.singleOrNull()?.payload?.obj("metadata")?.text("source") == "cataloging_default_volume") {
                "请读取卷索引，由模型明确选择 parent_id"
            }
        }
        if (type == "section") {
            require(raw.number("scene_number") in 1..summary.numberOfScenes()) { "scene_number 越界" }
            require(contract.sceneFields.all { it in raw }) { "场景缺少状态字段：${contract.sceneFields.filter { it !in raw }}" }
        }
    }

    fun diagnostics(): List<String> {
        val missing = mutableListOf<String>()
        candidates.forEach { row -> runCatching { validate(row.payload) }.exceptionOrNull()?.let {
            missing += "候选 ${row.id}（${row.kind}）：${it.message}"
        } }
        val plan = runCatching { summary }.getOrElse { return listOf(it.message.orEmpty()) }
        if (plan.text("summary_text").count { !it.isWhitespace() } < 40) missing += "章节摘要少于 40 个非空白字符"
        val manifest = plan.obj("coverage_manifest")
        val outlines = candidates.filter { it.kind in OUTLINE_TYPES }
        val allocated = candidates.filter { it.kind.endsWith("_create") }.map { it.payload.text("client_id") }.filter(String::isNotBlank)
        if (allocated.distinct().size != allocated.size) missing += "不同新实体必须使用不同的 client_id"
        if (outlines.count { it.payload.text("node_type") == "chapter" } != 1) missing += "每章必须且只能有一个章级大纲候选"
        val scenes = outlines.filter { it.payload.text("node_type") == "section" }
        val count = plan.numberOfScenes()
        if ((count > 1 || scenes.isNotEmpty()) && scenes.map { it.payload.number("scene_number") }.sorted() != (1..count).toList()) {
            missing += "场景大纲必须完整且唯一覆盖 scene_number=1..$count"
        }
        for ((field, create) in listOf("character_bindings" to "character_create", "worldbuilding_bindings" to "worldbuilding_create")) {
            plan.objects(field).filter { it.text("decision") == "new" }.forEach { binding ->
                if (candidates.none { it.kind == create && it.payload.text("client_id") == binding.text("id") }) missing += "新实体 ${binding.text("name")} 缺少 $create"
            }
        }
        manifest.strings("characters").forEach { name ->
            val id = runCatching { binding(name).text("id") }.getOrDefault("")
            if (candidates.none { (it.kind == "character_state_update" && it.payload.text("id") == id) ||
                (it.kind == "character_create" && it.payload.text("client_id") == id && contract.stateFields.any { key -> it.payload.text(key).isNotBlank() }) }) missing += "角色 $name 缺少本章状态候选"
        }
        manifest.strings("character_profiles").forEach { name ->
            val id = runCatching { binding(name).text("id") }.getOrDefault("")
            if (candidates.none { it.kind in setOf("character_create", "character_update") && (it.payload.text("id") == id || it.payload.text("client_id") == id) }) missing += "角色 $name 缺少稳定档案候选"
        }
        manifest.objects("relationships").forEach { relation ->
            if (candidates.none { it.kind == "character_relationship" && listOf("source_name", "target_name", "relationship_type").all { key -> it.payload.text(key) == relation.text(key) } }) missing += "缺少关系候选：$relation"
        }
        val links = candidates.filter { it.kind == "chapter_link" }
        if (links.size > 1 || ((manifest.strings("characters") + manifest.strings("worldbuilding")).isNotEmpty() && links.size != 1)) missing += "必须提交唯一的聚合 chapter_link"
        if (links.size == 1) {
            val link = links.single().payload
            if (!link.objects("characters").map { it.text("name") }.containsAll(manifest.strings("characters"))) missing += "chapter_link 缺少已声明角色"
            if (!link.strings("worldbuilding_titles").containsAll(manifest.strings("worldbuilding"))) missing += "chapter_link 缺少已声明设定"
        }
        return missing.distinct()
    }

    fun requireComplete() { require(diagnostics().isEmpty()) { diagnostics().joinToString("；") } }

    private fun preserve(raw: JsonObject, current: JsonObject, field: String) {
        val before = current.text(field)
        val after = raw.text(field)
        if (after.isNotEmpty() && before.isNotEmpty() && after != before) {
            require(raw.text("${field}_before") == before) { "$field 修改必须用 ${field}_before 逐字复制当前完整值：$before" }
            require(before in after) { "$field 新值必须逐字保留旧内容并追加本章变化" }
        }
    }

    private fun slot(raw: JsonObject): String = when (val type = raw.text("type")) {
        "chapter_summary", "chapter_link" -> type
        in OUTLINE_TYPES -> "outline:${raw.text("node_type")}:${if (raw.text("node_type") == "section") raw.number("scene_number").toString() else if (raw.text("node_type") == "chapter") chapterId else raw.text("id").ifBlank { raw.text("title") }}"
        "character_relationship" -> "$type:${raw.text("source_name")}:${raw.text("target_name")}"
        "character_merge_candidate" -> "$type:${raw.text("primary_name")}:${raw.text("secondary_name")}"
        else -> "$type:${raw.text("id").ifBlank { raw.text("client_id") }}:${if (type.endsWith("_timeline")) raw.text("event_type") + ":" + raw.number("sort_order") else ""}"
    }

    private fun issue(message: String, raw: JsonObject) = buildJsonObject { put("item_type", raw.text("type")); put("message", message) }
    private fun JsonObject.numberOfScenes() = (get("scenes") as? JsonArray)?.size ?: 0
    private fun JsonObject.objectsOrScenesCount() = numberOfScenes()

    companion object { val OUTLINE_TYPES = setOf("outline_create", "outline_update") }
}
