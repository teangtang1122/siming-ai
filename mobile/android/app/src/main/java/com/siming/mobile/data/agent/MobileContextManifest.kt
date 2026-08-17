package com.siming.mobile.data.agent

import android.content.Context
import java.security.MessageDigest
import java.util.UUID
import kotlin.math.max
import kotlin.math.min
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.put

internal data class MobileContextCategoryPolicy(
    val tier: Int,
    val maxItems: Int,
    val required: Boolean,
    val contentLimitChars: Int?,
    val fieldLimitChars: Int?,
    val sourceType: String?,
    val emptyLedgerText: String?,
)

internal data class MobileContextPolicy(
    val schemaVersion: Int,
    val policyVersion: Int,
    val indexVersion: Int,
    val sourceHash: String,
    val requiredCategories: Set<String>,
    val optionalCategories: Set<String>,
    val contextWindowTokens: Int,
    val safetyMarginTokens: Int,
    val minimumOutputReserveTokens: Int,
    val outputRatio: Double,
    val categories: Map<String, MobileContextCategoryPolicy>,
    val lexicalWeight: Double,
    val recencyWeight: Double,
    val structuralWeight: Double,
) {
    companion object {
        fun fromJson(root: JsonObject): MobileContextPolicy {
            val contract = root.objectValue("contract")
            val defaults = root.objectValue("model_defaults")
            val categoryRoot = root.objectValue("categories")
            val fallback = root.objectValue("ranking").objectValue("lexical_fallback")
            return MobileContextPolicy(
                schemaVersion = root.intValue("schema_version", 1),
                policyVersion = root.intValue("policy_version", 1),
                indexVersion = root.intValue("index_version", 1),
                sourceHash = root.stringValue("source_sha256"),
                requiredCategories = contract.stringList("required_categories").toSet(),
                optionalCategories = contract.stringList("optional_categories").toSet(),
                contextWindowTokens = defaults.intValue("context_window_tokens", 16_384),
                safetyMarginTokens = defaults.intValue("safety_margin_tokens", 512),
                minimumOutputReserveTokens = defaults.intValue("minimum_output_reserve_tokens", 2_048),
                outputRatio = defaults.doubleValue("output_ratio", 0.45),
                categories = categoryRoot.mapValues { (_, raw) ->
                    val value = raw as? JsonObject ?: JsonObject(emptyMap())
                    MobileContextCategoryPolicy(
                        tier = value.intValue("tier", 4),
                        maxItems = value.intValue("max_items", 1),
                        required = value.booleanValue("required"),
                        contentLimitChars = value.optionalInt("content_limit_chars"),
                        fieldLimitChars = value.optionalInt("field_limit_chars"),
                        sourceType = value.stringValue("source_type").ifBlank { null },
                        emptyLedgerText = value.stringValue("empty_ledger_text").ifBlank { null },
                    )
                },
                lexicalWeight = fallback.doubleValue("lexical", 0.70),
                recencyWeight = fallback.doubleValue("recency", 0.20),
                structuralWeight = fallback.doubleValue("structural", 0.10),
            )
        }
    }
}

internal class PcContextManifestPolicy(context: Context) {
    private val json = Json { ignoreUnknownKeys = true }
    val policy: MobileContextPolicy = context.assets.open(ASSET_NAME).bufferedReader(Charsets.UTF_8).use { reader ->
        MobileContextPolicy.fromJson(json.parseToJsonElement(reader.readText()) as JsonObject)
    }

    companion object {
        private const val ASSET_NAME = "pc_context_manifest_policy.json"
    }
}

