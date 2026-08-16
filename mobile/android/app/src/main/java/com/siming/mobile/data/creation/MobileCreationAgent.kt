package com.siming.mobile.data.creation

import android.content.Context
import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.data.network.DirectApiConfig
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import kotlin.math.max
import kotlin.math.min
import kotlin.math.round

/** Standalone execution of the same V3 interview/stage prompt contract as PC. */
internal class MobileCreationAgent(
    private val contract: PcCreationPromptContract,
    private val directApi: DirectApiClient,
) {
    constructor(context: Context, directApi: DirectApiClient) : this(PcCreationPromptContract(context), directApi)

    internal constructor(contractJson: String, directApi: DirectApiClient) :
        this(PcCreationPromptContract(contractJson), directApi)

    private val json = Json { ignoreUnknownKeys = true }

    fun start(input: CreationStartInput): JsonObject {
        require(input.brief.isNotBlank()) { "先用一两句话告诉 AI 你想写什么" }
        val now = Instant.now().toString()
        val sessionId = UUID.randomUUID().toString()
        val defaults = contract.presetDefaults(input.presetId)
        val form = buildJsonObject {
            put("brief", input.brief.trim())
            put("preset_id", input.presetId)
            put("theme_id", input.themeId)
            put("genre", input.genre.trim().ifBlank { contract.presetLabel(input.presetId).ifBlank { "自由创作" } })
            put("target_audience", input.targetAudience.trim().ifBlank { "成年大众" })
            put("platform", input.platform.trim().ifBlank { "暂不确定" })
            put("target_words", input.targetWords)
            put("target_chapters", input.targetChapters)
            put("opening_chapters", 3)
            put("world_tone", input.worldTone.trim().ifBlank { defaults.string("world_tone") })
            put("story_structure", input.storyStructure.trim().ifBlank { defaults.string("story_structure") })
            put("pacing", input.pacing.trim().ifBlank { defaults.string("pacing") })
            put("writing_style", input.writingStyle.trim().ifBlank { defaults.string("writing_style") })
            put(
                "special_requirements",
                if (input.specialRequirements.isNotEmpty()) strings(input.specialRequirements)
                else defaults["special_requirements"] ?: buildJsonArray {},
            )
            put(
                "avoid",
                if (input.avoid.isNotEmpty()) strings(input.avoid)
                else defaults["avoid"] ?: buildJsonArray {},
            )
            put("author_overrides", buildJsonObject {})
        }
        val stages = buildJsonObject {
            contract.stageOrder.forEach { stage ->
                put(stage, buildJsonObject {
                    put("status", if (stage == "constraints") "generated" else "pending")
                    put("data", if (stage == "constraints") form else JsonNull)
                    put("updated_at", if (stage == "constraints") JsonPrimitive(now) else JsonNull)
                })
            }
        }
        val draft = buildJsonObject {
            put("schema_version", contract.schemaVersion)
            put("creation_mode", input.creationMode.takeIf { it in setOf("author_led", "explore") } ?: "explore")
            put("author_brief", if (input.creationMode == "author_led") input.brief.trim() else "")
            put("author_outline", input.authorOutline.trim())
            put("locked_requirements", strings(input.lockedRequirements))
            put("form", form)
            put("concepts", buildJsonArray {})
            put("concept_seeds", buildJsonObject {})
            put("selected_concept_id", JsonNull)
            put("stages", stages)
            put("quick_mode", false)
            put("created_at", now)
            put("updated_at", now)
        }
        return buildJsonObject {
            put("id", sessionId)
            put("source_project_id", JsonNull)
            put("created_project_id", JsonNull)
            put("status", "drafting")
            put("mode", "hybrid")
            put("schema_version", contract.schemaVersion)
            put("current_stage", "constraints")
            put("revision", 0)
            put("user_brief", input.brief.trim())
            put("display_title", input.brief.trim().lineSequence().first().take(40))
            put("target_audience", input.targetAudience.trim())
            put("genre", input.genre.trim())
            put("platform", input.platform.trim())
            put("draft", draft)
            put("created_at", now)
            put("updated_at", now)
        }
    }

    suspend fun generateStage(
    source: JsonObject,
    stage: String,
    instruction: String,
    config: DirectApiConfig,
): JsonObject {
    require(stage in contract.stageOrder && stage != "constraints") { "未知立项阶段" }
    val (system, user) = if (stage == "concepts") {
        contract.conceptMessages(source, instruction)
    } else {
        contract.stageMessages(source, stage, baseline(source, stage), instruction)
    }
    val maxTokens = if (stage == "concepts") 3_200 else 6_000
    val temperature = if (stage == "concepts") 0.8 else 0.65
    val creationExtraBody = if (config.isDeepSeek()) buildJsonObject {
        put("thinking", buildJsonObject { put("type", "disabled") })
    } else null
    val raw = directApi.complete(
        config,
        system,
        user,
        maxOutputTokens = maxTokens,
        temperature = temperature,
        extraBody = creationExtraBody,
    )
    val stageBaseline = baseline(source, stage)
    var sourceLabel = "model"
    var warning = ""
    var repairMethod = ""
    var rawResponsePreview = ""
    val data = try {
        parseStageData(stage, raw, stageBaseline, source.objectValue("draft"))
    } catch (initialError: Exception) {
        val (repairSystem, repairUser) = contract.repairMessages(
            raw,
            initialError.message.orEmpty(),
            stage,
        )
        val repaired = try {
            directApi.complete(
                config,
                repairSystem,
                repairUser,
                maxOutputTokens = maxTokens,
                temperature = 0.0,
                extraBody = creationExtraBody,
            )
        } catch (error: CancellationException) {
            throw error
        } catch (_: Exception) {
            null
        }
        val repairedData = repaired?.let { repairedRaw ->
            runCatching {
                parseStageData(stage, repairedRaw, stageBaseline, source.objectValue("draft"))
            }.getOrNull()
        }
        if (repairedData != null) {
            sourceLabel = "model_repaired"
            warning = "模型返回的结构已自动修复；请像 PC 端一样先审阅再确认。"
            repairMethod = "model_json"
            repairedData
        } else {
            sourceLabel = "contract_fallback"
            warning = "模型回复格式不可用，已保留可编辑安全草稿；请重点检查后再确认。"
            repairMethod = "safe_partial_draft"
            rawResponsePreview = raw.take(4_000)
            safeFallbackData(source, stage, raw)
        }
    }
    return writeStage(
        source,
        stage,
        data,
        status = "generated",
        sourceLabel = sourceLabel,
        warning = warning,
        repairMethod = repairMethod,
        rawResponsePreview = rawResponsePreview,
    )
}

    private fun parseStageData(
        stage: String,
        raw: String,
        stageBaseline: JsonObject,
        draft: JsonObject,
    ): JsonObject {
        val parsed = parseObject(raw)
        val rawData = (parsed["data"] as? JsonObject) ?: parsed
        if (stage != "concepts") validateAuthorRequirements(stage, rawData, stageBaseline, draft)
        val data = if (stage == "concepts") {
            normalizeConcepts(rawData)
        } else {
            normalizeStage(stage, rawData, stageBaseline)
        }
        validateStage(stage, data)
        validateAuthorRequirements(stage, data, stageBaseline, draft)
        return data
    }

    internal fun safeFallbackData(source: JsonObject, stage: String, raw: String): JsonObject {
    require(stage in contract.stageOrder && stage != "constraints") { "未知立项阶段" }
    val draft = source.objectValue("draft")
    val stageBaseline = baseline(source, stage)
    if (stage == "concepts") {
        safePartialConceptData(raw, draft)?.let { return it }
        val fallback = normalizeConcepts(buildJsonObject {
            put("concepts", JsonArray(listOf(safeConceptCard(draft))))
        })
        validateStage(stage, fallback)
        validateAuthorRequirements(stage, fallback, JsonObject(emptyMap()), draft)
        return fallback
    }
    runCatching { parseStageData(stage, raw, stageBaseline, draft) }
        .getOrNull()
        ?.let { return it }
    validateStage(stage, stageBaseline)
    validateAuthorRequirements(stage, stageBaseline, stageBaseline, draft)
    return stageBaseline
}

private fun safePartialConceptData(raw: String, draft: JsonObject): JsonObject? {
    val parsed = MobileCreationJsonRepair.parseObjectDetailed(raw)?.value ?: return null
    val payload = (parsed["data"] as? JsonObject) ?: parsed
    val rows = (payload["concepts"] as? JsonArray)
        ?: (payload["options"] as? JsonArray)
        ?: return null
    val sourceCard = rows.firstOrNull() as? JsonObject ?: return null
    val baseCard = safeConceptCard(draft)
    val merged = baseCard.toMutableMap().apply { putAll(sourceCard) }
    val protagonist = baseCard.objectValue("protagonist_seed").toMutableMap().apply {
        (sourceCard["protagonist_seed"] as? JsonObject)?.let { putAll(it) }
    }
    merged["protagonist_seed"] = JsonObject(protagonist)
    return try {
        val data = normalizeConcepts(buildJsonObject {
            put("concepts", JsonArray(listOf(JsonObject(merged))))
        })
        validateStage("concepts", data)
        validateAuthorRequirements("concepts", data, JsonObject(emptyMap()), draft)
        data
    } catch (_: Exception) {
        null
    }
}

private fun safeConceptCard(draft: JsonObject): JsonObject {
    val form = draft.objectValue("form")
    val brief = draft.string("author_brief")
        .ifBlank { form.string("brief") }
        .ifBlank { "待补充故事方案" }
    val requirements = draft.arrayValue("locked_requirements")
        .map(::stringElement)
        .filter(String::isNotBlank)
    val lockedSource = buildList {
        add(brief)
        addAll(requirements)
    }.joinToString("；")
    val protagonistName = Regex("(?:^|[，。；])\\s*([^，。；]{2,20}?)(?:必须是|必须为|是)")
        .find(lockedSource)
        ?.groupValues
        ?.getOrNull(1)
        ?.trim()
        .orEmpty()
        .ifBlank { "待确认主角" }
    val titleSeed = brief.substringBefore('。').substringBefore('，').take(20).ifBlank { "作者方案" }
    return buildJsonObject {
        put("title", titleSeed)
        put("subtitle", "依据作者原始设定整理，可继续编辑")
        put("logline", brief.take(120))
        put("protagonist_seed", buildJsonObject {
            put("name", protagonistName)
            put("identity", lockedSource.take(500))
            put("goal", "落实作者方案中的首要目标")
            put("lack", "待作者确认")
        })
        put("world_hook", lockedSource.take(500))
        put("core_conflict", lockedSource.take(500))
        put("story_engine", "遵循作者大纲持续推进")
        put("opening_hook", "从作者指定的起点切入")
        put("differentiators", strings(requirements.ifEmpty { listOf("保留作者原始设定") }))
        put("risks", strings(listOf("这是模型格式异常后的安全草稿，请在继续前检查")))
    }
}

    internal fun replaceArtifact(
        source: JsonObject,
        stage: String,
        data: JsonObject,
        sourceLabel: String = "assistant",
    ): JsonObject {
        require(stage in contract.stageOrder) { "未知立项阶段" }
        validateStage(stage, data)
        validateAuthorRequirements(stage, data, baseline(source, stage), source.objectValue("draft"))
        return writeStage(source, stage, data, status = "generated", sourceLabel = sourceLabel)
    }

    fun confirmStage(source: JsonObject, stage: String, editedData: JsonObject? = null): JsonObject {
        require(stage in contract.stageOrder) { "未知立项阶段" }
        val current = source.objectValue("draft").objectValue("stages").objectValue(stage)
        var data = editedData ?: (current["data"] as? JsonObject) ?: error("请先生成当前阶段")
        if (stage == "concepts") {
            val options = data["options"] as? JsonArray ?: JsonArray(emptyList())
            val selectedId = data.string("selected_concept_id").ifBlank {
                (options.firstOrNull() as? JsonObject)?.string("id").orEmpty()
            }
            require(selectedId.isNotBlank()) { "请选择一个创意方向" }
            data = JsonObject(data.toMutableMap().apply {
                put("selected_concept_id", JsonPrimitive(selectedId))
            })
        }
        validateStage(stage, data)
        return writeStage(source, stage, data, status = "confirmed", sourceLabel = "author")
    }

    fun markCompleted(source: JsonObject, projectId: String): JsonObject = JsonObject(
        source.toMutableMap().apply {
            put("status", JsonPrimitive("completed"))
            put("created_project_id", JsonPrimitive(projectId))
            put("completed_at", JsonPrimitive(Instant.now().toString()))
            put("updated_at", JsonPrimitive(Instant.now().toString()))
        },
    )

    private fun writeStage(
        source: JsonObject,
        stage: String,
        data: JsonObject,
        status: String,
        sourceLabel: String,
        warning: String = "",
        repairMethod: String = "",
        rawResponsePreview: String = "",
    ): JsonObject = updateDraft(source) { draft ->
        val stages = (draft["stages"] as? JsonObject ?: JsonObject(emptyMap())).toMutableMap()
        val previous = stages[stage] as? JsonObject
        val changed = previous?.get("data") != data
        if (changed) {
            contract.impactDependencies[stage].orEmpty().forEach { downstream ->
                val downstreamState = stages[downstream] as? JsonObject ?: return@forEach
                if (downstreamState.string("status") in setOf("generated", "confirmed")) {
                    stages[downstream] = JsonObject(downstreamState.toMutableMap().apply {
                        put("status", JsonPrimitive("stale"))
                        put("stale_reason", JsonPrimitive("上游阶段“${contract.stageLabels[stage]}”已修改"))
                        put("stale_source", JsonPrimitive(stage))
                    })
                }
            }
        }
        stages[stage] = buildJsonObject {
            put("status", status)
            put("data", data)
            put("source", sourceLabel)
            if (warning.isNotBlank()) put("warning", warning)
            if (repairMethod.isNotBlank()) put("repair_method", repairMethod)
            if (rawResponsePreview.isNotBlank()) put("raw_response_preview", rawResponsePreview.take(4_000))
            put("updated_at", Instant.now().toString())
        }
        draft["stages"] = JsonObject(stages)
        if (stage == "constraints") draft["form"] = data
        if (stage == "concepts") {
            val options = data["options"] as? JsonArray ?: JsonArray(emptyList())
            draft["concepts"] = options
            draft["concept_seeds"] = buildJsonObject {
                options.mapNotNull { it as? JsonObject }.forEach { card ->
                    card.string("id").takeIf(String::isNotBlank)?.let { put(it, card) }
                }
            }
            data.string("selected_concept_id").takeIf(String::isNotBlank)?.let {
                draft["selected_concept_id"] = JsonPrimitive(it)
            }
            if (status == "generated") draft["selected_concept_id"] = JsonNull
        }
        draft["updated_at"] = JsonPrimitive(Instant.now().toString())
    }.let { updated ->
        val index = contract.stageOrder.indexOf(stage)
        val current = if (status == "confirmed") contract.stageOrder.getOrNull(index + 1) ?: stage else stage
        JsonObject(updated.toMutableMap().apply {
            put("current_stage", JsonPrimitive(current))
            put("status", JsonPrimitive("reviewing"))
        })
    }

    private fun updateDraft(source: JsonObject, block: (MutableMap<String, JsonElement>) -> Unit): JsonObject {
        val draft = source.objectValue("draft").toMutableMap()
        block(draft)
        val now = Instant.now().toString()
        return JsonObject(source.toMutableMap().apply {
            put("draft", JsonObject(draft))
            put("revision", JsonPrimitive((source.int("revision") + 1)))
            put("updated_at", JsonPrimitive(now))
        })
    }

    private fun normalizeConcepts(raw: JsonObject): JsonObject {
        val rows = raw["concepts"] as? JsonArray
            ?: raw["options"] as? JsonArray
            ?: error("模型没有返回创意卡数组")
        require(rows.size == 1) { "PC V3 创意方向必须恰好包含一张卡" }
        val card = rows.first().jsonObject
        val protagonist = card.objectValue("protagonist_seed")
        val normalized = buildJsonObject {
            put("id", card.string("id").ifBlank { "concept-1" })
            put("source_index", 0)
            put("title", card.string("title").ifBlank { "创意方向 1" })
            put("subtitle", card.string("subtitle"))
            put("logline", card.string("logline"))
            put("protagonist_seed", buildJsonObject {
                put("name", protagonist.string("name").ifBlank { "待命名主角" })
                put("identity", protagonist.string("identity"))
                put("goal", protagonist.string("goal"))
                put("lack", protagonist.string("lack"))
            })
            put("world_hook", card.string("world_hook"))
            put("core_conflict", card.string("core_conflict"))
            put("story_engine", card.string("story_engine").ifBlank { card.string("core_conflict") })
            put("opening_hook", card.string("opening_hook"))
            put("differentiators", card.arrayValue("differentiators").takeArray(3))
            put("risks", card.arrayValue("risks").takeArray(2))
            put("coverage", normalizedCoverage(card))
        }
        return buildJsonObject {
            put("options", JsonArray(listOf(normalized)))
            put("selected_concept_id", JsonNull)
        }
    }

    private fun normalizedCoverage(card: JsonObject): JsonObject {
        val existing = card["coverage"] as? JsonObject
        if (existing != null) {
            return buildJsonObject {
                put("score", existing.int("score"))
                put("covered", existing.arrayValue("covered"))
                put("missing", existing.arrayValue("missing"))
            }
        }
        val protagonist = card.objectValue("protagonist_seed")
        val checks = listOf(
            "一句话梗概" to card.string("logline").isNotBlank(),
            "主角种子" to protagonist.isNotEmpty(),
            "世界钩子" to card.string("world_hook").isNotBlank(),
            "核心冲突" to card.string("core_conflict").isNotBlank(),
            "开篇钩子" to card.string("opening_hook").isNotBlank(),
        )
        val covered = checks.filter { it.second }.map { it.first }
        val missing = checks.filterNot { it.second }.map { it.first }
        return buildJsonObject {
            put("score", ((covered.size.toDouble() / checks.size) * 100).toInt())
            put("covered", strings(covered))
            put("missing", strings(missing))
        }
    }

    internal fun normalizeStage(stage: String, raw: JsonObject, baseline: JsonObject): JsonObject {
        val source = if (looksLikeCliMetadata(raw)) JsonObject(emptyMap()) else raw
        val merged = baseline.toMutableMap().apply { putAll(source) }
        return when (stage) {
            "world_style" -> JsonObject(merged.apply {
                listOf("writing_style", "world_tone", "story_structure", "pacing").forEach { field ->
                    put(field, JsonPrimitive(authorText(get(field))))
                }
                put("worldbuilding", normalizeWorldbuilding(get("worldbuilding")))
            })
            "characters" -> normalizeCharacters(source, baseline)
            "locations" -> normalizeLocations(source, baseline)
            "macro_outline" -> normalizeMacroOutline(source, baseline)
            "opening_outline" -> normalizeOpening(source, baseline)
            else -> JsonObject(merged)
        }
    }

    private fun normalizeWorldbuilding(value: JsonElement?): JsonArray {
        if (value is JsonArray) return JsonArray(value.mapNotNull { it as? JsonObject })
        if (value !is JsonObject) return JsonArray(emptyList())
        return JsonArray(value.map { (key, child) ->
            if (child is JsonObject) {
                JsonObject(child.toMutableMap().apply {
                    putIfAbsent("title", JsonPrimitive(key.trim()))
                    putIfAbsent("dimension", JsonPrimitive(key.trim()))
                    if (stringElement(get("content")).isBlank()) {
                        put(
                            "content",
                            JsonPrimitive(
                                authorText(get("summary") ?: get("description") ?: child),
                            ),
                        )
                    }
                })
            } else {
                buildJsonObject {
                    put("title", key.trim())
                    put("dimension", key.trim())
                    put("content", authorText(child))
                }
            }
        })
    }

    private fun normalizeCharacters(data: JsonObject, baseline: JsonObject): JsonObject {
        var sourceRows = dictRows(data["characters"])
        val baseRows = dictRows(baseline["characters"])
        if (sourceRows.isEmpty()) sourceRows = baseRows
        val baseByName = baseRows
            .mapNotNull { row -> row.string("name").takeIf(String::isNotBlank)?.let { it to row } }
            .toMap()
        val characters = sourceRows.mapIndexed { index, source ->
            val name = source.string("name")
            val base = baseByName[name] ?: baseRows.getOrNull(index) ?: JsonObject(emptyMap())
            val item = base.toMutableMap().apply { putAll(source) }
            item["name"] = JsonPrimitive(name.ifBlank { base.string("name").ifBlank { "角色${index + 1}" } })
            val baseProfile = base.objectValue("profile")
            val sourceProfile = (item["profile"] as? JsonObject) ?: JsonObject(emptyMap())
            val profile = baseProfile.toMutableMap().apply { putAll(sourceProfile) }
            val rawRoleType = firstText(source["role_type"], source["role"], base["role_type"])
            val roleType = normalizeRoleType(rawRoleType, if (index == 0) "protagonist" else "supporting")
            val goal = firstText(
                source["goal"],
                source["current_goal"],
                source.objectValue("profile")["core_motivation"],
                base["goal"],
                profile["core_motivation"],
            )
            item["role_type"] = JsonPrimitive(roleType)
            item["goal"] = JsonPrimitive(goal)
            item["current_goal"] = JsonPrimitive(goal)
            val background = firstText(item["background"], item["position"], item["status"])
            item["background"] = appendRoleDescription(background, rawRoleType)?.let(::JsonPrimitive) ?: JsonNull
            if (stringElement(profile["core_motivation"]).isBlank()) {
                profile["core_motivation"] = JsonPrimitive(goal)
            }
            item["profile"] = JsonObject(profile)
            JsonObject(item)
        }
        val relationships = dictRows(data["relationships"], "id")
            .ifEmpty { dictRows(baseline["relationships"], "id") }
        return JsonObject(baseline.toMutableMap().apply {
            putAll(data)
            put("characters", JsonArray(dedupe(characters) { it.string("name").lowercase() }))
            put("relationships", JsonArray(dedupe(relationships) { canonicalKey(it) }))
        })
    }

    private fun normalizeLocations(data: JsonObject, baseline: JsonObject): JsonObject {
        val entries = dictRows(data["entries"], "title") + dictRows(baseline["entries"], "title")
        val relations = dictRows(data["relations"], "id") + dictRows(baseline["relations"], "id")
        return JsonObject(baseline.toMutableMap().apply {
            putAll(data)
            put("entries", JsonArray(dedupe(entries) { it.string("title").lowercase() }))
            put("relations", JsonArray(dedupe(relations) {
                listOf(
                    it.string("source_title").lowercase(),
                    it.string("target_title").lowercase(),
                    it.string("relation_type").lowercase(),
                ).joinToString("\u0000")
            }))
        })
    }

    private fun normalizeMacroOutline(data: JsonObject, baseline: JsonObject): JsonObject {
        val normalized = baseline.toMutableMap().apply { putAll(data) }
        var sourceVolumes = dictRows(data["volumes"], "title")
        val baseVolumes = dictRows(baseline["volumes"], "title")
        if (sourceVolumes.isEmpty()) sourceVolumes = baseVolumes
        val requestedCount = baseline.int("requested_volume_count")
        if (requestedCount > 0) {
            sourceVolumes = sourceVolumes.take(requestedCount).toMutableList().apply {
                if (size < requestedCount) addAll(baseVolumes.drop(size).take(requestedCount - size))
            }
        }
        val volumes = sourceVolumes.mapIndexed { index, source ->
            val base = baseVolumes.getOrNull(index) ?: JsonObject(emptyMap())
            val item = base.toMutableMap().apply { putAll(source) }
            val range = chapterRange(item["chapters"] ?: item["range"])
            val start = firstPositiveInt(item["start_chapter"], range.first, base["start_chapter"])
            val end = firstPositiveInt(item["end_chapter"], range.second, base["end_chapter"])
            item["start_chapter"] = JsonPrimitive(start ?: 0)
            item["end_chapter"] = JsonPrimitive(end ?: 0)
            item["summary"] = JsonPrimitive(firstText(
                item["summary"], item["core_function"], item["focus"], item["climax"], base["summary"],
            ))
            item["title"] = JsonPrimitive(stringElement(item["title"]).ifBlank { "第${index + 1}卷" })
            JsonObject(item)
        }
        normalized["volumes"] = JsonArray(volumes)
        val stagePlan = dictRows(normalized["stage_plan"], "name").ifEmpty {
            volumes.map { item ->
                buildJsonObject {
                    put("name", item.string("title"))
                    put("range", buildJsonArray {
                        add(JsonPrimitive(item.int("start_chapter")))
                        add(JsonPrimitive(item.int("end_chapter")))
                    })
                    put("promise", item.string("summary"))
                }
            }
        }
        normalized["stage_plan"] = JsonArray(stagePlan)
        return JsonObject(normalized)
    }

    private fun normalizeOpening(data: JsonObject, baseline: JsonObject): JsonObject {
        var sourceChapters = dictRows(data["chapters"], "title")
        val baseChapters = dictRows(baseline["chapters"], "title")
        if (baseChapters.isNotEmpty()) {
            sourceChapters = (sourceChapters + List(baseChapters.size) { JsonObject(emptyMap()) })
                .take(baseChapters.size)
        }
        val chapters = mutableListOf<JsonObject>()
        val sections = mutableListOf<JsonObject>()
        val topSections = dictRows(data["sections"], "title")
        val baseSections = dictRows(baseline["sections"], "title")
        sourceChapters.forEachIndexed { index, source ->
            val base = baseChapters.getOrNull(index) ?: JsonObject(emptyMap())
            val originalId = source.string("client_id")
            val chapterNumber = chapterNumber(
                source["chapter_number"] ?: source["chapter"] ?: source["number"],
                index + 1,
            )
            val chapterId = originalId.ifBlank {
                base.string("client_id").ifBlank { "chapter-${chapterNumber.toString().padStart(2, '0')}" }
            }
            val chapter = base.toMutableMap().apply { putAll(source) }
            val nestedSections = dictRows(chapter.remove("sections"), "title")
            chapter["client_id"] = JsonPrimitive(chapterId)
            chapter["chapter_number"] = JsonPrimitive(chapterNumber)
            chapter["node_type"] = JsonPrimitive("chapter")
            chapter["sort_order"] = JsonPrimitive(chapterNumber(chapter["sort_order"], chapterNumber))
            chapter["title"] = JsonPrimitive(stringElement(chapter["title"]).ifBlank { "第${chapterNumber}章 未命名事件" })
            chapter["summary"] = JsonPrimitive(firstText(chapter["summary"], chapter["planned_summary"], chapter["beat"]))
            chapter["planned_summary"] = JsonPrimitive(firstText(chapter["planned_summary"], chapter["summary"]))
            chapters += JsonObject(chapter)

            val aliases = mutableSetOf(chapterId, chapterNumber.toString(), "chapter-${chapterNumber.toString().padStart(2, '0')}")
            if (originalId.isNotBlank()) aliases += originalId
            var matching = nestedSections.ifEmpty {
                topSections.filter { it.string("parent_client_id") in aliases }
            }
            val baseChapterId = base.string("client_id").ifBlank { chapterId }
            val fallbackSections = baseSections.filter { it.string("parent_client_id") == baseChapterId }
            if (matching.size !in 2..6 && fallbackSections.isNotEmpty()) matching = fallbackSections
            matching.take(6).forEachIndexed { sceneIndex, rawSection ->
                sections += normalizeSection(
                    rawSection,
                    fallbackSections.getOrNull(sceneIndex) ?: JsonObject(emptyMap()),
                    chapterId,
                    chapterNumber,
                    sceneIndex + 1,
                )
            }
        }
        return JsonObject(baseline.toMutableMap().apply {
            putAll(data)
            put("opening_chapter_count", JsonPrimitive(chapters.size))
            put("chapters", JsonArray(chapters))
            put("sections", JsonArray(sections))
            put("section_rule", JsonPrimitive("每章2至6个场景事件"))
        })
    }

    private fun normalizeSection(
        section: JsonObject,
        base: JsonObject,
        chapterId: String,
        chapterNumber: Int,
        sceneNumber: Int,
    ): JsonObject {
        val item = base.toMutableMap().apply { putAll(section) }
        item["client_id"] = JsonPrimitive(stringElement(item["client_id"]).ifBlank { "$chapterId-section-$sceneNumber" })
        item["parent_client_id"] = JsonPrimitive(chapterId)
        item["node_type"] = JsonPrimitive("section")
        item["sort_order"] = JsonPrimitive(chapterNumber(item["sort_order"], sceneNumber))
        item["title"] = JsonPrimitive(stringElement(item["title"]).ifBlank { "第${chapterNumber}章 · 场景$sceneNumber" })
        item["summary"] = JsonPrimitive(firstText(item["summary"], item["planned_summary"], item["purpose"]))
        item["planned_summary"] = JsonPrimitive(firstText(item["planned_summary"], item["summary"]))
        val metadata = base.objectValue("metadata").toMutableMap().apply {
            putAll((item["metadata"] as? JsonObject).orEmpty())
        }
        metadata["scene_number"] = JsonPrimitive(chapterNumber(metadata["scene_number"], sceneNumber))
        metadata["purpose"] = JsonPrimitive(firstText(metadata["purpose"], item["purpose"], item["summary"]).ifBlank { "推进本章目标" })
        metadata["location"] = JsonPrimitive(stringElement(metadata["location"]).ifBlank { "地点待定" })
        metadata["timeline"] = JsonPrimitive(stringElement(metadata["timeline"]).ifBlank { "第${chapterNumber}章第${sceneNumber}场" })
        metadata["pov_character"] = JsonPrimitive(stringElement(metadata["pov_character"]).ifBlank { "主角" })
        if (metadata["characters"] !is JsonArray) {
            metadata["characters"] = buildJsonArray { add(metadata.getValue("pov_character")) }
        }
        metadata["entry_state"] = JsonPrimitive(stringElement(metadata["entry_state"]).ifBlank { "承接上一场景" })
        metadata["exit_state"] = JsonPrimitive(stringElement(metadata["exit_state"]).ifBlank { "产生新的行动压力" })
        metadata["emotional_residue"] = JsonPrimitive(stringElement(metadata["emotional_residue"]).ifBlank { "情绪推动下一场景" })
        if (metadata["unresolved_actions"] !is JsonArray) {
            metadata["unresolved_actions"] = strings(listOf("追踪本场景产生的新问题"))
        }
        item["metadata"] = JsonObject(metadata)
        return JsonObject(item)
    }

    internal fun baseline(session: JsonObject, stage: String): JsonObject {
        val draft = session.objectValue("draft")
        val form = draft.objectValue("form")
        val blueprint = compactBlueprint(draft, form)
        return when (stage) {
            "world_style" -> buildJsonObject {
                put("writing_style", form.string("writing_style").ifBlank { blueprint.string("writing_style") })
                put("world_tone", form.string("world_tone"))
                put("story_structure", form.string("story_structure"))
                put("pacing", form.string("pacing"))
                put("style_rules", blueprint.arrayValue("style_rules"))
                put("forbidden_patterns", JsonArray(form.arrayValue("avoid") + blueprint.arrayValue("forbidden_patterns")))
                put("worldbuilding", blueprint.arrayValue("worldbuilding"))
                put("display_groups", strings(listOf("世界规则", "力量与资源", "社会与文化", "历史与冲突", "生活与感官")))
            }
            "characters" -> buildJsonObject {
                val protagonist = JsonObject(blueprint.objectValue("protagonist").toMutableMap().apply {
                    put("role_type", JsonPrimitive("protagonist"))
                })
                val rows = listOf(protagonist) + blueprint.arrayValue("characters").mapNotNull { it as? JsonObject }
                val seen = mutableSetOf<String>()
                put("characters", JsonArray(rows.mapIndexedNotNull { index, row ->
                    val name = row.string("name")
                    if (name.isBlank() || !seen.add(name)) null else JsonObject(row.toMutableMap().apply {
                        put("profile", characterProfile(row, index))
                    })
                }))
                put("relationships", blueprint.arrayValue("relationships"))
            }
            "locations" -> locationsBaseline(blueprint)
            "macro_outline" -> macroOutlineBaseline(draft, blueprint, form)
            "opening_outline" -> openingBaseline(blueprint, form)
            "final_review" -> finalReviewBaseline(draft)
            else -> buildJsonObject {}
        }
    }

    private fun compactBlueprint(draft: JsonObject, form: JsonObject): JsonObject {
        val selectedId = draft.string("selected_concept_id")
        val seed = draft.objectValue("concept_seeds").objectValue(selectedId).takeIf { it.isNotEmpty() }
            ?: (draft.arrayValue("concepts").firstOrNull() as? JsonObject)
            ?: draft.objectValue("stages").objectValue("concepts").objectValue("data")
                .arrayValue("options").firstOrNull() as? JsonObject
            ?: JsonObject(emptyMap())
        val protagonist = seed.objectValue("protagonist_seed")
        val logline = seed.string("logline")
        val worldHook = seed.string("world_hook")
        val coreConflict = seed.string("core_conflict")
        val openingHook = seed.string("opening_hook")
        return buildJsonObject {
            put("title", seed.string("title").ifBlank { "未命名小说" })
            put("subtitle", seed.string("subtitle"))
            put("genre", form.string("genre"))
            put("genre_positioning", seed.string("subtitle"))
            put("logline", logline)
            put("premise", logline)
            put("core_conflict", coreConflict)
            put("protagonist", buildJsonObject {
                put("name", protagonist.string("name").ifBlank { "待命名主角" })
                put("goal", protagonist.string("goal"))
                put("weakness", protagonist.string("lack"))
                put("conflict", coreConflict)
                put("background", protagonist.string("identity"))
                put("current_location", "故事起点")
            })
            put("characters", buildJsonArray {})
            put("relationships", buildJsonArray {})
            put("worldbuilding", buildJsonArray {
                if (worldHook.isNotBlank()) add(buildJsonObject {
                    put("title", "核心世界钩子")
                    put("dimension", "power_system")
                    put("content", worldHook)
                })
            })
            put("volume_outline", buildJsonArray {})
            put("outline", buildJsonArray {
                if (openingHook.isNotBlank() || coreConflict.isNotBlank()) add(buildJsonObject {
                    put("title", openingHook.ifBlank { "开篇钩子" })
                    put("summary", openingHook.ifBlank { coreConflict })
                    put("node_type", "chapter")
                    put("purpose", "建立主角的即时压力与持续追读钩子")
                })
            })
            put("golden_three", buildJsonObject {
                put("opening_scene", openingHook)
                put("chapter_1", openingHook)
            })
            put("style_rules", buildJsonArray {})
            put("forbidden_patterns", form.arrayValue("avoid"))
            put("risks", seed.arrayValue("risks"))
        }
    }

    private fun characterProfile(character: JsonObject, index: Int): JsonObject {
        val goal = firstText(character["goal"], character["current_goal"])
        val conflict = firstText(character["conflict"], character["active_conflict"])
        val personality = character.string("personality")
        return buildJsonObject {
            put("core_motivation", goal)
            put("inner_lack", firstText(character["weakness"], JsonPrimitive(conflict)).ifBlank { "尚未意识到的内在缺口" })
            put("core_belief", character.string("belief").ifBlank { "相信行动能够改变自身处境" })
            put("public_persona", firstText(character["public_persona"], JsonPrimitive(personality)))
            put("hidden_persona", character.string("hidden_persona").ifBlank { "在高压下显露的另一面" })
            put("reveal_chapter", character.int("reveal_chapter").takeIf { it > 0 } ?: max(2, index + 2))
            put("moral_taboo", character.string("moral_taboo").ifBlank { "不主动牺牲无辜者" })
            put("voice", character.string("voice").ifBlank { "句式和措辞与身份、年龄一致" })
            put("action_habit", character.string("action_habit").ifBlank { "紧张时先观察出口和他人反应" })
            put("trauma_trigger", firstText(character["trauma_trigger"], JsonPrimitive(conflict)))
        }
    }

    private fun locationsBaseline(blueprint: JsonObject): JsonObject {
        val entries = blueprint.arrayValue("worldbuilding").mapNotNull { it as? JsonObject }
        val filtered = entries.filter { it.string("dimension") in setOf("geography", "factions", "location", "organization") }
            .ifEmpty { entries.take(6) }
        return buildJsonObject {
            put("entries", JsonArray(filtered))
            put("relations", buildJsonArray {
                repeat(max(0, filtered.size - 1)) { index ->
                    add(buildJsonObject {
                        put("source_title", filtered[index].string("title"))
                        put("target_title", filtered[index + 1].string("title"))
                        put("relation_type", if (index % 2 == 0) "connected_to" else "influences")
                        put("description", "双方在资源、通行或权力上互相影响")
                        put("metadata", buildJsonObject {
                            put("stable", true)
                            put("source", "novel_creation_v2")
                        })
                    })
                }
            })
        }
    }

    private fun macroOutlineBaseline(draft: JsonObject, blueprint: JsonObject, form: JsonObject): JsonObject {
        val targetChapters = form.int("target_chapters").takeIf { it > 0 } ?: 240
        val requestedCount = requestedVolumeCount(draft)
        val volumeCount = requestedCount ?: min(12, max(3, round(targetChapters / 100.0).toInt()))
        val volumes = blueprint.arrayValue("volume_outline").mapNotNull { it as? JsonObject }
            .take(volumeCount)
            .toMutableList()
        while (volumes.size < volumeCount) {
            val index = volumes.size
            volumes += buildJsonObject {
                put("title", "第${index + 1}卷 阶段转折")
                put("summary", "围绕全书核心冲突完成第${index + 1}阶段的目标、代价与格局变化。")
            }
        }
        val span = max(1, targetChapters / volumes.size)
        val ranged = volumes.mapIndexed { index, volume ->
            JsonObject(volume.toMutableMap().apply {
                put("start_chapter", JsonPrimitive(index * span + 1))
                put("end_chapter", JsonPrimitive(if (index == volumes.lastIndex) targetChapters else (index + 1) * span))
            })
        }
        return buildJsonObject {
            put("story_overview", firstText(blueprint["premise"], blueprint["logline"]))
            put("core_conflict", blueprint.string("core_conflict"))
            put("ending_direction", blueprint.string("ending_direction").ifBlank { "主角必须以最终选择回应开篇提出的核心问题" })
            put("target_chapters", targetChapters)
            put("requested_volume_count", requestedCount?.let(::JsonPrimitive) ?: JsonNull)
            put("volumes", JsonArray(ranged))
            put("stage_plan", buildJsonArray {
                ranged.forEach { item ->
                    add(buildJsonObject {
                        put("name", item.string("title"))
                        put("range", buildJsonArray {
                            add(JsonPrimitive(item.int("start_chapter")))
                            add(JsonPrimitive(item.int("end_chapter")))
                        })
                        put("promise", item.string("summary"))
                    })
                }
            })
        }
    }

    private fun openingBaseline(blueprint: JsonObject, form: JsonObject): JsonObject {
        val chapterSources = blueprint.arrayValue("outline")
            .mapNotNull { it as? JsonObject }
            .filter { it.string("node_type").ifBlank { "chapter" } == "chapter" }
        val protagonist = blueprint.objectValue("protagonist")
        val protagonistName = protagonist.string("name").ifBlank { "主角" }
        val coreConflict = firstText(blueprint["core_conflict"], protagonist["conflict"])
            .ifBlank { "核心矛盾持续升级" }
        val location = protagonist.string("current_location").ifBlank { "故事起点" }
        val chapters = mutableListOf<JsonObject>()
        val sections = mutableListOf<JsonObject>()
        val chapterCount = if (form.int("opening_chapters") == 15) 15 else 3
        repeat(chapterCount) { index ->
            val number = index + 1
            val source = chapterSources.getOrNull(index) ?: JsonObject(emptyMap())
            val summary = firstText(source["summary"], source["planned_summary"])
                .ifBlank { "主角围绕“$coreConflict”采取新的行动，并承担由此产生的后果。" }
            val chapterId = "chapter-${number.toString().padStart(2, '0')}"
            val titleSource = source.string("title").ifBlank { "局势推进 $number" }
            val cleanTitle = titleSource.replace(Regex("^第\\s*\\d+\\s*章[：:\\s-]*"), "").trim()
                .ifBlank { "未命名事件 $number" }
            val chapterTitle = "第${number}章 $cleanTitle"
            chapters += buildJsonObject {
                put("client_id", chapterId)
                put("node_type", "chapter")
                put("chapter_number", number)
                put("title", chapterTitle)
                put("summary", summary)
                put("planned_summary", summary)
                put("purpose", source.string("purpose").ifBlank { "推进主线并改变人物状态" })
                put("parent_index", source.int("parent_index"))
                put("sort_order", number)
            }
            val specs = listOf(
                Triple("压力进入", "建立本章局面与立即目标", "局面由稳定转为受压"),
                Triple("选择与对抗", "让人物用行动处理核心阻力", "行动制造新的信息或代价"),
                Triple("后果与钩子", "结算变化并留下下一章驱动力", "本章目标部分兑现但新问题出现"),
            )
            specs.forEachIndexed { sceneIndex, (suffix, purpose, exitState) ->
                val scene = sceneIndex + 1
                sections += buildJsonObject {
                    put("client_id", "$chapterId-section-$scene")
                    put("parent_client_id", chapterId)
                    put("node_type", "section")
                    put("title", "$chapterTitle · $suffix")
                    put("summary", "$purpose：$summary")
                    put("planned_summary", "$purpose：$summary")
                    put("sort_order", scene)
                    put("metadata", buildJsonObject {
                        put("scene_number", scene)
                        put("purpose", purpose)
                        put("location", source.string("location").ifBlank { location })
                        put("timeline", "第${number}章第${scene}场")
                        put("pov_character", source.string("pov_character").ifBlank { protagonistName })
                        put("characters", source.arrayValue("characters").takeIf { it.isNotEmpty() }
                            ?: strings(listOf(protagonistName)))
                        put("entry_state", "承接上一场景的目标与压力")
                        put("exit_state", exitState)
                        put("emotional_residue", "人物对下一步行动形成新的情绪倾向")
                        put("unresolved_actions", strings(listOf("追踪本场景产生的新问题")))
                    })
                }
            }
        }
        return buildJsonObject {
            put("opening_chapter_count", chapterCount)
            put("chapters", JsonArray(chapters))
            put("sections", JsonArray(sections))
            put("section_rule", "每章3个场景事件，允许作者调整为2至6个")
        }
    }

    private fun finalReviewBaseline(draft: JsonObject): JsonObject {
        val required = listOf("constraints", "concepts", "world_style", "characters", "locations", "macro_outline")
        val stages = draft.objectValue("stages")
        val openingState = stages.objectValue("opening_outline")
        val openingConfirmed = openingState.string("status") == "confirmed"
        val opening = if (openingConfirmed) openingState.objectValue("data") else JsonObject(emptyMap())
        val characters = stages.objectValue("characters").objectValue("data")
        val world = stages.objectValue("world_style").objectValue("data")
        val blocking = required
            .filter { stages.objectValue(it).string("status") != "confirmed" }
            .map { "${contract.stageLabels[it]}尚未确认或需要重新生成" }
            .toMutableList()
        if (openingConfirmed) {
            val expected = if (opening.int("opening_chapter_count") == 15) 15 else 3
            val chapters = opening.arrayValue("chapters").mapNotNull { it as? JsonObject }
            if (chapters.size != expected) blocking += "已确认的前${expected}章细纲不完整"
            val counts = opening.arrayValue("sections").mapNotNull { it as? JsonObject }
                .groupingBy { it.string("parent_client_id") }.eachCount()
            if (chapters.any { it.string("client_id").isBlank() || counts[it.string("client_id")] !in 2..6 }) {
                blocking += "已确认的开篇细纲中，每章必须包含2至6个场景事件"
            }
        }
        if (characters.arrayValue("characters").isEmpty()) blocking += "缺少角色档案"
        if (world.arrayValue("worldbuilding").isEmpty()) blocking += "缺少世界观条目"
        val warnings = mutableListOf("后续章节仅保留宏观卷纲，写作前再按批次展开细纲")
        if (!openingConfirmed) warnings += "开篇细纲尚未确认，本次只写入核心立项资料；可在正式作品中继续生成和完善章节。"
        return buildJsonObject {
            put("ready", blocking.isEmpty())
            put("blocking", strings(blocking))
            put("warnings", strings(warnings))
            put("counts", buildJsonObject {
                put("characters", characters.arrayValue("characters").size)
                put("worldbuilding", world.arrayValue("worldbuilding").size)
                put("chapters", opening.arrayValue("chapters").size)
                put("sections", opening.arrayValue("sections").size)
            })
        }
    }

    private fun looksLikeCliMetadata(data: JsonObject): Boolean {
        val metadataTypes = setOf(
            "step_start", "step_finish", "message_start", "message_finish", "tool_start", "tool_finish",
        )
        val eventType = data.string("type").lowercase().replace('-', '_')
        val partType = data.objectValue("part").string("type").lowercase().replace('-', '_')
        return eventType in metadataTypes || partType in metadataTypes
    }

    private fun questionKey(value: String): String =
        value.lowercase().replace(Regex("[^\\p{L}\\p{N}]+"), "")

    private fun authorText(value: JsonElement?): String = when (value) {
        null, JsonNull -> ""
        is JsonPrimitive -> value.booleanOrNull?.let { if (it) "是" else "否" }
            ?: value.content.trim()
        is JsonArray -> value.map(::authorText).filter(String::isNotBlank).joinToString("；")
        is JsonObject -> value.mapNotNull { (key, child) ->
            authorText(child).takeIf(String::isNotBlank)?.let { "${authorFieldLabel(key)}：$it" }
        }.joinToString("；")
        else -> value.toString().trim()
    }

    private fun authorFieldLabel(key: String): String = mapOf(
        "writing_style" to "正文风格",
        "world_tone" to "世界基调",
        "story_structure" to "剧情结构",
        "pacing" to "叙事节奏",
        "core_tone" to "核心基调",
        "atmosphere" to "氛围",
        "emotional_color" to "情绪色彩",
        "reader_experience" to "读者感受",
        "narrative_perspective" to "叙事视角",
        "perspective" to "叙事视角",
        "sentence_rhythm" to "句式节奏",
        "language_style" to "语言风格",
        "main_line" to "主线结构",
        "stages" to "阶段安排",
        "opening" to "开篇节奏",
        "middle" to "中段节奏",
        "climax" to "高潮节奏",
        "summary" to "摘要",
        "description" to "说明",
        "content" to "内容",
    )[key] ?: key.replace('_', ' ')

    private fun dictRows(value: JsonElement?, nameField: String = "name"): List<JsonObject> = when (value) {
        is JsonArray -> value.mapNotNull { it as? JsonObject }
        is JsonObject -> value.mapNotNull { (key, child) ->
            (child as? JsonObject)?.let { row ->
                JsonObject(row.toMutableMap().apply {
                    if (nameField !in this) put(nameField, JsonPrimitive(key.trim()))
                })
            }
        }
        else -> emptyList()
    }

    private fun dedupe(rows: List<JsonObject>, keyBuilder: (JsonObject) -> String): List<JsonObject> {
        val unique = mutableListOf<JsonObject>()
        val seen = mutableMapOf<String, Int>()
        rows.forEach { row ->
            val key = keyBuilder(row)
            if (key.isEmpty()) {
                unique += JsonObject(row.toMap())
            } else if (key in seen) {
                val index = seen.getValue(key)
                val existing = unique[index].toMutableMap()
                row.forEach { (field, value) ->
                    if (isEmptyJson(existing[field])) existing[field] = value
                }
                unique[index] = JsonObject(existing)
            } else {
                seen[key] = unique.size
                unique += JsonObject(row.toMap())
            }
        }
        return unique
    }

    private fun isEmptyJson(value: JsonElement?): Boolean = when (value) {
        null, JsonNull -> true
        is JsonPrimitive -> value.contentOrNull?.isEmpty() == true
        is JsonArray -> value.isEmpty()
        is JsonObject -> value.isEmpty()
        else -> false
    }

    private fun canonicalKey(value: JsonElement): String = when (value) {
        is JsonObject -> value.toSortedMap().entries.joinToString(prefix = "{", postfix = "}") {
            "${it.key}:${canonicalKey(it.value)}"
        }
        is JsonArray -> value.joinToString(prefix = "[", postfix = "]") { canonicalKey(it) }
        else -> value.toString()
    }

    private fun stringElement(value: JsonElement?): String =
        (value as? JsonPrimitive)?.contentOrNull.orEmpty().trim()

    private fun firstText(vararg values: JsonElement?): String =
        values.firstNotNullOfOrNull { stringElement(it).takeIf(String::isNotBlank) }.orEmpty()

    private fun normalizeRoleType(value: String, default: String): String {
        val normalized = value.trim().replace(Regex("\\s+"), " ").lowercase()
        if (normalized.isBlank()) return default
        if (normalized == "merged_alias") return normalized
        ROLE_ALIASES[normalized]?.let { return it }
        val tokens = normalized.split(Regex("[,，、/|;；\\n\\r]+"))
            .map { it.trim(' ', '(', ')', '（', '）', '[', ']', '【', '】', ':', '\'', '"') }
        val resolved = tokens.mapNotNull(ROLE_ALIASES::get)
        listOf("protagonist", "antagonist", "mentor", "supporting", "other").firstOrNull {
            it in resolved
        }?.let { return it }
        if (tokens.any { Regex("^(?:本书|故事)?(?:男|女)?主角(?:身份|定位)?$").matches(it) }) return "protagonist"
        tokens.forEach { token ->
            if (listOf("反派", "敌对", "宿敌", "villain", "antagonist").any(token::contains)) return "antagonist"
            if (listOf("导师", "师父", "师傅", "引路", "mentor").any(token::contains)) return "mentor"
            if (listOf("配角", "同伴", "伙伴", "亲属", "父亲", "母亲").any(token::contains)) return "supporting"
        }
        return default
    }

    private fun appendRoleDescription(background: String, roleValue: String): String? {
        val current = background.trim()
        val details = roleValue.trim().replace(Regex("\\s+"), " ")
            .split(Regex("[,，、/|;；\\n\\r]+"))
            .map { it.trim(' ', '(', ')', '（', '）', '[', ']', '【', '】', ':', '\'', '"') }
            .filter { token ->
                token.isNotBlank() &&
                    token.lowercase() !in ROLE_ALIASES &&
                    token.lowercase() !in setOf("protagonist", "supporting", "antagonist", "mentor", "other") &&
                    !Regex("^(?:本书|故事)?(?:男|女)?主角(?:身份|定位)?$").matches(token)
            }
            .distinct()
            .joinToString("、")
        if (details.isBlank()) return current.take(8_000).ifBlank { null }
        val sentence = "身份补充：$details"
        if (details in current || sentence in current) return current.take(8_000).ifBlank { null }
        return listOf(current, sentence).filter(String::isNotBlank).joinToString("\n\n").take(8_000)
    }

    private fun chapterRange(value: JsonElement?): Pair<Int?, Int?> {
        if (value is JsonArray && value.size >= 2) {
            return value[0].primitiveInt() to value[1].primitiveInt()
        }
        val numbers = stringElement(value).let { Regex("\\d+").findAll(it).map(MatchResult::value).toList() }
        return if (numbers.size >= 2) numbers[0].toIntOrNull() to numbers[1].toIntOrNull() else null to null
    }

    private fun firstPositiveInt(vararg values: Any?): Int? = values.firstNotNullOfOrNull { value ->
        val parsed = when (value) {
            is JsonElement -> value.primitiveInt()
            is Int -> value
            else -> null
        }
        parsed?.takeIf { it != 0 }
    }

    private fun JsonElement.primitiveInt(): Int? = (this as? JsonPrimitive)?.contentOrNull?.toIntOrNull()

    private fun chapterNumber(value: JsonElement?, fallback: Int): Int {
        value?.primitiveInt()?.let { return it }
        return Regex("\\d+").find(stringElement(value))?.value?.toIntOrNull() ?: fallback
    }

    private fun requestedVolumeCount(draft: JsonObject): Int? {
        val source = buildList {
            add(draft.string("author_outline"))
            addAll(draft.arrayValue("locked_requirements").map(::stringElement))
        }.joinToString("\n")
        val token = Regex("([0-9０-９零〇一二两三四五六七八九十百]+)\\s*卷")
            .findAll(source).lastOrNull()?.groupValues?.getOrNull(1) ?: return null
        val ascii = token.map { FULL_WIDTH_DIGITS[it] ?: it }.joinToString("")
        val value = ascii.toIntOrNull() ?: chineseNumberToInt(ascii)
        return value?.takeIf { it in 1..100 }
    }

    private fun chineseNumberToInt(value: String): Int? {
        if (value.isBlank()) return null
        val digits = mapOf('零' to 0, '〇' to 0, '一' to 1, '二' to 2, '两' to 2, '三' to 3, '四' to 4,
            '五' to 5, '六' to 6, '七' to 7, '八' to 8, '九' to 9)
        var total = 0
        var current = 0
        value.forEach { char ->
            when (char) {
                '十' -> { total += (if (current == 0) 1 else current) * 10; current = 0 }
                '百' -> { total += (if (current == 0) 1 else current) * 100; current = 0 }
                else -> current = digits[char] ?: return null
            }
        }
        return total + current
    }

    private fun validateAuthorRequirements(
        stage: String,
        data: JsonObject,
        baseline: JsonObject,
        draft: JsonObject,
    ) {
        val requirements = draft.arrayValue("locked_requirements")
            .map(::stringElement)
            .filter(String::isNotBlank)
        val requestedVolumes = requestedVolumeCount(draft)
        if (stage == "macro_outline" && requestedVolumes != null) {
            val volumeCount = data.arrayValue("volumes").size
            require(volumeCount == requestedVolumes) {
                "作者锁定要求为 $requestedVolumes 卷，模型返回 $volumeCount 卷"
            }
        }
        if (requirements.isEmpty()) return
        val outputRegions = stageSemanticRegions(stage, data)
        val baselineText = stageSemanticRegions(stage, baseline).joinToString("")
        val stageKeywords = mapOf(
            "characters" to listOf("主角", "角色", "姓名", "名字", "身份"),
            "world_style" to listOf("世界", "设定", "规则", "基调"),
            "locations" to listOf("地点", "城市", "势力", "组织"),
            "macro_outline" to listOf("主线", "核心", "冲突", "结局", "卷"),
            "opening_outline" to listOf("开篇", "前三章", "前3章", "前十五章", "前15章"),
        )
        val missing = mutableListOf<String>()
        requirements.forEach { requirement ->
            var relevant = stage == "concepts" || stageKeywords[stage].orEmpty().any(requirement::contains)
            val anchors = lockedAnchors(requirement)
            val tokens = anchors.map(::lockText).filter(String::isNotBlank)
            if (tokens.any(baselineText::contains)) relevant = true
            if (!relevant || tokens.isEmpty()) return@forEach
            if (outputRegions.none { region -> tokens.all(region::contains) }) {
                val absent = anchors.zip(tokens).filter { (_, token) -> outputRegions.none { token in it } }.map { it.first }
                missing += absent.ifEmpty { listOf(requirement) }
            }
        }
        require(missing.isEmpty()) {
            "模型结果删除或改写了作者锁定内容：${missing.distinct().joinToString("、")}"
        }
    }

    private fun lockedAnchors(requirement: String): List<String> {
        if (requirement.isBlank()) return emptyList()
        val anchors = mutableListOf<String>()
        Regex("[“\\\"「『]([^”\\\"」』]{2,40})[”\\\"」』]")
            .findAll(requirement).forEach { anchors += it.groupValues[1] }
        if ('：' in requirement || ':' in requirement) {
            val tail = requirement.split(Regex("[：:]"), limit = 2).last().trim()
            if (tail.length in 2..80) anchors += tail
        }
        Regex("^(.{2,20}?)(?:必须|不得|不可|不能)").find(requirement)?.groupValues?.getOrNull(1)
            ?.trim('，', '。', '；', '：', ':', ' ')
            ?.takeIf { it !in setOf("全书", "故事", "作品", "设定", "核心设定", "结局", "主角", "角色") }
            ?.let(anchors::add)
        Regex(
            "(?:必须(?:保留|叫|名为|是|为|包含|采用)?|不得(?:删除|改写|修改|更名)?|" +
                "不可(?:删除|改写|修改|更名)?|不能(?:删除|改写|修改|更名)?|保留)\\s*(.{2,80})$",
        ).find(requirement)?.groupValues?.getOrNull(1)?.let { raw ->
            val value = raw.replace(Regex("(?:不得|不可|不能)?(?:删除|改写|修改|更名|改变)$"), "")
                .trim('，', '。', '；', '：', ':', ' ')
            if (value.isNotBlank() && !Regex("[0-9０-９零〇一二两三四五六七八九十百]+卷").matches(value)) {
                anchors += value
            }
        }
        val unique = mutableListOf<String>()
        anchors.forEach { anchor ->
            val normalized = lockText(anchor)
            if (normalized.length >= 2 && unique.none { lockText(it) == normalized }) unique += anchor.trim()
        }
        return unique
    }

    private fun lockText(value: JsonElement?): String = lockText(authorText(value))
    private fun lockText(value: String): String = value
        .replace(Regex("[\\s，。；：、,.!?！？;:‘’“”\\\"'（）()《》〈〉【】\\[\\]]+"), "")
        .lowercase()

    private fun semanticRegion(value: JsonObject?, fields: List<String>): String {
        if (value == null) return ""
        return lockText(buildJsonObject {
            fields.forEach { field -> value[field]?.let { put(field, it) } }
        })
    }

    private fun stageSemanticRegions(stage: String, data: JsonObject): List<String> {
        val regions = mutableListOf<String>()
        when (stage) {
            "concepts" -> data.arrayValue("options").mapNotNull { it as? JsonObject }.forEach { option ->
                regions += semanticRegion(
                    option["protagonist_seed"] as? JsonObject,
                    listOf("name", "identity", "goal", "lack", "background", "motivation"),
                )
                regions += semanticRegion(
                    option,
                    listOf("title", "logline", "world_hook", "core_conflict", "story_engine", "opening_hook"),
                )
            }
            "characters" -> {
                data.arrayValue("characters").mapNotNull { it as? JsonObject }.forEach {
                    regions += semanticRegion(it, listOf(
                        "name", "identity", "role_type", "goal", "current_goal", "background",
                        "personality", "description", "profile",
                    ))
                }
                data.arrayValue("relationships").mapNotNull { it as? JsonObject }.forEach {
                    regions += semanticRegion(it, listOf(
                        "source", "target", "source_name", "target_name", "relationship_type",
                        "relation_type", "description",
                    ))
                }
            }
            "world_style" -> {
                regions += semanticRegion(data, listOf(
                    "writing_style", "world_tone", "story_structure", "pacing", "style_rules", "forbidden_patterns",
                ))
                data.arrayValue("worldbuilding").mapNotNull { it as? JsonObject }.forEach {
                    regions += semanticRegion(it, listOf("title", "dimension", "content", "description", "rules", "summary"))
                }
            }
            "locations" -> {
                data.arrayValue("entries").mapNotNull { it as? JsonObject }.forEach {
                    regions += semanticRegion(it, listOf("title", "dimension", "content", "description", "rules", "summary"))
                }
                data.arrayValue("relations").mapNotNull { it as? JsonObject }.forEach {
                    regions += semanticRegion(it, listOf("source_title", "target_title", "relation_type", "description"))
                }
            }
            "macro_outline" -> {
                regions += semanticRegion(data, listOf("story_overview", "core_conflict", "ending_direction", "stage_plan"))
                data.arrayValue("volumes").mapNotNull { it as? JsonObject }.forEach {
                    regions += semanticRegion(it, listOf("title", "summary", "core_conflict", "ending", "goal"))
                }
            }
            "opening_outline" -> {
                data.arrayValue("chapters").mapNotNull { it as? JsonObject }.forEach {
                    regions += semanticRegion(it, listOf("title", "summary", "goal", "hook", "chapter_number", "description"))
                }
                data.arrayValue("sections").mapNotNull { it as? JsonObject }.forEach {
                    regions += semanticRegion(it, listOf("title", "summary", "description", "metadata"))
                }
            }
            "final_review" -> regions += semanticRegion(data, listOf("blocking", "warnings", "counts"))
        }
        return regions.filter(String::isNotBlank)
    }

    private fun validateStage(stage: String, data: JsonObject) {
        require(data.isNotEmpty()) { "模型没有返回可用的阶段对象" }
        when (stage) {
            "constraints" -> require(data.string("brief").isNotBlank()) { "创作约束缺少作品构想" }
            "concepts" -> {
                val rows = data["options"] as? JsonArray ?: error("创意方向缺少 options")
                require(rows.size == 1) { "创意方向必须恰好包含一张方案卡" }
                val card = rows.first().jsonObject
                listOf("title", "logline", "world_hook", "core_conflict", "opening_hook").forEach {
                    require(card.string(it).isNotBlank()) { "创意方向缺少 $it" }
                }
                val protagonist = card.objectValue("protagonist_seed")
                listOf("identity", "goal", "lack").forEach {
                    require(protagonist.string(it).isNotBlank()) { "主角种子缺少 $it" }
                }
            }
            "world_style" -> {
                listOf("writing_style", "world_tone", "story_structure", "pacing").forEach {
                    require(data.string(it).isNotBlank()) { "文风与世界观缺少 $it" }
                }
                require((data["worldbuilding"] as? JsonArray)?.isNotEmpty() == true) { "世界设定条目为空" }
            }
            "characters" -> {
                val characters = data.arrayValue("characters").mapNotNull { it as? JsonObject }
                require(characters.isNotEmpty()) { "角色数组为空" }
                val invalid = characters.filter {
                    it.string("name").isBlank() ||
                        it.string("role_type").isBlank() ||
                        firstText(it["goal"], it["current_goal"]).isBlank()
                }
                require(invalid.isEmpty()) { "角色缺少角色类型或当前目标" }
            }
            "locations" -> {
                val entries = data.arrayValue("entries").mapNotNull { it as? JsonObject }
                require(entries.isNotEmpty()) { "地点与势力数组为空" }
                val titles = entries.map { it.string("title").lowercase() }.filter(String::isNotBlank).toSet()
                val invalid = data.arrayValue("relations").mapNotNull { it as? JsonObject }.filter {
                    it.string("source_title").isBlank() ||
                        it.string("target_title").isBlank() ||
                        it.string("relation_type").isBlank() ||
                        it.string("source_title").lowercase() !in titles ||
                        it.string("target_title").lowercase() !in titles
                }
                require(invalid.isEmpty()) { "地点关系缺少端点、类型或引用了不存在的实体" }
            }
            "macro_outline" -> {
                listOf("story_overview", "core_conflict", "ending_direction").forEach {
                    require(data.string(it).isNotBlank()) { "全书主线缺少 $it" }
                }
                val volumes = data.arrayValue("volumes").mapNotNull { it as? JsonObject }
                require(volumes.isNotEmpty()) { "分卷规划为空" }
                require(volumes.all {
                    it.string("summary").isNotBlank() &&
                        it.int("start_chapter") > 0 &&
                        it.int("end_chapter") >= it.int("start_chapter")
                }) { "分卷缺少有效章节范围或摘要" }
                val requested = data.int("requested_volume_count")
                require(requested <= 0 || volumes.size == requested) {
                    "作者锁定要求为 $requested 卷，模型返回 ${volumes.size} 卷"
                }
            }
            "opening_outline" -> {
                val chapters = data["chapters"] as? JsonArray ?: error("前三章细纲缺少 chapters")
                val sections = data["sections"] as? JsonArray ?: error("前三章细纲缺少 sections")
                val expected = if (data.int("opening_chapter_count") == 15) 15 else 3
                require(chapters.size == expected) { "前${expected}章细纲必须恰好包含 $expected 章" }
                chapters.forEach { chapter ->
                    val id = chapter.jsonObject.string("client_id")
                    val count = sections.count { (it as? JsonObject)?.string("parent_client_id") == id }
                    require(count in 2..6) { "每章必须包含 2 至 6 个场景" }
                }
                val requiredMetadata = setOf(
                    "scene_number", "purpose", "location", "timeline", "pov_character", "characters",
                    "entry_state", "exit_state", "emotional_residue", "unresolved_actions",
                )
                require(sections.mapNotNull { it as? JsonObject }.all { section ->
                    section.string("client_id").isNotBlank() &&
                        section.string("parent_client_id").isNotBlank() &&
                        requiredMetadata.all(section.objectValue("metadata")::containsKey)
                }) { "开篇场景缺少结构化信息" }
            }
            "final_review" -> require((data["ready"] as? JsonPrimitive)?.booleanOrNull != null) { "最终审阅缺少 ready" }
        }
    }

    private fun parseObject(raw: String): JsonObject =
    MobileCreationJsonRepair.parseObjectDetailed(raw)?.value
        ?: error("模型返回的 JSON 结构无效")

    private fun strings(values: List<String>): JsonArray = JsonArray(
        values.map(String::trim).filter(String::isNotBlank).map(::JsonPrimitive),
    )

    private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())
    private fun JsonObject.arrayValue(name: String): JsonArray = get(name) as? JsonArray ?: JsonArray(emptyList())
    private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
    private fun JsonObject.int(name: String): Int = (get(name) as? JsonPrimitive)?.intOrNull ?: 0
    private fun JsonArray.takeArray(count: Int): JsonArray = JsonArray(take(count))
    private fun DirectApiConfig.isDeepSeek(): Boolean =
        listOf(displayName, baseUrl, model).any { it.contains("deepseek", ignoreCase = true) }

    private companion object {
        val FULL_WIDTH_DIGITS = mapOf(
            '０' to '0', '１' to '1', '２' to '2', '３' to '3', '４' to '4',
            '５' to '5', '６' to '6', '７' to '7', '８' to '8', '９' to '9',
        )
        val ROLE_ALIASES = mapOf(
            "protagonist" to "protagonist", "primary" to "protagonist", "lead" to "protagonist",
            "main character" to "protagonist", "主角" to "protagonist", "主人公" to "protagonist",
            "男主" to "protagonist", "女主" to "protagonist", "核心主角" to "protagonist", "第一主角" to "protagonist",
            "supporting" to "supporting", "support" to "supporting", "side character" to "supporting",
            "deuteragonist" to "supporting", "配角" to "supporting", "重要配角" to "supporting",
            "次要角色" to "supporting", "同伴" to "supporting", "伙伴" to "supporting", "队友" to "supporting",
            "盟友" to "supporting", "同门" to "supporting", "家人" to "supporting", "亲属" to "supporting",
            "父亲" to "supporting", "母亲" to "supporting", "爷爷" to "supporting", "奶奶" to "supporting",
            "antagonist" to "antagonist", "villain" to "antagonist", "rival" to "antagonist",
            "反派" to "antagonist", "反面角色" to "antagonist", "敌人" to "antagonist",
            "敌对者" to "antagonist", "对手" to "antagonist", "宿敌" to "antagonist",
            "mentor" to "mentor", "guide" to "mentor", "导师" to "mentor", "师父" to "mentor",
            "师傅" to "mentor", "老师" to "mentor", "前辈" to "mentor", "引路人" to "mentor",
            "other" to "other", "其他" to "other", "路人" to "other", "背景角色" to "other", "工具人" to "other",
        )
    }
}
