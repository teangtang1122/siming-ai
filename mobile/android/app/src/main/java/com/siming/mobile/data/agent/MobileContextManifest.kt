package com.siming.mobile.data.agent

import android.content.Context
import com.siming.mobile.data.network.DirectApiConfig
import java.security.MessageDigest
import java.util.UUID
import kotlin.math.max
import kotlin.math.min
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
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.put

internal const val OUTLINE_PROPOSAL_MAX_NODES = 12

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
    val taskType: String,
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
    val softInputTargetTokens: Int,
    val searchExcerptChars: Int,
    val searchSourceTypes: Set<String>,
    val categories: Map<String, MobileContextCategoryPolicy>,
    val lexicalWeight: Double,
    val recencyWeight: Double,
    val structuralWeight: Double,
) {
    companion object {
        fun fromJson(root: JsonObject, taskType: String = "writing"): MobileContextPolicy {
            val embedded = root.objectValue("task_policies")[taskType] as? JsonObject
            val policyRoot = embedded ?: root
            val contract = policyRoot.objectValue("contract")
            val defaults = policyRoot.objectValue("model_defaults")
            val categoryRoot = policyRoot.objectValue("categories")
            val fallback = policyRoot.objectValue("ranking").objectValue("lexical_fallback")
            val selection = policyRoot.objectValue("selection")
            return MobileContextPolicy(
                taskType = policyRoot.stringValue("task_type").ifBlank { taskType },
                schemaVersion = policyRoot.intValue("schema_version", 1),
                policyVersion = policyRoot.intValue("policy_version", 1),
                indexVersion = policyRoot.intValue("index_version", 1),
                sourceHash = root.stringValue("source_sha256"),
                requiredCategories = contract.stringList("required_categories").toSet(),
                optionalCategories = contract.stringList("optional_categories").toSet(),
                contextWindowTokens = defaults.intValue(
                    "context_window_tokens",
                    DirectApiConfig.DEFAULT_CONTEXT_WINDOW_TOKENS,
                ),
                safetyMarginTokens = defaults.intValue("safety_margin_tokens", 512),
                minimumOutputReserveTokens = defaults.intValue("minimum_output_reserve_tokens", 2_048),
                outputRatio = defaults.doubleValue("output_ratio", 0.45),
                softInputTargetTokens = defaults.intValue("soft_input_target_tokens", 32_000),
                searchExcerptChars = selection.intValue("search_excerpt_chars", 600),
                searchSourceTypes = selection.stringList("search_source_types").toSet(),
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
    private val root: JsonObject = context.assets.open(ASSET_NAME).bufferedReader(Charsets.UTF_8).use { reader ->
        json.parseToJsonElement(reader.readText()) as JsonObject
    }
    val policy: MobileContextPolicy get() = policy("writing")

    fun policy(taskType: String): MobileContextPolicy = MobileContextPolicy.fromJson(root, taskType)

    companion object {
        private const val ASSET_NAME = "pc_context_manifest_policy.json"
    }
}

internal data class MobileContextRequest(
    val outlineNodeId: String = "",
    val targetChapterId: String = "",
    val sourceDraftId: String = "",
    val requirements: String,
    val minimumHanCharacters: Int? = null,
    val taskType: String = "writing",
    val parentId: String = "",
    val insertAfterId: String = "",
    val batchCount: Int = 1,
) {
    fun toJson(): JsonObject = buildJsonObject {
        put("task_type", taskType)
        put("outline_node_id", outlineNodeId)
        put("target_chapter_id", targetChapterId)
        put("source_draft_id", sourceDraftId)
        put("requirements", requirements)
        minimumHanCharacters?.let { put("minimum_han_characters", it) }
        put("parent_id", parentId)
        put("insert_after_id", insertAfterId)
        put("batch_count", batchCount)
    }

    fun fingerprint(projectId: String): String = mobileSha256(
        listOf(
            "project=$projectId",
            "outline=$outlineNodeId",
            "chapter=$targetChapterId",
            "source_draft=$sourceDraftId",
            "task=$taskType",
            "parent=$parentId",
            "insert_after=$insertAfterId",
            "batch_count=$batchCount",
            "requirements=$requirements",
            "minimum_han_characters=${minimumHanCharacters ?: ""}",
        ).joinToString("\u001f"),
    )

    companion object {
        fun fromJson(root: JsonObject): MobileContextRequest = MobileContextRequest(
            outlineNodeId = root.stringValue("outline_node_id"),
            targetChapterId = root.stringValue("target_chapter_id"),
            sourceDraftId = root.stringValue("source_draft_id"),
            requirements = root.stringValue("requirements"),
            minimumHanCharacters = root.optionalInt("minimum_han_characters")
                ?.takeIf { it in 1..100_000 },
            taskType = root.stringValue("task_type").ifBlank { "writing" },
            parentId = root.stringValue("parent_id"),
            insertAfterId = root.stringValue("insert_after_id"),
            batchCount = root.intValue("batch_count", 1)
                .coerceIn(1, OUTLINE_PROPOSAL_MAX_NODES),
        )

        fun fromArgs(taskType: String, args: JsonObject): MobileContextRequest = MobileContextRequest(
            outlineNodeId = args.stringValue("outline_node_id"),
            targetChapterId = args.stringValue("target_chapter_id"),
            sourceDraftId = args.stringValue("source_draft_id"),
            requirements = args.stringValue("requirements").trim(),
            minimumHanCharacters = args.optionalInt("minimum_han_characters")
                ?.takeIf { it in 1..100_000 },
            taskType = taskType,
            parentId = args.stringValue("parent_id"),
            insertAfterId = args.stringValue("insert_after_id"),
            batchCount = args.intValue("batch_count", 1)
                .coerceIn(1, OUTLINE_PROPOSAL_MAX_NODES),
        )
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
    val sourceDraft: MobileChapterWriteRun? = null,
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

    companion object {
        fun fromJson(root: JsonObject): MobileContextCoverage = MobileContextCoverage(
            required = root.booleanValue("required"),
            status = root.stringValue("status"),
            itemCount = root.intValue("item_count", 0),
            reason = root.stringValue("reason"),
        )
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
    val itemId: String get() = mobileSha256(
        listOf(sourceType, sourceId.orEmpty(), chunkId.orEmpty(), sourceHash).joinToString("\u001f"),
    )

    fun identity(): String = listOf(sourceType, sourceId.orEmpty(), chunkId.orEmpty()).joinToString("\u001f")

    fun toJson(includeContent: Boolean): JsonObject = buildJsonObject {
        put("item_id", itemId)
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

    companion object {
        fun fromJson(root: JsonObject): MobileContextManifestItem {
            val scores = root.objectValue("scores")
            return MobileContextManifestItem(
                category = root.stringValue("category"),
                sourceType = root.stringValue("source_type"),
                sourceId = root.stringValue("source_id").ifBlank { null },
                chunkId = root.stringValue("chunk_id").ifBlank { null },
                title = root.stringValue("title"),
                content = root.stringValue("content"),
                required = root.booleanValue("required"),
                tier = root.intValue("tier", 4),
                lexicalScore = scores.optionalDouble("lexical"),
                recencyScore = scores.optionalDouble("recency"),
                structuralScore = scores.optionalDouble("structural"),
                finalScore = scores.doubleValue("final", 0.0),
                selectionReason = root.stringValue("selection_reason"),
                sourceHash = root.stringValue("source_hash").ifBlank {
                    mobileSha256(root.stringValue("content"))
                },
            )
        }
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
    val softInputTargetTokens: Int,
    val outputReserveTokens: Int,
    val safetyMarginTokens: Int,
    val items: List<MobileContextManifestItem>,
    val coverage: Map<String, MobileContextCoverage>,
    val warnings: List<String>,
    val selectionToken: String? = null,
    val contextDelivery: MobileContextDeliveryState? = null,
) {
    val generationItems: List<MobileContextManifestItem>
        get() = items.filter { it.category != "agent_search" }
    val estimatedInputTokens: Int get() = generationItems.sumOf(MobileContextManifestItem::estimatedTokens)
    val estimatedInputChars: Int get() = generationItems.sumOf { it.content.length }

    fun items(category: String): List<MobileContextManifestItem> = items.filter { it.category == category }

    fun renderedContext(): String {
        val grouped = linkedMapOf<String, MutableList<MobileContextManifestItem>>()
        generationItems.forEach { item -> grouped.getOrPut(item.category) { mutableListOf() } += item }
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
        put("task_type", request.taskType)
        put("model", model)
        put("execution_route", "android_standalone")
        put("policy_version", policyVersion)
        put("index_version", indexVersion)
        put("policy_source_sha256", policySourceHash)
        put("status", status)
        put("request_fingerprint", requestFingerprint)
        put("selection_fingerprint", selectionFingerprint)
        put("selection", buildJsonObject {
            put("status", if (selectionToken.isNullOrBlank()) "pending" else "ready")
            selectionToken?.let { put("token", it) }
            put(
                "selected_item_ids",
                JsonArray(items.filter { it.category == "agent_selected" }.map { JsonPrimitive(it.itemId) }),
            )
        })
        contextDelivery?.let { put("context_delivery", it.toJson()) }
        put("budget", buildJsonObject {
            put("context_window_tokens", contextWindowTokens)
            put("input_budget_tokens", inputBudgetTokens)
            put("soft_input_target_tokens", softInputTargetTokens)
            put("soft_target_exceeded", estimatedInputTokens > softInputTargetTokens)
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

    companion object {
        fun fromJson(root: JsonObject, request: MobileContextRequest): MobileContextManifest {
            val budget = root.objectValue("budget")
            val coverage = root.objectValue("coverage").mapValues { (_, value) ->
                MobileContextCoverage.fromJson(value as? JsonObject ?: JsonObject(emptyMap()))
            }
            val items = (root["items"] as? JsonArray).orEmpty().mapNotNull { value ->
                (value as? JsonObject)?.let(MobileContextManifestItem::fromJson)
            }
            val inputBudgetTokens = budget.intValue("input_budget_tokens", 8_000)
            return MobileContextManifest(
                id = root.stringValue("id"),
                projectId = root.stringValue("project_id"),
                model = root.stringValue("model"),
                policyVersion = root.intValue("policy_version", 1),
                indexVersion = root.intValue("index_version", 1),
                policySourceHash = root.stringValue("policy_source_sha256"),
                status = root.stringValue("status"),
                request = request,
                requestFingerprint = root.stringValue("request_fingerprint"),
                selectionFingerprint = root.stringValue("selection_fingerprint"),
                contextWindowTokens = budget.intValue(
                    "context_window_tokens",
                    DirectApiConfig.DEFAULT_CONTEXT_WINDOW_TOKENS,
                ),
                inputBudgetTokens = inputBudgetTokens,
                softInputTargetTokens = budget.intValue(
                    "soft_input_target_tokens",
                    min(32_000, inputBudgetTokens),
                ),
                outputReserveTokens = budget.intValue("output_reserve_tokens", 2_048),
                safetyMarginTokens = budget.intValue("safety_margin_tokens", 512),
                items = items,
                coverage = coverage,
                warnings = root.stringList("warnings"),
                selectionToken = root.objectValue("selection").stringValue("token").ifBlank { null },
                contextDelivery = (root["context_delivery"] as? JsonObject)
                    ?.let(MobileContextDeliveryState::fromJson),
            ).also { manifest ->
                require(manifest.id.isNotBlank() && manifest.projectId.isNotBlank()) {
                    "持久化 ContextManifest 缺少标识"
                }
                require(manifest.items.all { it.content.isNotBlank() }) {
                    "持久化 ContextManifest 缺少来源正文"
                }
            }
        }
    }
}

internal data class MobileContextValidation(
    val status: String,
    val detail: String,
    val current: MobileContextManifest,
) {
    val ready: Boolean get() = status == "ready"
}

internal data class MobileContextSearch(
    val manifest: MobileContextManifest,
    val items: List<MobileContextManifestItem>,
    val cursor: Int,
    val limit: Int,
    val nextCursor: Int?,
    val hasMore: Boolean,
)

internal data class MobileContextSelection(
    val manifest: MobileContextManifest,
    val accepted: List<MobileContextManifestItem>,
    val rejected: List<String>,
) {
    val ready: Boolean get() = rejected.isEmpty() && !manifest.selectionToken.isNullOrBlank()
}

internal data class MobileContextEvidenceResolution(
    val itemIds: List<String>,
    val rejected: List<String>,
)

/** Resolve only the documented evidence selectors against this manifest's search results. */
internal fun resolveMobileContextEvidenceSources(
    manifest: MobileContextManifest,
    sources: List<JsonObject>,
): MobileContextEvidenceResolution {
    val candidates = manifest.items.filter { it.category == "agent_search" }
    val resolved = mutableListOf<String>()
    val rejected = mutableListOf<String>()

    sources.forEachIndexed { index, source ->
        val itemId = source.stringValue("item_id").trim()
        val chunkId = source.stringValue("chunk_id").trim()
        val sourceType = source.stringValue("source_type").trim()
        val sourceId = source.stringValue("source_id").trim()
        val sourceHash = source.stringValue("source_hash").trim()

        if (itemId.isBlank() && chunkId.isBlank() && (sourceType.isBlank() || sourceId.isBlank())) {
            rejected += "sources[$index] 缺少 item_id、chunk_id 或 source_type + source_id。"
            return@forEachIndexed
        }

        val matches = candidates.filter { candidate ->
            (itemId.isBlank() || candidate.itemId == itemId) &&
                (chunkId.isBlank() || candidate.chunkId == chunkId) &&
                (sourceType.isBlank() || candidate.sourceType == sourceType) &&
                (sourceId.isBlank() || candidate.sourceId == sourceId) &&
                (sourceHash.isBlank() || candidate.sourceHash == sourceHash)
        }
        when (matches.size) {
            0 -> rejected += "sources[$index] 与当前 search_task_context 检索结果不匹配。"
            1 -> resolved += matches.single().itemId
            else -> rejected += "sources[$index] 匹配多个检索结果，请改用唯一 item_id。"
        }
    }

    return MobileContextEvidenceResolution(resolved.distinct(), rejected.distinct())
}

/** Deterministic Android projection of a PC model-selected ContextManifest policy. */
internal class MobileContextManifestEngine(
    private val policy: MobileContextPolicy,
) {
    fun prepare(inputs: MobileContextInputs, id: String = UUID.randomUUID().toString()): MobileContextManifest {
        require(inputs.request.taskType == policy.taskType) {
            "ContextManifest task_type 与加载的 PC 策略不一致"
        }
        val requestFingerprint = inputs.request.fingerprint(inputs.projectId)
        val coverage = linkedMapOf<String, MobileContextCoverage>()
        val candidates = mutableListOf<MobileContextManifestItem>()
        val warnings = mutableListOf<String>()

        addStyle(inputs, candidates, coverage)
        when (policy.taskType) {
            "writing" -> {
                addTargetOutline(inputs, candidates, coverage)
                addTargetDraft(inputs, candidates, coverage)
            }
            "outline_planning" -> addOutlinePosition(inputs, candidates, coverage)
        }
        addRequirements(inputs, candidates, coverage)
        coverage["agent_selection"] = MobileContextCoverage(
            required = false,
            status = "pending",
            itemCount = 0,
            reason = "The task Agent has not finalized task-specific evidence yet.",
        )

        val window = max(1, inputs.contextWindowTokens ?: policy.contextWindowTokens)
        val ratioLimit = (window * policy.outputRatio).toInt()
        val configuredLimit = inputs.maxOutputTokens ?: ratioLimit
        val outputReserve = max(policy.minimumOutputReserveTokens, min(configuredLimit, ratioLimit))
        val inputBudget = max(0, window - outputReserve - policy.safetyMarginTokens)
        val selected = budget(candidates, coverage, warnings, inputBudget)
        val requiredCategories = policy.requiredCategories + coverage
            .filterValues { item -> item.required }
            .keys
        val missingRequired = requiredCategories.filter { category ->
            coverage[category]?.status !in setOf("covered", "not_applicable")
        }
        val status = if (missingRequired.isEmpty()) "ready" else "needs_confirmation"
        if (missingRequired.isNotEmpty()) {
            warnings += "Required context is missing: ${missingRequired.joinToString(", ")}"
        }
        warnings += "手机独立模式只自动提供任务锚点、文风和作者要求；其余资料由模型通过本地词法检索选择。"

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
            softInputTargetTokens = min(policy.softInputTargetTokens, inputBudget),
            outputReserveTokens = outputReserve,
            safetyMarginTokens = policy.safetyMarginTokens,
            items = selected,
            coverage = coverage.toMap(),
            warnings = warnings.distinct(),
        )
    }

    fun validate(existing: MobileContextManifest, inputs: MobileContextInputs): MobileContextValidation {
        val current = prepare(inputs, id = existing.id)
        val currentAnchors = current.items.associateBy(MobileContextManifestItem::identity)
        val changedSelectedSource = existing.items
            .filter { it.category == "agent_selected" }
            .firstOrNull { selected ->
                exactCandidate(inputs, selected.sourceType, selected.sourceId.orEmpty())
                    ?.sourceHash != selected.sourceHash
            }
        val changedAnchor = existing.items
            .filter { it.category !in setOf("agent_search", "agent_selected") }
            .firstOrNull { anchor -> currentAnchors[anchor.identity()]?.sourceHash != anchor.sourceHash }
        val staleReason = when {
            existing.policyVersion != policy.policyVersion ||
                existing.indexVersion != policy.indexVersion ||
                existing.policySourceHash != policy.sourceHash -> "PC 上下文策略版本已变化，请重新预检。"
            existing.model != inputs.model -> "模型已从 ${existing.model} 切换为 ${inputs.model}，请重新预检。"
            existing.requestFingerprint != current.requestFingerprint -> "任务目标、位置或作者要求已变化，请重新建立精简基线。"
            changedAnchor != null -> "必选上下文来源发生变化：${changedAnchor.title}。"
            changedSelectedSource != null -> "已选择的上下文来源发生变化：${changedSelectedSource.title}。"
            current.status != "ready" -> "当前必选上下文不完整，需要作者确认。"
            else -> ""
        }
        return if (staleReason.isBlank()) {
            MobileContextValidation("ready", "ContextManifest 仍然有效。", existing)
        } else if (current.status == "needs_confirmation" && existing.requestFingerprint == current.requestFingerprint) {
            MobileContextValidation("needs_confirmation", staleReason, current.copy(status = "needs_confirmation"))
        } else {
            MobileContextValidation(
                "stale",
                staleReason,
                current.copy(status = "stale", selectionToken = null, contextDelivery = null),
            )
        }
    }

    fun search(
        existing: MobileContextManifest,
        inputs: MobileContextInputs,
        query: String,
        sourceTypes: Set<String> = emptySet(),
        limit: Int = 10,
        cursor: Int = 0,
    ): MobileContextSearch {
        val pageLimit = limit.coerceIn(1, 10)
        val pageCursor = cursor.coerceIn(0, 20)
        val allCandidates = queryCandidates(inputs, query, sourceTypes)
        val candidates = allCandidates
            .drop(pageCursor)
            .take(pageLimit)
        val retained = existing.items.filter { it.category != "agent_selected" }
        val merged = (retained + candidates)
            .distinctBy { listOf(it.category, it.sourceType, it.sourceId.orEmpty(), it.sourceHash).joinToString("\u001f") }
        val coverage = existing.coverage.toMutableMap().apply {
            this["agent_selection"] = MobileContextCoverage(
                false,
                "pending",
                0,
                "Retrieved candidates must be reviewed and finalized by the task Agent.",
            )
        }
        val cleared = existing.copy(
            items = merged,
            coverage = coverage,
            selectionToken = null,
            contextDelivery = null,
            selectionFingerprint = fingerprint(merged.filter { it.category != "agent_search" }),
        )
        val nextCursor = (pageCursor + candidates.size).takeIf { next ->
            candidates.size == pageLimit && next <= 20 && next < allCandidates.size
        }
        return MobileContextSearch(
            manifest = cleared,
            items = candidates,
            cursor = pageCursor,
            limit = pageLimit,
            nextCursor = nextCursor,
            hasMore = nextCursor != null,
        )
    }

    fun select(
        existing: MobileContextManifest,
        inputs: MobileContextInputs,
        itemIds: List<String>,
        referenceRejections: List<String> = emptyList(),
    ): MobileContextSelection {
        val baseAndSearch = existing.items.filter { it.category != "agent_selected" }
        val cleared = existing.copy(
            items = baseAndSearch,
            selectionToken = null,
            contextDelivery = null,
            selectionFingerprint = fingerprint(baseAndSearch.filter { it.category != "agent_search" }),
        )
        if (referenceRejections.isNotEmpty()) {
            return MobileContextSelection(cleared, emptyList(), referenceRejections.distinct())
        }
        val candidates = baseAndSearch
            .filter { it.category == "agent_search" }
            .associateBy(MobileContextManifestItem::itemId)
        val rejected = mutableListOf<String>()
        val selected = itemIds.distinct().mapNotNull { itemId ->
            val candidate = candidates[itemId]
            if (candidate == null) {
                rejected += "候选 $itemId 不属于当前检索结果。"
                return@mapNotNull null
            }
            val exact = exactCandidate(inputs, candidate.sourceType, candidate.sourceId.orEmpty())
            if (exact == null || exact.sourceHash != candidate.sourceHash) {
                rejected += "来源 ${candidate.title} 已变化或无法精确读取，请重新检索。"
                return@mapNotNull null
            }
            exact.copy(
                category = "agent_selected",
                tier = 3,
                selectionReason = "Exact source selected by the task Agent after retrieval review.",
            )
        }.distinctBy { it.identity() }

        var usedTokens = baseAndSearch
            .filter { it.category != "agent_search" }
            .sumOf(MobileContextManifestItem::estimatedTokens)
        selected.forEach { item ->
            if (usedTokens + item.estimatedTokens > existing.inputBudgetTokens) {
                rejected += "来源 ${item.title} 会挤占模型预留的输出空间，请缩减资料或输出预留。"
            } else {
                usedTokens += item.estimatedTokens
            }
        }
        if (rejected.isNotEmpty()) {
            return MobileContextSelection(cleared, emptyList(), rejected.distinct())
        }

        val token = UUID.randomUUID().toString()
        val finalItems = baseAndSearch + selected
        val coverage = existing.coverage.toMutableMap().apply {
            this["agent_selection"] = MobileContextCoverage(
                false,
                "covered",
                selected.size,
                "Exact sources were selected by the task Agent after retrieval review.",
            )
        }
        val softWarningPrefix = "任务上下文超过软目标："
        val warnings = existing.warnings
            .filterNot { it.startsWith(softWarningPrefix) }
            .toMutableList()
        if (usedTokens > existing.softInputTargetTokens) {
            warnings += "$softWarningPrefix$usedTokens/${existing.softInputTargetTokens} token；模型已明确选择，可继续生成。"
        }
        val finalized = existing.copy(
            items = finalItems,
            coverage = coverage,
            warnings = warnings.distinct(),
            selectionToken = token,
            contextDelivery = null,
            selectionFingerprint = fingerprint(finalItems.filter { it.category != "agent_search" }),
        )
        return MobileContextSelection(finalized, selected, emptyList())
    }

    private fun fingerprint(items: List<MobileContextManifestItem>): String = mobileSha256(
        items.joinToString("\u001e") { item ->
            listOf(item.category, item.sourceType, item.sourceId.orEmpty(), item.chunkId.orEmpty(), item.sourceHash)
                .joinToString("\u001f")
        },
    )

    private fun queryCandidates(
        inputs: MobileContextInputs,
        query: String,
        sourceTypes: Set<String>,
    ): List<MobileContextManifestItem> {
        val normalizedTypes = sourceTypes.map(String::trim).filter(String::isNotBlank).toSet()
        val selectedTypes = if (normalizedTypes.isEmpty()) {
            policy.searchSourceTypes
        } else {
            normalizedTypes intersect policy.searchSourceTypes
        }
        if (selectedTypes.isEmpty()) return emptyList()
        fun allows(type: String): Boolean = type in selectedTypes
        val raw = mutableListOf<MobileContextManifestItem>()

        if (allows("character")) {
            inputs.rawRecords
                .filter { it.mobileRecordType() == "character" }
                .distinctBy { it.stringValue("id") }
                .mapNotNullTo(raw) { exactCandidate(inputs, "character", it.stringValue("id")) }
        }
        if (allows("character_timeline")) {
            inputs.rawRecords
                .filter { it.mobileRecordType() == "character_timeline" }
                .map { it.stringValue("character_id") }
                .filter(String::isNotBlank)
                .distinct()
                .mapNotNullTo(raw) { exactCandidate(inputs, "character_timeline", it) }
        }
        if (allows("worldbuilding")) {
            inputs.rawRecords
                .filter {
                    it.mobileRecordType() == "world_entry" &&
                        it.isCurrentWorldbuildingEntry()
                }
                .distinctBy { it.stringValue("id") }
                .mapNotNullTo(raw) { exactCandidate(inputs, "worldbuilding", it.stringValue("id")) }
        }
        if (allows("outline")) {
            inputs.rawRecords
                .filter { it.mobileRecordType() == "outline_node" }
                .distinctBy { it.stringValue("id") }
                .mapNotNullTo(raw) { exactCandidate(inputs, "outline", it.stringValue("id")) }
        }
        if (allows("chapter_summary")) {
            inputs.rawRecords
                .filter { it.mobileRecordType() == "chapter" && it.stringValue("summary").isNotBlank() }
                .distinctBy { it.stringValue("id") }
                .mapNotNullTo(raw) { exactCandidate(inputs, "chapter_summary", it.stringValue("id")) }
        }
        if (allows("chapter")) {
            inputs.rawRecords
                .filter { it.mobileRecordType() == "chapter" && it.stringValue("content").isNotBlank() }
                .distinctBy { it.stringValue("id") }
                .mapNotNullTo(raw) { exactCandidate(inputs, "chapter", it.stringValue("id")) }
        }
        if (allows("assistant_memory")) {
            inputs.rawRecords
                .filter { it.mobileRecordType() == "assistant_memory" }
                .distinctBy { it.stringValue("id") }
                .mapNotNullTo(raw) { exactCandidate(inputs, "assistant_memory", it.stringValue("id")) }
        }
        if (allows("narrative_governance")) {
            exactCandidate(inputs, "narrative_governance", inputs.projectId)?.let(raw::add)
        }

        val queryTokens = lexicalTokens(query)
        if (queryTokens.isEmpty()) return emptyList()
        val scored = raw.map { candidate ->
            candidate to lexicalOverlap(
                queryTokens,
                lexicalTokens(listOf(candidate.title, candidate.content).joinToString("\n")),
            )
        }.filter { (_, score) -> score > 0.0 }
        val maxScore = scored.maxOfOrNull { it.second } ?: return emptyList()
        return scored.map { (candidate, score) ->
            val lexical = score / maxScore
            candidate.copy(
                lexicalScore = lexical,
                finalScore = lexical,
                selectionReason = "Local lexical retrieval candidate chosen by the model's query.",
            )
        }.sortedWith(
            compareByDescending<MobileContextManifestItem> { it.finalScore }
                .thenBy { it.title },
        )
    }

    private fun exactCandidate(
        inputs: MobileContextInputs,
        sourceType: String,
        sourceId: String,
    ): MobileContextManifestItem? {
        val row = inputs.rawRecords.firstOrNull { value ->
            value.stringValue("id") == sourceId && when (sourceType) {
                "character" -> value.mobileRecordType() == "character"
                "worldbuilding" ->
                    value.mobileRecordType() == "world_entry" &&
                        value.isCurrentWorldbuildingEntry()
                "outline" -> value.mobileRecordType() == "outline_node"
                "chapter", "chapter_summary" -> value.mobileRecordType() == "chapter"
                "assistant_memory" -> value.mobileRecordType() == "assistant_memory"
                else -> false
            }
        }
        val (title, content) = when (sourceType) {
            "character" -> {
                if (row == null) return null
                val targetChapterNumber = inputs.rawRecords.firstOrNull {
                    it.mobileRecordType() == "outline_node" &&
                        it.stringValue("id") == inputs.request.outlineNodeId
                }?.intValue("sort_order", 0)?.takeIf { it > 0 }
                row.stringValue("name").ifBlank { "Character" } to
                    cleanMobileText(
                        pcExactCharacterArchive(
                            inputs.rawRecords,
                            row,
                            targetChapterNumber = targetChapterNumber,
                        ),
                    )
            }
            "character_timeline" -> {
                val events = inputs.rawRecords
                    .filter {
                        it.mobileRecordType() == "character_timeline" &&
                            it.stringValue("character_id") == sourceId
                    }
                    .sortedWith(
                        compareBy<JsonObject> { it.intValue("sort_order", 0) }
                            .thenBy { it.stringValue("created_at") },
                    )
                if (events.isEmpty()) return null
                val characterName = inputs.rawRecords.firstOrNull {
                    it.mobileRecordType() == "character" && it.stringValue("id") == sourceId
                }?.stringValue("name").orEmpty().ifBlank { sourceId.take(8) }
                "$characterName timeline" to cleanMobileText(
                    events.joinToString("\n") { event ->
                        buildString {
                            append('[').append(event.stringValue("event_type")).append("] ")
                            append(event.stringValue("event_description"))
                            event.stringValue("emotional_state_change").takeIf(String::isNotBlank)?.let {
                                append(" (emotional change: ").append(it).append(')')
                            }
                        }
                    },
                )
            }
            "worldbuilding" -> {
                if (row == null) return null
                row.stringValue("title").ifBlank { "Worldbuilding" } to buildString {
                    append("Worldbuilding: ").append(row.stringValue("title")).append('\n')
                    append("Dimension: ").append(row.stringValue("dimension")).append('\n')
                    append(cleanMobileText(row.stringValue("content")))
                }
            }
            "outline" -> {
                if (row == null) return null
                row.stringValue("title").ifBlank { "Outline" } to mobileOutlineText(row)
            }
            "chapter_summary" -> {
                if (row == null || row.stringValue("summary").isBlank()) return null
                row.stringValue("title").ifBlank { "Chapter summary" } to
                    "Chapter summary: ${row.stringValue("title")}\n${cleanMobileText(row.stringValue("summary"))}"
            }
            "chapter" -> {
                if (row == null || row.stringValue("content").isBlank()) return null
                row.stringValue("title").ifBlank { "Chapter" } to buildString {
                    append("Chapter: ").append(row.stringValue("title"))
                    row.stringValue("summary").takeIf(String::isNotBlank)?.let { summary ->
                        append("\nSummary: ").append(cleanMobileText(summary))
                    }
                    append("\nText:\n").append(cleanMobileText(row.stringValue("content")))
                }
            }
            "assistant_memory" -> {
                if (row == null) return null
                row.stringValue("key").ifBlank { "Memory" } to cleanMobileText(
                    row.stringValue("value"),
                )
            }
            "narrative_governance" -> "Narrative governance ledger" to cleanMobileText(
                pcGovernanceContext(inputs.rawRecords, limit = null).ifBlank {
                    "Narrative governance: no due or high-risk items."
                },
            )
            else -> return null
        }
        if (content.isBlank()) return null
        return item(
            category = "agent_search",
            sourceType = sourceType,
            sourceId = sourceId,
            title = title,
            content = content,
            required = false,
            score = 0.0,
            reason = "Exact local source available for model-driven retrieval.",
            sourceHash = mobileSha256(content),
        )
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
        val requestedTarget = inputs.request.outlineNodeId.takeIf(String::isNotBlank)?.let { id ->
            inputs.rawRecords.firstOrNull { it.mobileRecordType() == "outline_node" && it.stringValue("id") == id }
                ?: inputs.primaryRecords.firstOrNull {
                    it.mobileRecordType() == "outline_node" && it.stringValue("id") == id
                }
        }
        val target = requestedTarget?.takeIf { it.stringValue("node_type") == "chapter" }
        if (target == null) {
            val reason = when {
                inputs.request.outlineNodeId.isBlank() -> "Writing needs a chapter-level outline target."
                requestedTarget == null -> "The requested outline node no longer exists."
                else -> "The writing target must be a chapter node, not a volume or section."
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

    private fun addTargetDraft(
        inputs: MobileContextInputs,
        candidates: MutableList<MobileContextManifestItem>,
        coverage: MutableMap<String, MobileContextCoverage>,
    ) {
        val sourceDraftId = inputs.request.sourceDraftId.trim()
        if (sourceDraftId.isBlank()) return
        val draft = inputs.sourceDraft
        if (
            draft == null ||
            draft.id != sourceDraftId ||
            draft.projectId != inputs.projectId ||
            draft.state != MobileChapterWriteState.GENERATED ||
            draft.manifest.request.outlineNodeId != inputs.request.outlineNodeId
        ) {
            coverage["target_draft"] = MobileContextCoverage(
                true,
                "missing",
                0,
                "The selected pending chapter draft is unavailable or has a different outline.",
            )
            return
        }
        val content = buildJsonObject {
            put("title", draft.title)
            put("outline_node_id", draft.manifest.request.outlineNodeId)
            put("content", draft.content)
        }.toString()
        candidates += item(
            category = "target_draft",
            sourceType = "chapter_draft",
            sourceId = draft.id,
            title = draft.title.ifBlank { "Current unsaved chapter draft" },
            content = content,
            required = true,
            score = 1.0,
            reason = "Exact pending draft selected by the Agent for revision.",
            sourceHash = mobileSha256(content),
        )
        coverage["target_draft"] = MobileContextCoverage(true, "covered", 1)
    }

    private fun addOutlinePosition(
        inputs: MobileContextInputs,
        candidates: MutableList<MobileContextManifestItem>,
        coverage: MutableMap<String, MobileContextCoverage>,
    ) {
        fun outline(id: String): JsonObject? = inputs.rawRecords.firstOrNull {
            it.mobileRecordType() == "outline_node" && it.stringValue("id") == id
        } ?: inputs.primaryRecords.firstOrNull {
            it.mobileRecordType() == "outline_node" && it.stringValue("id") == id
        }

        val requestedParentId = inputs.request.parentId.trim()
        val requestedInsertAfterId = inputs.request.insertAfterId.trim()
        val parent = requestedParentId.takeIf(String::isNotBlank)?.let(::outline)
        if (requestedParentId.isNotBlank() && parent == null) {
            coverage["outline_position"] = MobileContextCoverage(
                true,
                "missing",
                0,
                "The requested outline parent does not exist in this project.",
            )
            return
        }
        val insertAfter = requestedInsertAfterId.takeIf(String::isNotBlank)?.let(::outline)
        if (requestedInsertAfterId.isNotBlank() && insertAfter == null) {
            coverage["outline_position"] = MobileContextCoverage(
                true,
                "missing",
                0,
                "The requested insertion anchor does not exist in this project.",
            )
            return
        }
        val resolvedParentId = requestedParentId.ifBlank {
            insertAfter?.stringValue("parent_id").orEmpty()
        }
        if (
            insertAfter != null &&
            insertAfter.stringValue("parent_id") != resolvedParentId
        ) {
            coverage["outline_position"] = MobileContextCoverage(
                true,
                "missing",
                0,
                "The insertion anchor is not a child of the requested parent.",
            )
            return
        }

        if (parent != null) {
            candidates += item(
                category = "outline_parent",
                sourceType = "outline",
                sourceId = parent.stringValue("id"),
                title = parent.stringValue("title").ifBlank { "Outline parent" },
                content = mobileOutlineText(
                    parent,
                    policy.categories["outline_parent"]?.fieldLimitChars ?: 1_200,
                ),
                required = true,
                score = 1.0,
                reason = "Author-selected parent for the proposed outline nodes.",
                sourceHash = mobileSha256(canonicalMobileJson(parent)),
            )
            coverage["outline_parent"] = MobileContextCoverage(true, "covered", 1)
        } else {
            coverage["outline_parent"] = MobileContextCoverage(false, "not_applicable", 0)
        }

        val position = buildJsonObject {
            if (resolvedParentId.isBlank()) put("parent_id", JsonNull)
            else put("parent_id", resolvedParentId)
            if (requestedInsertAfterId.isBlank()) put("insert_after_id", JsonNull)
            else put("insert_after_id", requestedInsertAfterId)
            put(
                "batch_count",
                inputs.request.batchCount.coerceIn(1, OUTLINE_PROPOSAL_MAX_NODES),
            )
        }.toString()
        candidates += item(
            category = "outline_position",
            sourceType = "inline",
            sourceId = "outline-position",
            title = "Author-selected outline insertion position",
            content = position,
            required = true,
            score = 1.0,
            reason = "Exact parent and insertion anchor for this outline proposal.",
        )
        coverage["outline_position"] = MobileContextCoverage(true, "covered", 1)
    }

    private fun addRequirements(
        inputs: MobileContextInputs,
        candidates: MutableList<MobileContextManifestItem>,
        coverage: MutableMap<String, MobileContextCoverage>,
    ) {
        val text = listOfNotNull(
            inputs.request.requirements.trim().ifBlank { null },
            inputs.request.minimumHanCharacters?.let {
                "Hard structured length constraint: the chapter body must contain at least $it Han characters. The draft boundary counts and enforces it."
            },
        ).joinToString("\n")
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

private fun mobileOutlineText(node: JsonObject, fieldLimit: Int? = null): String = buildList {
    add("Outline: ${node.stringValue("title")}")
    add("Node type: ${node.stringValue("node_type").ifBlank { "unknown" }}")
    listOf(
        "Summary" to node.stringValue("summary"),
        "Planned" to node.stringValue("planned_summary"),
        "Actual" to node.stringValue("actual_summary"),
        "Status" to node.stringValue("status"),
    ).forEach { (label, value) -> if (value.isNotBlank()) add("$label: ${cleanMobileText(value, fieldLimit)}") }
}.joinToString("\n")

private fun cleanMobileText(value: String, limit: Int? = null): String {
    val text = value.trim()
    if (limit == null) return text
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

internal fun canonicalMobileJson(element: JsonElement): String = when (element) {
    is JsonObject -> element.entries.sortedBy { it.key }
        .joinToString(prefix = "{", postfix = "}", separator = ",") { (key, value) ->
            JsonPrimitive(key).toString() + ":" + canonicalMobileJson(value)
        }
    is JsonArray -> element.joinToString(prefix = "[", postfix = "]", separator = ",", transform = ::canonicalMobileJson)
    else -> element.toString()
}

internal fun JsonObject.mobileRecordType(): String = stringValue("_record_type")

internal fun JsonObject.isCurrentWorldbuildingEntry(): Boolean {
    val status = stringValue("status").trim()
    return status.isBlank() || status.equals("active", ignoreCase = true)
}

private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())

internal fun JsonObject.stringValue(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()

private fun JsonObject.intValue(name: String, fallback: Int): Int = (get(name) as? JsonPrimitive)?.intOrNull ?: fallback

private fun JsonObject.optionalInt(name: String): Int? = (get(name) as? JsonPrimitive)?.intOrNull

private fun JsonObject.doubleValue(name: String, fallback: Double): Double =
    (get(name) as? JsonPrimitive)?.doubleOrNull ?: fallback

private fun JsonObject.optionalDouble(name: String): Double? =
    (get(name) as? JsonPrimitive)?.doubleOrNull

private fun JsonObject.booleanValue(name: String): Boolean = (get(name) as? JsonPrimitive)?.booleanOrNull ?: false

private fun JsonObject.stringList(name: String): List<String> = when (val value = get(name)) {
    is JsonArray -> value.mapNotNull { (it as? JsonPrimitive)?.contentOrNull?.trim() }.filter(String::isNotBlank)
    is JsonPrimitive -> value.contentOrNull.orEmpty().split(',', '，').map(String::trim).filter(String::isNotBlank)
    else -> emptyList()
}