internal data class MobileContextRequest(
    val outlineNodeId: String,
    val targetChapterId: String,
    val requirements: String,
    val involvedCharacters: List<String>,
    val characterLimit: Int,
    val recentLimit: Int,
) {
    fun fingerprint(projectId: String): String = mobileSha256(
        listOf(
            "project=$projectId",
            "outline=$outlineNodeId",
            "chapter=$targetChapterId",
            "requirements=$requirements",
            "characters=${involvedCharacters.joinToString("\u001e")}",
            "character_limit=$characterLimit",
            "recent_limit=$recentLimit",
        ).joinToString("\u001f"),
    )

    companion object {
        fun fromArgs(args: JsonObject, policy: MobileContextPolicy): MobileContextRequest {
            val maxCharacters = policy.categories["scene_character"]?.maxItems ?: 12
            val maxSummaries = policy.categories["previous_summary"]?.maxItems ?: 3
            return MobileContextRequest(
                outlineNodeId = args.stringValue("outline_node_id")
                    .ifBlank { args.stringValue("target_outline_node_id") },
                targetChapterId = args.stringValue("chapter_id")
                    .ifBlank { args.stringValue("target_chapter_id") },
                requirements = args.stringValue("requirements")
                    .ifBlank { args.stringValue("instruction") }
                    .ifBlank { args.stringValue("request") }
                    .trim(),
                involvedCharacters = args.stringList("involved_characters")
                    .ifEmpty { args.stringList("character_names") }
                    .map(String::trim)
                    .filter(String::isNotBlank)
                    .distinct()
                    .take(maxCharacters),
                characterLimit = args.intValue("character_limit", min(8, maxCharacters))
                    .coerceIn(1, maxCharacters),
                recentLimit = args.intValue("recent_limit", maxSummaries)
                    .coerceIn(1, maxSummaries),
            )
        }
    }
}

internal data class MobileContextInputs(
    val projectId: String,
    val model: String,
    val request: MobileContextRequest,
    val project: JsonObject,
    val styleText: String,
    val primaryRecords: List<JsonObject>,
    val rawRecords: List<JsonObject>,
    val orderedChapters: List<JsonObject>,
    val contextWindowTokens: Int? = null,
    val maxOutputTokens: Int? = null,
)

internal data class MobileContextCoverage(
    val required: Boolean,
    val status: String,
    val itemCount: Int,
    val reason: String = "",
) {
    fun toJson(): JsonObject = buildJsonObject {
        put("required", required)
        put("status", status)
        put("item_count", itemCount)
        if (reason.isNotBlank()) put("reason", reason)
    }
}

internal data class MobileContextManifestItem(
    val category: String,
    val sourceType: String,
    val sourceId: String?,
    val chunkId: String?,
    val title: String,
    val content: String,
    val required: Boolean,
    val tier: Int,
    val lexicalScore: Double?,
    val recencyScore: Double?,
    val structuralScore: Double?,
    val finalScore: Double,
    val selectionReason: String,
    val sourceHash: String = mobileSha256(content),
) {
    val estimatedTokens: Int get() = estimateMobileTokens(content)

    fun identity(): String = listOf(sourceType, sourceId.orEmpty(), chunkId.orEmpty()).joinToString("\u001f")

    fun toJson(includeContent: Boolean): JsonObject = buildJsonObject {
        put("category", category)
        put("source_type", sourceType)
        sourceId?.let { put("source_id", it) }
        chunkId?.let { put("chunk_id", it) }
        put("source_hash", sourceHash)
        put("title", title)
        put("required", required)
        put("pinned", false)
        put("tier", tier)
        put("scores", buildJsonObject {
            lexicalScore?.let { put("lexical", it) }
            recencyScore?.let { put("recency", it) }
            structuralScore?.let { put("structural", it) }
            put("final", finalScore)
        })
        put("selection_reason", selectionReason)
        put("estimated_tokens", estimatedTokens)
        if (includeContent) put("content", content)
    }
}

internal data class MobileContextManifest(
    val id: String,
    val projectId: String,
    val model: String,
    val policyVersion: Int,
    val indexVersion: Int,
    val policySourceHash: String,
    val status: String,
    val request: MobileContextRequest,
    val requestFingerprint: String,
    val selectionFingerprint: String,
    val contextWindowTokens: Int,
    val inputBudgetTokens: Int,
    val outputReserveTokens: Int,
    val safetyMarginTokens: Int,
    val items: List<MobileContextManifestItem>,
    val coverage: Map<String, MobileContextCoverage>,
    val warnings: List<String>,
) {
    val estimatedInputTokens: Int get() = items.sumOf(MobileContextManifestItem::estimatedTokens)
    val estimatedInputChars: Int get() = items.sumOf { it.content.length }

    fun items(category: String): List<MobileContextManifestItem> = items.filter { it.category == category }

    fun renderedContext(): String {
        val grouped = linkedMapOf<String, MutableList<MobileContextManifestItem>>()
        items.forEach { item -> grouped.getOrPut(item.category) { mutableListOf() } += item }
        return buildList {
            add("# Governed Task Context")
            grouped.forEach { (category, values) ->
                add("\n## $category")
                values.forEach { add("### ${it.title}\n${it.content}") }
            }
        }.joinToString("\n\n").trim()
    }

    fun toJson(includeContent: Boolean = false): JsonObject = buildJsonObject {
        put("id", id)
        put("project_id", projectId)
        put("task_type", "writing")
        put("model", model)
        put("execution_route", "android_standalone")
        put("policy_version", policyVersion)
        put("index_version", indexVersion)
        put("policy_source_sha256", policySourceHash)
        put("status", status)
        put("request_fingerprint", requestFingerprint)
        put("selection_fingerprint", selectionFingerprint)
        put("budget", buildJsonObject {
            put("context_window_tokens", contextWindowTokens)
            put("input_budget_tokens", inputBudgetTokens)
            put("output_reserve_tokens", outputReserveTokens)
            put("safety_margin_tokens", safetyMarginTokens)
            put("estimated_input_tokens", estimatedInputTokens)
            put("estimated_input_chars", estimatedInputChars)
            put("remaining_input_tokens", max(0, inputBudgetTokens - estimatedInputTokens))
        })
        put("coverage", buildJsonObject {
            coverage.forEach { (category, value) -> put(category, value.toJson()) }
        })
        put("warnings", JsonArray(warnings.map(::JsonPrimitive)))
        put("items", JsonArray(items.map { it.toJson(includeContent) }))
        if (includeContent) put("rendered_context", renderedContext())
    }
}

internal data class MobileContextValidation(
    val status: String,
    val detail: String,
    val current: MobileContextManifest,
) {
    val ready: Boolean get() = status == "ready"
}

/** Deterministic Android projection of the PC writing ContextManifest policy. */
internal class MobileContextManifestEngine(
    private val policy: MobileContextPolicy,
) {
    fun prepare(inputs: MobileContextInputs, id: String = UUID.randomUUID().toString()): MobileContextManifest {
        val requestFingerprint = inputs.request.fingerprint(inputs.projectId)
        val coverage = linkedMapOf<String, MobileContextCoverage>()
        val candidates = mutableListOf<MobileContextManifestItem>()
        val warnings = mutableListOf<String>()

        addStyle(inputs, candidates, coverage)
        addTargetOutline(inputs, candidates, coverage)
        addRequirements(inputs, candidates, coverage)
        addPreviousSummaries(inputs, candidates, coverage)
        val resolution = addSceneCharacters(inputs, candidates, coverage)
        addGovernance(inputs, candidates, coverage)
        addWorldRetrieval(inputs, candidates, coverage)

        val requestedNames = inputs.request.involvedCharacters.toSet()
        val matchedNames = resolution.characters.mapTo(mutableSetOf()) { it.stringValue("name") } +
            resolution.resolvedAliases.keys
        val missingNames = requestedNames - matchedNames
        if (missingNames.isNotEmpty()) {
            warnings += "部分指定角色未命中角色卡或别名：${missingNames.joinToString("、")}"
        }

        val window = max(1, inputs.contextWindowTokens ?: policy.contextWindowTokens)
        val ratioLimit = (window * policy.outputRatio).toInt()
        val configuredLimit = inputs.maxOutputTokens ?: ratioLimit
        val outputReserve = max(policy.minimumOutputReserveTokens, min(configuredLimit, ratioLimit))
        val inputBudget = max(0, window - outputReserve - policy.safetyMarginTokens)
        val selected = budget(candidates, coverage, warnings, inputBudget)
        val missingRequired = policy.requiredCategories.filter { category ->
            coverage[category]?.status !in setOf("covered", "not_applicable")
        }
        val status = if (missingRequired.isEmpty()) "ready" else "needs_confirmation"
        if (missingRequired.isNotEmpty()) {
            warnings += "Required context is missing: ${missingRequired.joinToString(", ")}"
        }
        warnings += "手机独立模式使用保守 16K 上下文与本地词法检索；未启用 PC FTS、向量检索或 pinned chunks。"

        val selectionFingerprint = mobileSha256(
            selected.joinToString("\u001e") { item ->
                listOf(item.category, item.sourceType, item.sourceId.orEmpty(), item.chunkId.orEmpty(), item.sourceHash)
                    .joinToString("\u001f")
            },
        )
        return MobileContextManifest(
            id = id,
            projectId = inputs.projectId,
            model = inputs.model,
            policyVersion = policy.policyVersion,
            indexVersion = policy.indexVersion,
            policySourceHash = policy.sourceHash,
            status = status,
            request = inputs.request,
            requestFingerprint = requestFingerprint,
            selectionFingerprint = selectionFingerprint,
            contextWindowTokens = window,
            inputBudgetTokens = inputBudget,
            outputReserveTokens = outputReserve,
            safetyMarginTokens = policy.safetyMarginTokens,
            items = selected,
            coverage = coverage.toMap(),
            warnings = warnings.distinct(),
        )
    }

    fun validate(existing: MobileContextManifest, inputs: MobileContextInputs): MobileContextValidation {
        val current = prepare(inputs, id = existing.id)
        val staleReason = when {
            existing.policyVersion != policy.policyVersion ||
                existing.indexVersion != policy.indexVersion ||
                existing.policySourceHash != policy.sourceHash -> "PC 上下文策略版本已变化，请重新预检。"
            existing.model != inputs.model -> "模型已从 ${existing.model} 切换为 ${inputs.model}，请重新预检。"
            existing.requestFingerprint != current.requestFingerprint -> "写作目标、要求或角色选择已变化，请重新预检。"
            existing.selectionFingerprint != current.selectionFingerprint -> "已选择的上下文来源发生变化，请重新预检。"
            current.status != "ready" -> "当前必选上下文不完整，需要作者确认。"
            else -> ""
        }
        return if (staleReason.isBlank()) {
            MobileContextValidation("ready", "ContextManifest 仍然有效。", current)
        } else if (current.status == "needs_confirmation" && existing.requestFingerprint == current.requestFingerprint) {
            MobileContextValidation("needs_confirmation", staleReason, current.copy(status = "needs_confirmation"))
        } else {
            MobileContextValidation("stale", staleReason, current.copy(status = "stale"))
        }
    }

    private fun addStyle(
        inputs: MobileContextInputs,
        candidates: MutableList<MobileContextManifestItem>,
        coverage: MutableMap<String, MobileContextCoverage>,
    ) {
        val content = inputs.styleText.trim()
        if (content.isBlank()) {
            coverage["style"] = MobileContextCoverage(true, "missing", 0, "Project style is required.")
            return
        }
        candidates += item(
            category = "style",
            sourceType = "project_style",
            sourceId = inputs.projectId,
            title = "Project style and fixed constraints",
            content = content,
            required = true,
            score = 1.0,
            reason = "Required project-level style and author constraints.",
        )
        coverage["style"] = MobileContextCoverage(true, "covered", 1)
    }

    private fun addTargetOutline(
        inputs: MobileContextInputs,
        candidates: MutableList<MobileContextManifestItem>,
        coverage: MutableMap<String, MobileContextCoverage>,
    ) {
        val target = inputs.request.outlineNodeId.takeIf(String::isNotBlank)?.let { id ->
            inputs.rawRecords.firstOrNull { it.mobileRecordType() == "outline_node" && it.stringValue("id") == id }
                ?: inputs.primaryRecords.firstOrNull {
                    it.mobileRecordType() == "outline_node" && it.stringValue("id") == id
                }
        }
        if (target == null) {
            val reason = if (inputs.request.outlineNodeId.isBlank()) {
                "Writing needs a target outline or section."
            } else {
                "The selected outline node no longer exists."
            }
            coverage["target_outline"] = MobileContextCoverage(true, "missing", 0, reason)
            return
        }
        candidates += item(
            category = "target_outline",
            sourceType = "outline",
            sourceId = target.stringValue("id"),
            title = target.stringValue("title").ifBlank { "Target outline" },
            content = mobileOutlineText(target, policy.categories["target_outline"]?.fieldLimitChars ?: 1_200),
            required = true,
            score = 1.0,
            reason = "Target outline/section required by the writing contract.",
            sourceHash = mobileSha256(canonicalMobileJson(target)),
        )
        coverage["target_outline"] = MobileContextCoverage(true, "covered", 1)
    }

    private fun addRequirements(
        inputs: MobileContextInputs,
        candidates: MutableList<MobileContextManifestItem>,
        coverage: MutableMap<String, MobileContextCoverage>,
    ) {
        val text = inputs.request.requirements.trim()
        if (text.isBlank()) {
            coverage["user_requirement"] = MobileContextCoverage(false, "not_applicable", 0)
            return
        }
        val limit = policy.categories["user_requirement"]?.contentLimitChars ?: 4_000
        candidates += item(
            category = "user_requirement",
            sourceType = "inline",
            sourceId = "user-requirement",
            title = "Author request",
            content = cleanMobileText(text, limit),
            required = false,
            score = 0.95,
            reason = "Explicit request passed to this task.",
        )
        coverage["user_requirement"] = MobileContextCoverage(false, "covered", 1)
    }

    private fun addPreviousSummaries(
        inputs: MobileContextInputs,
        candidates: MutableList<MobileContextManifestItem>,
        coverage: MutableMap<String, MobileContextCoverage>,
    ) {
        val limit = policy.categories["previous_summary"]?.contentLimitChars ?: 1_600
        val rows = inputs.orderedChapters
            .filter { inputs.request.targetChapterId.isBlank() || it.stringValue("id") != inputs.request.targetChapterId }
            .filter { it.stringValue("summary").isNotBlank() }
            .takeLast(inputs.request.recentLimit)
        rows.forEachIndexed { index, chapter ->
            val score = max(0.2, 0.9 - (rows.lastIndex - index) * 0.1)
            candidates += item(
                category = "previous_summary",
                sourceType = "chapter_summary",
                sourceId = chapter.stringValue("id"),
                title = "Previous summary: ${chapter.stringValue("title")}",
                content = cleanMobileText(chapter.stringValue("summary"), limit),
                required = false,
                score = score,
                recency = max(0.2, 1.0 - (rows.lastIndex - index) * 0.15),
                structural = 0.8,
                reason = "Most recent confirmed chapter summary.",
                sourceHash = mobileSha256(chapter.stringValue("summary")),
            )
        }
        coverage["previous_summary"] = MobileContextCoverage(
            required = false,
            status = if (rows.isEmpty()) "not_applicable" else "covered",
            itemCount = rows.size,
        )
    }

    private fun addSceneCharacters(
        inputs: MobileContextInputs,
        candidates: MutableList<MobileContextManifestItem>,
        coverage: MutableMap<String, MobileContextCoverage>,
    ): PcCharacterResolution {
        val resolution = resolvePcCharacters(
            inputs.rawRecords,
            inputs.request.outlineNodeId.takeIf(String::isNotBlank),
            inputs.request.involvedCharacters,
            inputs.request.characterLimit,
        )
        val limit = policy.categories["scene_character"]?.contentLimitChars ?: 12_000
        resolution.characters.forEach { character ->
            candidates += item(
                category = "scene_character",
                sourceType = "character",
                sourceId = character.stringValue("id"),
                title = character.stringValue("name").ifBlank { "Character" },
                content = cleanMobileText(pcCharacterDetails(inputs.rawRecords, listOf(character)), limit),
                required = false,
                score = 0.95,
                structural = 0.95,
                reason = "Character explicitly selected or linked to the target outline.",
                sourceHash = mobileSha256(
                    buildString {
                        append(canonicalMobileJson(character))
                        inputs.rawRecords.asSequence()
                            .filter { it.mobileRecordType() == "character_relationship" }
                            .filter {
                                it.stringValue("from") == character.stringValue("id") ||
                                    it.stringValue("to") == character.stringValue("id")
                            }
                            .sortedBy { it.stringValue("id") }
                            .forEach { append("\u001e").append(canonicalMobileJson(it)) }
                    },
                ),
            )
        }
        val characterCount = inputs.rawRecords.count { it.mobileRecordType() == "character" }
        coverage["scene_character"] = MobileContextCoverage(
            required = false,
            status = when {
                resolution.characters.isNotEmpty() -> "covered"
                characterCount == 0 -> "not_applicable"
                else -> "missing"
            },
            itemCount = resolution.characters.size,
            reason = if (characterCount > 0 && resolution.characters.isEmpty()) "No target character was resolved." else "",
        )
        return resolution
    }

    private fun addGovernance(
        inputs: MobileContextInputs,
        candidates: MutableList<MobileContextManifestItem>,
        coverage: MutableMap<String, MobileContextCoverage>,
    ) {
        val category = policy.categories["narrative_governance"]
        val content = pcGovernanceContext(inputs.rawRecords)
            .ifBlank { category?.emptyLedgerText ?: "Narrative governance: no due or high-risk items." }
        candidates += item(
            category = "narrative_governance",
            sourceType = "narrative_governance",
            sourceId = inputs.request.targetChapterId.ifBlank { inputs.projectId },
            title = "Narrative governance ledger",
            content = cleanMobileText(content, category?.contentLimitChars ?: 5_000),
            required = false,
            score = 0.85,
            structural = 0.85,
            reason = "Current debts, foreshadowing, causal chains and state conflicts.",
        )
        coverage["narrative_governance"] = MobileContextCoverage(false, "covered", 1)
    }

    private fun addWorldRetrieval(
        inputs: MobileContextInputs,
        candidates: MutableList<MobileContextManifestItem>,
        coverage: MutableMap<String, MobileContextCoverage>,
    ) {
        val outline = candidates.firstOrNull { it.category == "target_outline" }
        val query = listOf(inputs.request.requirements, outline?.title.orEmpty(), outline?.content.orEmpty())
            .filter(String::isNotBlank)
            .joinToString("\n")
            .take(12_000)
        val queryTokens = lexicalTokens(query)
        val rows = inputs.rawRecords
            .filter { it.mobileRecordType() == "world_entry" }
            .distinctBy { it.stringValue("id") }
            .sortedWith(
                compareByDescending<JsonObject> { it.stringValue("updated_at") }
                    .thenByDescending { it.stringValue("created_at") }
                    .thenBy { it.stringValue("title") },
            )
        if (queryTokens.isEmpty() || rows.isEmpty()) {
            coverage["hybrid_retrieval"] = MobileContextCoverage(false, "not_applicable", 0)
            return
        }
        val rawScores = rows.map { world ->
            val text = listOf(
                world.stringValue("dimension"),
                world.stringValue("title"),
                world.stringValue("content"),
                world.stringValue("constraints"),
                world.stringValue("plot_usage"),
            ).joinToString("\n")
            world to lexicalOverlap(queryTokens, lexicalTokens(text))
        }
        val maxLexical = rawScores.maxOfOrNull { it.second } ?: 0.0
        val category = policy.categories["hybrid_retrieval"]
        val ranked = rawScores.mapIndexedNotNull { index, (world, rawLexical) ->
            if (rawLexical <= 0.0 || maxLexical <= 0.0) return@mapIndexedNotNull null
            val lexical = rawLexical / maxLexical
            val recency = max(0.05, 1.0 / (1.0 + index.toDouble() / 8.0))
            val structural = 0.25
            val final = lexical * policy.lexicalWeight +
                recency * policy.recencyWeight +
                structural * policy.structuralWeight
            item(
                category = "hybrid_retrieval",
                sourceType = "worldbuilding",
                sourceId = world.stringValue("id"),
                title = world.stringValue("title").ifBlank { world.stringValue("dimension").ifBlank { "Worldbuilding" } },
                content = cleanMobileText(
                    "【${world.stringValue("dimension").ifBlank { "culture" }}·${world.stringValue("title")}】\n" +
                        world.stringValue("content"),
                    category?.contentLimitChars ?: 1_800,
                ),
                required = false,
                score = final,
                lexical = lexical,
                recency = recency,
                structural = structural,
                reason = "Lexical fallback ranking: lexical 70%, recency 20%, structure 10%.",
                sourceHash = mobileSha256(canonicalMobileJson(world)),
            )
        }.sortedWith(compareByDescending<MobileContextManifestItem> { it.finalScore }.thenBy { it.title })
            .take(category?.maxItems ?: 24)
        candidates += ranked
        coverage["hybrid_retrieval"] = MobileContextCoverage(
            required = false,
            status = if (ranked.isEmpty()) "not_applicable" else "covered",
            itemCount = ranked.size,
        )
    }

    private fun budget(
        candidates: List<MobileContextManifestItem>,
        coverage: MutableMap<String, MobileContextCoverage>,
        warnings: MutableList<String>,
        inputBudget: Int,
    ): List<MobileContextManifestItem> {
        val selected = mutableListOf<MobileContextManifestItem>()
        val seen = mutableSetOf<String>()
        var used = 0
        candidates.sortedWith(
            compareBy<MobileContextManifestItem> { it.tier }
                .thenBy { if (it.required) 0 else 1 }
                .thenByDescending { it.finalScore }
                .thenBy { it.title },
        ).forEach { candidate ->
            if (candidate.identity() in seen) return@forEach
            if (used + candidate.estimatedTokens <= inputBudget) {
                selected += candidate
                used += candidate.estimatedTokens
                seen += candidate.identity()
            } else if (candidate.required) {
                coverage[candidate.category] = MobileContextCoverage(
                    required = true,
                    status = "missing",
                    itemCount = 0,
                    reason = "Required anchor exceeds the remaining context budget.",
                )
                warnings += "Required context '${candidate.title}' did not fit the input budget."
            } else if (candidate.category == "hybrid_retrieval" && candidate.finalScore >= 0.6) {
                warnings += "Relevant retrieved source '${candidate.title}' was omitted by the budget."
            }
        }
        val counts = selected.groupingBy(MobileContextManifestItem::category).eachCount()
        coverage.keys.toList().forEach { category ->
            val current = coverage.getValue(category)
            if (current.status == "covered") {
                val count = counts[category] ?: 0
                if (count == 0) {
                    coverage[category] = current.copy(
                        status = if (current.required) "missing" else "not_selected",
                        itemCount = 0,
                    )
                } else {
                    coverage[category] = current.copy(itemCount = count)
                }
            }
        }
        return selected
    }

    private fun item(
        category: String,
        sourceType: String,
        sourceId: String?,
        title: String,
        content: String,
        required: Boolean,
        score: Double,
        reason: String,
        lexical: Double? = null,
        recency: Double? = null,
        structural: Double? = null,
        sourceHash: String? = null,
    ): MobileContextManifestItem = MobileContextManifestItem(
        category = category,
        sourceType = sourceType,
        sourceId = sourceId,
        chunkId = null,
        title = title,
        content = content,
        required = required,
        tier = policy.categories[category]?.tier ?: 4,
        lexicalScore = lexical,
        recencyScore = recency,
        structuralScore = structural,
        finalScore = score,
        selectionReason = reason,
        sourceHash = sourceHash ?: mobileSha256(content),
    )
}

internal fun estimateMobileTokens(text: String): Int {
    if (text.isEmpty()) return 0
    val cjk = text.count { character -> character in '\u4e00'..'\u9fff' || character in '\u3400'..'\u4dbf' }
    val nonCjk = text.length - cjk
    return cjk + max(1, nonCjk / 4)
}

internal fun mobileSha256(value: String): String = MessageDigest.getInstance("SHA-256")
    .digest(value.toByteArray(Charsets.UTF_8))
    .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }

private fun mobileOutlineText(node: JsonObject, fieldLimit: Int): String = buildList {
    add("Outline: ${node.stringValue("title")}")
    add("Node type: ${node.stringValue("node_type").ifBlank { "unknown" }}")
    listOf(
        "Summary" to node.stringValue("summary"),
        "Planned" to node.stringValue("planned_summary"),
        "Actual" to node.stringValue("actual_summary"),
        "Status" to node.stringValue("status"),
    ).forEach { (label, value) -> if (value.isNotBlank()) add("$label: ${cleanMobileText(value, fieldLimit)}") }
}.joinToString("\n")

private fun cleanMobileText(value: String, limit: Int): String {
    val text = value.trim()
    if (text.length <= limit) return text
    if (limit <= 3) return text.take(limit)
    return text.take(limit - 3).trimEnd() + "..."
}

private fun lexicalTokens(text: String): Set<String> {
    if (text.isBlank()) return emptySet()
    val tokens = linkedSetOf<String>()
    val latin = StringBuilder()
    fun flushLatin() {
        if (latin.isNotEmpty()) {
            tokens += latin.toString().lowercase()
            latin.clear()
        }
    }
    val cjkRun = StringBuilder()
    fun flushCjk() {
        if (cjkRun.isEmpty()) return
        for (index in cjkRun.indices) {
            tokens += cjkRun[index].toString()
            if (index + 1 < cjkRun.length) tokens += cjkRun.substring(index, index + 2)
        }
        cjkRun.clear()
    }
    text.forEach { character ->
        when {
            character in '\u4e00'..'\u9fff' || character in '\u3400'..'\u4dbf' -> {
                flushLatin()
                cjkRun.append(character)
            }
            character.isLetterOrDigit() -> {
                flushCjk()
                latin.append(character.lowercaseChar())
            }
            else -> {
                flushLatin()
                flushCjk()
            }
        }
    }
    flushLatin()
    flushCjk()
    return tokens
}

private fun lexicalOverlap(query: Set<String>, source: Set<String>): Double {
    if (query.isEmpty() || source.isEmpty()) return 0.0
    return query.count { it in source }.toDouble() / query.size.toDouble()
}

private fun canonicalMobileJson(element: JsonElement): String = when (element) {
    is JsonObject -> element.entries.sortedBy { it.key }
        .joinToString(prefix = "{", postfix = "}", separator = ",") { (key, value) ->
            JsonPrimitive(key).toString() + ":" + canonicalMobileJson(value)
        }
    is JsonArray -> element.joinToString(prefix = "[", postfix = "]", separator = ",", transform = ::canonicalMobileJson)
    else -> element.toString()
}

private fun JsonObject.mobileRecordType(): String = stringValue("_record_type")

private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())

private fun JsonObject.stringValue(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()

private fun JsonObject.intValue(name: String, fallback: Int): Int = (get(name) as? JsonPrimitive)?.intOrNull ?: fallback

private fun JsonObject.optionalInt(name: String): Int? = (get(name) as? JsonPrimitive)?.intOrNull

private fun JsonObject.doubleValue(name: String, fallback: Double): Double =
    (get(name) as? JsonPrimitive)?.doubleOrNull ?: fallback

private fun JsonObject.booleanValue(name: String): Boolean = (get(name) as? JsonPrimitive)?.booleanOrNull ?: false

private fun JsonObject.stringList(name: String): List<String> = when (val value = get(name)) {
    is JsonArray -> value.mapNotNull { (it as? JsonPrimitive)?.contentOrNull?.trim() }.filter(String::isNotBlank)
    is JsonPrimitive -> value.contentOrNull.orEmpty().split(',', '，').map(String::trim).filter(String::isNotBlank)
    else -> emptyList()
}
