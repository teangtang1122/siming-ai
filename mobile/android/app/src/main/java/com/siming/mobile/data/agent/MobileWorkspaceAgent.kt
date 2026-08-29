package com.siming.mobile.data.agent

import android.content.Context
import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.local.orderReplicaEntities
import com.siming.mobile.data.local.primaryAuthoringSnapshot
import com.siming.mobile.data.network.DirectAgentTurn
import com.siming.mobile.data.network.DirectAgentToolCall
import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.data.network.DirectApiConfig
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.put

/**
 * Standalone Android implementation of the desktop workspace-assistant loop.
 *
 * The system prompt, nested writer prompts, and tool schemas are loaded from a
 * build-generated projection of the PC sources. Only the storage adapter is
 * mobile-specific: tool writes target the local replica/outbox when no Gateway
 * is connected.
 */
internal class MobileWorkspaceAgent(
    context: Context,
    private val directApi: DirectApiClient,
    private val loadSnapshot: suspend (String) -> List<ReplicaEntity>,
    private val saveEntity: suspend (String, String, String, JsonObject) -> String,
) {
    private val contract = PcPromptContract(context.applicationContext)
    private val contextPolicies = PcContextManifestPolicy(context.applicationContext)
    private val chapterWriteStore = MobileChapterWriteStore(context.applicationContext)
    private val outlineDraftStore = MobileOutlineDraftStore(context.applicationContext)
    private val json = Json { ignoreUnknownKeys = true }
    private val contextManifests = LinkedHashMap<String, MobileContextManifest>()

    suspend fun pendingChapterDraft(projectId: String): JsonObject? {
        val run = chapterWriteStore.latestGenerated(projectId) ?: return null
        return buildJsonObject {
            put("draft_id", run.id)
            put("project_id", run.projectId)
            put("content_ref", run.id)
            put("title", run.title)
            put("outline_node_id", run.manifest.request.outlineNodeId)
            put("context_manifest_id", run.manifest.id)
            put("draft_status", "pending")
            put("content", run.content)
            put("word_count", countWords(run.content))
            put("execution_route", "android_standalone")
            put("next_actions", buildJsonArray {
                add(JsonPrimitive("save_only"))
                add(JsonPrimitive("save_and_catalog"))
            })
        }
    }

    suspend fun markChapterDraftSaved(draftId: String) {
        chapterWriteStore.markSaved(draftId)
    }

    suspend fun pendingOutlineDraft(projectId: String): JsonObject? =
        outlineDraftStore.latestPending(projectId)?.let(::outlineDraftData)

    suspend fun updateOutlineDraft(
        draftId: String,
        nodes: JsonArray,
        designNotes: String,
    ): JsonObject? {
        val draft = outlineDraftStore.load(draftId) ?: return null
        if (draft.state != MobileOutlineDraftState.PENDING || nodes.isEmpty()) return null
        return outlineDraftData(
            outlineDraftStore.save(
                draft.copy(nodes = nodes, designNotes = designNotes),
            ),
        )
    }

    suspend fun markOutlineDraftConfirmed(draftId: String, savedIds: List<String>): JsonObject? =
        outlineDraftStore.markConfirmed(draftId, savedIds)?.let(::outlineDraftData)

    suspend fun discardOutlineDraft(draftId: String): JsonObject? =
        outlineDraftStore.markDiscarded(draftId)?.let(::outlineDraftData)

    suspend fun supersedeOutlineDraft(draftId: String): JsonObject? =
        outlineDraftStore.markSuperseded(draftId)?.let(::outlineDraftData)

    suspend fun run(
        projectId: String,
        prompt: String,
        config: DirectApiConfig,
        history: List<JsonObject> = emptyList(),
        onEvent: suspend (String) -> Unit,
    ) {
        val initialRecords = records(projectId)
        val project = initialRecords.firstOrNull { it.entity.entityType == "project" }?.payload
            ?: error("当前作品副本不存在，无法启动手机独立工作区")
        val messages = mutableListOf(message("system", contract.workspaceSystem()))
        messages += history.filter { value -> value.string("role") in setOf("user", "assistant") }
        messages += message(
            "user",
            contract.initialUserMessage(project, prompt),
        )
        onEvent(event("status", "已加载 PC 提示词契约 ${contract.sourceHash.take(12)}，开始执行"))

        var iteration = 0
        var activeCategories = emptyList<String>()
        var categorySelected = false
        while (iteration < MAX_ITERATIONS) {
            val scopedTools = contract.toolSchemas(activeCategories)
            var streamedContent = false
            var streamedReasoning = false
            val turn = directApi.streamAgentTurn(
                config = config,
                messages = messages,
                tools = scopedTools,
                toolChoice = if (categorySelected) "auto" else "required",
                maxOutputTokens = 6_000,
                temperature = 0.3,
                onContentDelta = { delta ->
                    streamedContent = true
                    onEvent(event(type = "content_delta", delta = delta))
                },
                onReasoningDelta = { delta ->
                    streamedReasoning = true
                    onEvent(event(type = "reasoning_delta", delta = delta))
                },
            )
            if (!streamedReasoning && turn.reasoningContent.isNotBlank()) {
                onEvent(event(type = "reasoning_delta", delta = turn.reasoningContent))
            }
            if (turn.toolCalls.isEmpty()) {
                check(categorySelected) {
                    "模型没有调用本步骤唯一开放的 set_tool_categories，本轮未接受文字回复"
                }
                val content = turn.content.trim()
                require(content.isNotBlank()) { "模型既没有调用 PC 工具，也没有返回最终内容" }
                if (!streamedContent) onEvent(event("content_delta", delta = content))
                onEvent(event("done", "任务完成"))
                return
            }

            val categoryCall = turn.toolCalls.firstOrNull { it.name == contract.toolCategories.controller }
            if (categoryCall != null) {
                messages += assistantToolMessage(turn.content, turn.reasoningContent, listOf(categoryCall))
                val selected = runCatching {
                    contract.toolCategories.normalize(
                        (categoryCall.arguments["enabled_categories"] as? JsonArray)
                            .orEmpty()
                            .mapNotNull { (it as? JsonPrimitive)?.contentOrNull },
                    )
                }
                val categoryResult = selected.fold(
                    onSuccess = { contract.toolCategories.selectionResult(it, contract.toolNames) },
                    onFailure = { errorResult(categoryCall.name, it.message ?: "工具类别参数无效") },
                )
                messages += buildJsonObject {
                    put("role", "tool")
                    put("tool_call_id", categoryCall.id)
                    put("content", categoryResult.toString())
                }
                selected.getOrNull()?.let { categories ->
                    activeCategories = categories
                    categorySelected = true
                    onEvent(
                        event(
                            type = "tool_categories_changed",
                            detail = categoryResult.string("detail"),
                        ),
                    )
                }
                iteration += 1
                continue
            }

            messages += turn.assistantMessage
            val availableTools = contract.availableToolNames(activeCategories)

            for (call in turn.toolCalls) {
                val result = if (call.name in availableTools) {
                    try {
                        execute(projectId, call.name, call.arguments, config, onEvent)
                    } catch (error: CancellationException) {
                        throw error
                    } catch (error: Exception) {
                        errorResult(call.name, error.message ?: "工具执行失败")
                    }
                } else {
                    skipped(call.name, "手机提示词契约未开放该工具")
                }
                onEvent(
                    event(
                        type = "tool",
                        detail = result.string("detail").ifBlank { "已执行 ${call.name}" },
                    ),
                )
                messages += buildJsonObject {
                    put("role", "tool")
                    put("tool_call_id", call.id)
                    put("content", modelToolResult(result).toString())
                }
                if (call.name == "chapter_writer" && result.string("status") == "ok") {
                    onEvent(
                        event(
                            type = "chapter_draft",
                            detail = result.string("detail"),
                            data = result["data"],
                        ),
                    )
                    onEvent(event("done", "章节草稿已生成，本轮已停止"))
                    return
                }
                if (call.name == "chapter_writer" && result.string("status") == "blocked") {
                    pendingChapterDraft(projectId)?.let { draft ->
                        onEvent(
                            event(
                                type = "chapter_draft",
                                detail = result.string("detail"),
                                data = draft,
                            ),
                        )
                    }
                    onEvent(event("done", result.string("detail")))
                    return
                }
                if (call.name == "outline_writer" && result.string("status") == "ok") {
                    onEvent(
                        event(
                            type = "outline_draft",
                            detail = result.string("detail"),
                            data = result["data"],
                        ),
                    )
                    onEvent(event("done", "大纲草稿已生成，本轮已停止"))
                    return
                }
                if (call.name == "outline_writer" && result.string("status") == "blocked") {
                    pendingOutlineDraft(projectId)?.let { draft ->
                        onEvent(
                            event(
                                type = "outline_draft",
                                detail = result.string("detail"),
                                data = draft,
                            ),
                        )
                    }
                    onEvent(event("done", result.string("detail")))
                    return
                }
            }
            iteration += 1
            if (iteration == MAX_ITERATIONS) {
                error("手机独立工作区达到 $MAX_ITERATIONS 轮工具上限，任务已安全停止")
            }
        }
    }

    private suspend fun execute(
        projectId: String,
        tool: String,
        args: JsonObject,
        config: DirectApiConfig,
        onEvent: suspend (String) -> Unit,
    ): JsonObject = when (tool) {
        "get_project_info" -> getProjectInfo(projectId)
        "update_project_info" -> updateProjectInfo(projectId, args)
        "list_characters" -> listCharacters(projectId)
        "list_chapters" -> listChapters(projectId)
        "list_worldbuilding" -> listWorldbuilding(projectId)
        "search_characters" -> searchCharacters(projectId, args)
        "search_chapters" -> searchChapters(projectId, args)
        "search_outline" -> searchOutline(projectId, args)
        "search_outline_tree" -> searchOutlineTree(projectId, args)
        "search_worldbuilding" -> searchWorldbuilding(projectId, args)
        "prepare_task_context" -> prepareTaskContext(projectId, args, config)
        "search_task_context" -> searchTaskContext(projectId, args, config)
        "submit_context_evidence" -> submitContextEvidence(projectId, args, config)
        "chapter_writer" -> chapterWriter(
            projectId,
            args,
            config.forTask(DirectApiConfig.TASK_WRITING),
            onEvent,
        )
        "character_writer" -> characterWriter(
            projectId,
            args,
            config.forTask(DirectApiConfig.TASK_PLANNING),
        )
        "outline_writer" -> outlineWriter(
            projectId,
            args,
            config.forTask(DirectApiConfig.TASK_PLANNING),
        )
        "worldbuilding_writer" -> worldbuildingWriter(
            projectId,
            args,
            config.forTask(DirectApiConfig.TASK_PLANNING),
        )
        "create_character" -> createCharacter(projectId, args)
        "update_character" -> updateCharacter(projectId, args)
        "create_outline_node" -> createOutlineNode(projectId, args)
        "create_outline_nodes" -> createOutlineNodes(projectId, args)
        "update_outline_node" -> updateOutlineNode(projectId, args)
        "create_worldbuilding_entry" -> createWorldbuilding(projectId, args)
        "update_worldbuilding_entry" -> updateWorldbuilding(projectId, args)
        else -> skipped(tool, "未知工具")
    }

    private suspend fun getProjectInfo(projectId: String): JsonObject {
        val project = records(projectId).firstOrNull { it.entity.entityType == "project" }
            ?: return skipped("get_project_info", "未找到作品")
        return ok("get_project_info", "已读取作品：${project.payload.string("title")}", clean(project.payload))
    }

    private suspend fun updateProjectInfo(projectId: String, args: JsonObject): JsonObject {
        val target = args.string("id").ifBlank { args.string("project_id") }.ifBlank { projectId }
        if (target != projectId) return skipped("update_project_info", "手机独立模式只能修改当前作品")
        val current = records(projectId).firstOrNull { it.entity.entityType == "project" }
            ?: return skipped("update_project_info", "未找到作品")
        val payload = mergeRecord(
            current.payload,
            args,
            "project",
            projectId,
            projectId,
            excluded = LOCATOR_FIELDS,
        )
        saveEntity(projectId, "project", projectId, payload)
        return ok("update_project_info", "已更新作品：${payload.string("title")}", clean(payload))
    }

    private suspend fun listCharacters(projectId: String): JsonObject {
        val items = records(projectId, "character")
            .sortedBy { it.payload.string("name") }
            .take(100)
            .map { item -> select(item.payload, "id", "name", "role_type") }
        return ok("list_characters", if (items.isEmpty()) "该项目暂无角色" else "共 ${items.size} 个角色", JsonArray(items))
    }

    private suspend fun listChapters(projectId: String): JsonObject {
        val items = records(projectId, "chapter")
            .take(500)
            .map { item -> select(item.payload, "id", "title", "outline_node_id") }
        return ok("list_chapters", if (items.isEmpty()) "该项目暂无章节" else "共 ${items.size} 个章节", JsonArray(items))
    }

    private suspend fun listWorldbuilding(projectId: String): JsonObject {
        val items = records(projectId, "world")
            .sortedWith(compareBy<LocalRecord> { it.payload.string("dimension") }.thenBy { it.payload.int("sort_order") })
            .take(200)
            .map { item -> select(item.payload, "id", "title", "dimension") }
        return ok(
            "list_worldbuilding",
            if (items.isEmpty()) "该项目暂无世界观条目" else "共 ${items.size} 个世界观条目",
            JsonArray(items),
        )
    }

    private suspend fun searchCharacters(projectId: String, args: JsonObject): JsonObject {
        val query = args.string("query")
        val limit = args.limit(10, 30)
        val items = records(projectId, "character")
            .filter { query.isBlank() || it.payload.string("name").contains(query, ignoreCase = true) }
            .sortedBy { it.payload.string("name") }
            .take(limit)
            .map { item ->
                select(
                    item.payload,
                    "id", "name", "role_type", "appearance", "personality", "background", "abilities",
                    "life_status", "current_location", "realm_or_level", "physical_state", "mental_state",
                    "current_goal", "active_conflict", "abilities_state", "items_or_assets", "profile",
                )
            }
        val detail = if (items.isEmpty()) {
            if (query.isBlank()) "该项目暂无角色" else "未找到匹配「$query」的角色"
        } else {
            "找到 ${items.size} 个角色" + if (query.isBlank()) "" else "（搜索「$query」）"
        }
        return ok("search_characters", detail, JsonArray(items))
    }

    private suspend fun searchChapters(projectId: String, args: JsonObject): JsonObject {
        val query = args.string("query")
        val outlineId = args.string("outline_node_id")
        val items = records(projectId, "chapter")
            .filter {
                if (outlineId.isNotBlank()) it.payload.string("outline_node_id") == outlineId
                else query.isBlank() || it.payload.string("title").contains(query, ignoreCase = true)
            }
            .take(args.limit(5, 20))
            .map { item ->
                val payload = item.payload
                buildJsonObject {
                    select(
                        payload,
                        "id", "title", "outline_node_id", "word_count", "summary",
                        "quality_score", "quality_detail", "quality_evaluated_at",
                    ).forEach { (key, value) -> put(key, value) }
                    put("content", payload.string("content").take(8_000))
                }
            }
        return ok("search_chapters", if (items.isEmpty()) "未找到匹配章节" else "找到 ${items.size} 个章节", JsonArray(items))
    }

    private suspend fun searchOutline(projectId: String, args: JsonObject): JsonObject {
        val all = records(projectId, "outline")
        val nodeId = args.string("node_id")
        if (nodeId.isNotBlank()) {
            val node = all.firstOrNull { it.entity.entityId == nodeId }
                ?: return ok("search_outline", "未找到大纲节点 $nodeId", JsonArray(emptyList()))
            val children = all.filter { it.payload.string("parent_id") == nodeId }
                .sortedBy { it.payload.int("sort_order") }
                .map { select(it.payload, "id", "node_type", "title", "summary", "status") }
            val result = buildJsonObject {
                outlinePayload(node.payload).forEach { (key, value) -> put(key, value) }
                put("children", JsonArray(children))
            }
            return ok("search_outline", "大纲节点 ${node.payload.string("title")}，${children.size} 个子节点", JsonArray(listOf(result)))
        }
        val query = args.string("query")
        val items = all
            .filter { query.isBlank() || it.payload.string("title").contains(query, ignoreCase = true) }
            .sortedBy { it.payload.int("sort_order") }
            .take(args.limit(10, 60))
            .map { outlinePayload(it.payload) }
        val detail = if (items.isEmpty()) {
            if (query.isBlank()) "该项目暂无大纲" else "未找到匹配「$query」的大纲节点"
        } else {
            "找到 ${items.size} 个大纲节点" + if (query.isBlank()) "" else "（搜索「$query」）"
        }
        return ok("search_outline", detail, JsonArray(items))
    }

    private suspend fun searchOutlineTree(projectId: String, args: JsonObject): JsonObject {
        val all = records(projectId, "outline")
        if (all.isEmpty()) return ok("search_outline_tree", "该项目暂无大纲", JsonArray(emptyList()))
        val rootId = args.string("root_id")
        val parentId = if (rootId.isBlank()) "" else rootId
        if (rootId.isNotBlank() && all.none { it.entity.entityId == rootId }) {
            return skipped("search_outline_tree", "未找到大纲节点 $rootId", JsonArray(emptyList()))
        }
        val tree = outlineTree(all, parentId, emptySet())
        val label = if (rootId.isBlank()) "完整大纲树：${all.size} 个节点" else "大纲子树：${countTree(tree)} 个节点"
        return ok("search_outline_tree", label, tree)
    }

    private suspend fun searchWorldbuilding(projectId: String, args: JsonObject): JsonObject {
        val query = args.string("query")
        val dimension = args.string("dimension")
        val items = records(projectId, "world")
            .filter {
                (query.isBlank() || it.payload.string("title").contains(query, ignoreCase = true)) &&
                    (dimension.isBlank() || it.payload.string("dimension") == dimension)
            }
            .sortedBy { it.payload.int("sort_order") }
            .take(args.limit(10, 30))
            .map { item ->
                select(
                    item.payload,
                    "id", "dimension", "title", "content", "sort_order", "status", "confidence",
                    "first_seen_chapter_id", "last_updated_chapter_id", "plot_usage", "constraints",
                )
            }
        return ok(
            "search_worldbuilding",
            if (items.isEmpty()) "未找到匹配的世界观条目" else "找到 ${items.size} 个世界观条目",
            JsonArray(items),
        )
    }

    private suspend fun prepareTaskContext(
        projectId: String,
        args: JsonObject,
        config: DirectApiConfig,
    ): JsonObject {
        val all = records(projectId)
        val rawPayloads = rawRecords(projectId).map(LocalRecord::payload)
        val project = all.firstOrNull { it.entity.entityType == "project" }?.payload
            ?: return skipped("prepare_task_context", "项目不存在", JsonObject(emptyMap()))
        val taskType = args.string("task_type").ifBlank { "writing" }
        if (taskType !in setOf("writing", "outline_planning")) {
            return skipped("prepare_task_context", "手机独立模式不支持该上下文任务：$taskType")
        }
        val taskArguments = args["arguments"] as? JsonObject ?: args
        val request = MobileContextRequest.fromArgs(taskType, taskArguments)
        val taskConfig = contextTaskConfig(config, taskType)
        val inputs = manifestInputs(projectId, taskConfig.model, request, project, all, rawPayloads)
        val manifest = contextEngine(taskType).prepare(inputs)
        cacheManifest(manifest)
        val data = buildJsonObject {
            put("manifest_id", manifest.id)
            put("context_manifest_id", manifest.id)
            put("context_manifest", manifest.toJson(includeContent = false))
            put("baseline_context", manifest.renderedContext())
            put("selection_required", true)
            put("next_tools", buildJsonArray {
                add(JsonPrimitive("search_task_context"))
                add(JsonPrimitive("submit_context_evidence"))
            })
        }
        val taskLabel = if (taskType == "writing") "写章" else "大纲规划"
        val detail = if (manifest.status == "ready") {
            "已建立精简$taskLabel 基线；请由模型检索并复核本任务需要的资料"
        } else {
            "$taskLabel 基线缺少必选位置、目标或文风锚点"
        }
        return result("prepare_task_context", manifest.status, detail, data)
    }

    private suspend fun searchTaskContext(
        projectId: String,
        args: JsonObject,
        config: DirectApiConfig,
    ): JsonObject {
        val manifestId = args.string("context_manifest_id").ifBlank { args.string("manifest_id") }
        val manifest = contextManifests[manifestId]
            ?: return skipped("search_task_context", "context_manifest_id 不存在或已失效")
        val taskConfig = contextTaskConfig(config, manifest.request.taskType)
        val engine = contextEngine(manifest.request.taskType)
        val policy = contextPolicies.policy(manifest.request.taskType)
        val query = args.string("query").trim()
        if (query.isBlank()) return skipped("search_task_context", "query 不能为空")
        val all = records(projectId)
        val rawPayloads = rawRecords(projectId).map(LocalRecord::payload)
        val project = all.firstOrNull { it.entity.entityType == "project" }?.payload
            ?: return skipped("search_task_context", "项目不存在")
        val inputs = manifestInputs(projectId, taskConfig.model, manifest.request, project, all, rawPayloads)
        val validation = engine.validate(manifest, inputs)
        if (!validation.ready) {
            cacheManifest(validation.current)
            return result(
                "search_task_context",
                validation.status,
                validation.detail,
                buildJsonObject { put("manifest_id", manifest.id) },
            )
        }
        val sourceTypes = args.stringList("source_types").toSet()
        val searched = engine.search(
            validation.current,
            inputs,
            query,
            sourceTypes,
            args.int("limit").takeIf { it > 0 } ?: 12,
        )
        cacheManifest(searched.manifest)
        val items = searched.items.map { item ->
            buildJsonObject {
                item.toJson(includeContent = false).forEach { (key, value) -> put(key, value) }
                put("excerpt", item.content.take(policy.searchExcerptChars))
                put("estimated_chunk_tokens", item.estimatedTokens)
            }
        }
        return ok(
            "search_task_context",
            "本次模型查询返回 ${items.size} 个候选；这些资料尚未进入正文上下文",
            buildJsonObject {
                put("manifest_id", searched.manifest.id)
                put("items", JsonArray(items))
            },
        )
    }

    private suspend fun submitContextEvidence(
        projectId: String,
        args: JsonObject,
        config: DirectApiConfig,
    ): JsonObject {
        val manifestId = args.string("context_manifest_id").ifBlank { args.string("manifest_id") }
        val manifest = contextManifests[manifestId]
            ?: return skipped("submit_context_evidence", "context_manifest_id 不存在或已失效")
        val taskConfig = contextTaskConfig(config, manifest.request.taskType)
        val engine = contextEngine(manifest.request.taskType)
        val all = records(projectId)
        val rawPayloads = rawRecords(projectId).map(LocalRecord::payload)
        val project = all.firstOrNull { it.entity.entityType == "project" }?.payload
            ?: return skipped("submit_context_evidence", "项目不存在")
        val inputs = manifestInputs(projectId, taskConfig.model, manifest.request, project, all, rawPayloads)
        val validation = engine.validate(manifest, inputs)
        if (!validation.ready) {
            cacheManifest(validation.current)
            return result(
                "submit_context_evidence",
                validation.status,
                validation.detail,
                buildJsonObject { put("manifest_id", manifest.id) },
            )
        }
        val sources = (args["sources"] as? JsonArray).orEmpty()
            .mapNotNull { it as? JsonObject }
        val itemIds = sources.mapNotNull { source ->
            source.string("item_id").ifBlank { source.string("id") }.takeIf(String::isNotBlank)
        }
        val selection = engine.select(validation.current, inputs, itemIds)
        cacheManifest(selection.manifest)
        val data = buildJsonObject {
            put("manifest_id", selection.manifest.id)
            put("accepted_count", selection.accepted.size)
            put("accepted", JsonArray(selection.accepted.map { it.toJson(includeContent = false) }))
            put("rejected", JsonArray(selection.rejected.map(::JsonPrimitive)))
            put("selection_ready", selection.ready)
            if (selection.ready) {
                put("context_selection_token", selection.manifest.selectionToken.orEmpty())
                put("task_context", selection.manifest.renderedContext())
                put("estimated_input_tokens", selection.manifest.estimatedInputTokens)
                put("input_budget_tokens", selection.manifest.inputBudgetTokens)
                put("soft_target_tokens", selection.manifest.softInputTargetTokens)
                put(
                    "soft_target_exceeded",
                    selection.manifest.estimatedInputTokens > selection.manifest.softInputTargetTokens,
                )
                put("warnings", JsonArray(selection.manifest.warnings.map(::JsonPrimitive)))
            }
        }
        return if (selection.ready) {
            ok(
                "submit_context_evidence",
                "已复核并精确载入 ${selection.accepted.size} 个来源；请在下一模型步骤携带选择令牌执行任务",
                data,
            )
        } else {
            result(
                "submit_context_evidence",
                "needs_confirmation",
                "所选资料未通过精确读取或模型动态容量校验，请调整后重新提交",
                data,
            )
        }
    }

    private suspend fun chapterWriter(
        projectId: String,
        args: JsonObject,
        config: DirectApiConfig,
        onEvent: suspend (String) -> Unit,
    ): JsonObject {
        val all = records(projectId)
        val rawPayloads = rawRecords(projectId).map(LocalRecord::payload)
        val project = all.firstOrNull { it.entity.entityType == "project" }?.payload
            ?: return skipped("chapter_writer", "项目不存在", JsonObject(emptyMap()))
        val manifestId = args.string("context_manifest_id")
        val selectionToken = args.string("context_selection_token")
        val requestedOutlineId = args.string("outline_node_id")
        val cachedManifest = contextManifests[manifestId]
            ?: return result(
                "chapter_writer",
                "needs_confirmation",
                "必须先建立精简基线，并让模型检索、复核本章资料",
                buildJsonObject { put("next_tool", "prepare_task_context") },
            )
        val request = cachedManifest.request
        if (request.taskType != "writing") {
            return result(
                "chapter_writer",
                "needs_confirmation",
                "context_manifest_id 不属于写章任务",
                buildJsonObject { put("next_tool", "prepare_task_context") },
            )
        }
        val engine = contextEngine("writing")
        if (requestedOutlineId != request.outlineNodeId) {
            return result(
                "chapter_writer",
                "needs_confirmation",
                "上下文清单目标与本次章级大纲不一致",
                buildJsonObject {
                    put("context_manifest_id", manifestId)
                    put("outline_node_id", requestedOutlineId)
                },
            )
        }
        val targetOutline = all.firstOrNull {
            it.entity.entityType == "outline" && it.entity.entityId == request.outlineNodeId
        }
        if (targetOutline == null || targetOutline.payload.string("node_type") != "chapter") {
            return skipped(
                "chapter_writer",
                "outline_node_id 必须是当前作品的章级节点，不能使用卷级或场景级节点",
            )
        }
        val chapterPayloads = all.asSequence()
            .filter { it.entity.entityType == "chapter" }
            .map(LocalRecord::payload)
            .toList()
        val pendingRun = chapterWriteStore.latestGenerated(projectId)
        val activePendingRun = if (pendingRun == null) {
            null
        } else {
            val pendingFormalChapterId = existingMobileChapterIdForOutline(
                chapterPayloads,
                pendingRun.manifest.request.outlineNodeId,
            )
            if (pendingFormalChapterId == null) {
                pendingRun
            } else {
                chapterWriteStore.markSuperseded(
                    pendingRun.id,
                    "对应大纲已关联正式章节；旧草稿已释放。",
                )
                null
            }
        }
        val existingChapterId = existingMobileChapterIdForOutline(
            chapterPayloads,
            request.outlineNodeId,
        )
        if (existingChapterId != null) {
            return skipped(
                "chapter_writer",
                "该章级大纲已关联正式章节；手机写作只生成独立的新章草稿，不能覆盖已有正文",
                buildJsonObject {
                    put("outline_node_id", request.outlineNodeId)
                    put("existing_chapter_id", existingChapterId)
                },
            )
        }
        if (activePendingRun != null) {
            return result(
                "chapter_writer",
                "blocked",
                "当前章节草稿尚未保存并完成建档，本轮未生成下一章。",
                buildJsonObject {
                    put("blocking_draft_id", activePendingRun.id)
                    put("outline_node_id", activePendingRun.manifest.request.outlineNodeId)
                    put("allowed_actions", buildJsonArray {
                        add(JsonPrimitive("save_and_catalog"))
                        add(JsonPrimitive("save_only"))
                    })
                },
            )
        }
        val inputs = manifestInputs(projectId, config.model, request, project, all, rawPayloads)
        val validation = engine.validate(cachedManifest, inputs)
        if (!validation.ready) {
            cacheManifest(validation.current)
            return result(
                "chapter_writer",
                validation.status,
                validation.detail,
                buildJsonObject {
                    put("context_status", validation.status)
                    put("context_manifest", validation.current.toJson(includeContent = false))
                },
            )
        }
        val selectedManifest = validation.current
        if (
            selectedManifest.selectionToken.isNullOrBlank() ||
            selectionToken.isBlank() ||
            selectionToken != selectedManifest.selectionToken
        ) {
            return result(
                "chapter_writer",
                "needs_confirmation",
                "context_selection_token 缺失或已失效；请使用 submit_context_evidence 在上一模型步骤返回的令牌",
                buildJsonObject {
                    put("context_manifest_id", selectedManifest.id)
                    put("next_tool", "submit_context_evidence")
                },
            )
        }
        val manifest = selectedManifest.copy(selectionToken = null)
        cacheManifest(manifest)

        val outlineTitle = targetOutline.payload.string("title")
        val runId = mobileChapterWriteRunId(projectId, config.model, manifest)
        val stored = chapterWriteStore.load(runId)
        var resumeContent = ""
        if (stored != null && stored.content.isNotBlank()) {
            val validation = engine.validate(stored.manifest, inputs)
            if (validation.ready) {
                val recoveredManifest = validation.current.copy(selectionToken = null)
                cacheManifest(recoveredManifest)
                if (stored.state == MobileChapterWriteState.GENERATED) {
                    val recovered = chapterWriteStore.save(
                        stored.copy(manifest = recoveredManifest),
                    )
                    return chapterDraftResult(
                        run = recovered,
                        outlineTitle = outlineTitle,
                        rawPayloads = rawPayloads,
                        detail = "已从本机恢复此前生成的未保存章节草稿",
                        recovered = true,
                    )
                }
                resumeContent = stored.content
            }
        }

        var checkpointRun = chapterWriteStore.save(
            stored?.copy(
                content = resumeContent,
                state = MobileChapterWriteState.GENERATING,
                manifest = manifest,
                error = null,
            ) ?: MobileChapterWriteRun(
                id = runId,
                projectId = projectId,
                model = config.model,
                title = outlineTitle.ifBlank { args.string("title").ifBlank { "未命名章节" } },
                content = resumeContent,
                state = MobileChapterWriteState.GENERATING,
                manifest = manifest,
            ),
        )
        val selectedItems = manifest.generationItems.filter { it.category == "agent_selected" }
        val supportingOutlines = selectedItems
            .filter { it.sourceType == "outline" }
            .joinToString("\n\n") { it.content }
        val outlineContext = listOf(
            manifest.categoryText("target_outline", "暂无当前大纲节点。"),
            supportingOutlines,
        ).filter(String::isNotBlank).joinToString("\n\n")
        val worldAndGovernance = selectedItems
            .filter { it.sourceType !in setOf("outline", "chapter", "chapter_summary", "character", "character_timeline") }
            .joinToString("\n\n") { it.content }
            .ifBlank { "暂无额外世界观资料。" }
        val characterProfiles = selectedItems
            .filter { it.sourceType in setOf("character", "character_timeline") }
            .joinToString("\n\n") { it.content }
            .ifBlank { "未选择额外角色档案。" }
        val recentSummaries = selectedItems
            .filter { it.sourceType in setOf("chapter", "chapter_summary") }
            .joinToString("\n\n") { it.content }
            .ifBlank { "暂无模型选中的前文资料。" }
        val requirements = manifest.categoryText("user_requirement", request.requirements)
        val messages = contract.chapterMessages(
            project = project,
            outlineContext = outlineContext,
            worldContext = worldAndGovernance,
            characterProfiles = characterProfiles,
            recentSummaries = recentSummaries,
            requirements = requirements,
        )
        var checkpointContent = checkpointRun.content
        var persistedChars = checkpointContent.length
        if (checkpointContent.isNotBlank()) {
            onEvent(event("status", "已恢复 ${checkpointContent.length} 字本机检查点，正在验证接缝并继续生成"))
        }
        val content = try {
            directApi.completeResumable(
                config = config,
                systemPrompt = messages[0].string("content"),
                userPrompt = messages[1].string("content"),
                maxOutputTokens = 7_000,
                temperature = 0.8,
                initialContent = checkpointContent,
                maxResumeAttempts = 8,
                onCheckpoint = { nextContent ->
                    val previousContent = checkpointContent
                    checkpointContent = nextContent
                    val delta = if (nextContent.startsWith(previousContent)) {
                        nextContent.removePrefix(previousContent)
                    } else {
                        nextContent
                    }
                    if (delta.isNotEmpty()) {
                        onEvent(
                            event(
                                type = "chapter_draft_delta",
                                delta = delta,
                                data = buildJsonObject {
                                    put("draft_id", runId)
                                    put("title", checkpointRun.title)
                                    put("outline_node_id", request.outlineNodeId)
                                    put("draft_status", MobileChapterWriteState.GENERATING)
                                    put("execution_route", "android_standalone")
                                },
                            ),
                        )
                    }
                    if (
                        nextContent.length - persistedChars >= 512 ||
                        nextContent.endsWith("\n\n")
                    ) {
                        checkpointRun = chapterWriteStore.save(
                            checkpointRun.copy(
                                content = nextContent,
                                state = MobileChapterWriteState.GENERATING,
                                error = null,
                            ),
                        )
                        persistedChars = nextContent.length
                        onEvent(event("status", "章节已生成并保存 ${nextContent.length} 字检查点"))
                    }
                },
            ).trim()
        } catch (error: CancellationException) {
            chapterWriteStore.transition(
                checkpointRun.copy(content = checkpointContent),
                MobileChapterWriteState.CANCELLED,
                error = "用户取消生成；已保存文字检查点，未写入章节。",
            )
            throw error
        } catch (error: Exception) {
            chapterWriteStore.transition(
                checkpointRun.copy(content = checkpointContent),
                MobileChapterWriteState.FAILED,
                error = error.message ?: "章节生成失败",
            )
            throw error
        }
        if (content.isBlank()) {
            chapterWriteStore.transition(
                checkpointRun.copy(content = checkpointContent),
                MobileChapterWriteState.FAILED,
                error = "模型返回空正文",
            )
            return errorResult("chapter_writer", "生成的章节正文为空")
        }
        val generated = chapterWriteStore.save(
            checkpointRun.copy(
                content = content,
                state = MobileChapterWriteState.GENERATED,
                error = null,
            ),
        )
        return chapterDraftResult(
            run = generated,
            outlineTitle = outlineTitle,
            rawPayloads = rawPayloads,
            detail = if (resumeContent.isNotBlank()) {
                "已从本机检查点续传并生成章节正文（${countWords(content)} 字），草稿与 ContextManifest 已持久化"
            } else {
                "已生成章节正文（${countWords(content)} 字），草稿与 ContextManifest 已持久化"
            },
            recovered = false,
        )
    }

    private fun chapterDraftResult(
        run: MobileChapterWriteRun,
        outlineTitle: String,
        rawPayloads: List<JsonObject>,
        detail: String,
        recovered: Boolean,
    ): JsonObject {
        val request = run.manifest.request
        val selectedCharacterItems = run.manifest.generationItems.filter {
            it.category == "agent_selected" && it.sourceType in setOf("character", "character_timeline")
        }
        val selectedCharacterIds = selectedCharacterItems.mapNotNull { it.sourceId }.toSet()
        val selectedCharacters = rawPayloads.filter {
            it.mobileRecordType() == "character" && it.stringValue("id") in selectedCharacterIds
        }
        val governanceUsed = run.manifest.generationItems.any {
            it.category == "agent_selected" && it.sourceType == "narrative_governance"
        }
        val data = buildJsonObject {
            put("draft_id", run.id)
            put("content_ref", run.id)
            put("project_id", run.projectId)
            put("title", run.title.ifBlank { outlineTitle }.ifBlank { "AI 生成章节" })
            put("outline_node_id", request.outlineNodeId)
            put("context_manifest_id", run.manifest.id)
            put("content", run.content)
            put("word_count", countWords(run.content))
            put("model", run.model)
            put("write_run_state", run.state)
            put("draft_status", "pending")
            put("recovered", recovered)
            put("context_snapshot", buildJsonObject {
                put("outline_node_id", request.outlineNodeId)
                put("outline_title", outlineTitle)
                put("involved_characters", JsonArray(selectedCharacterItems.map { JsonPrimitive(it.title) }))
                put("resolved_aliases", JsonObject(emptyMap()))
                put("relationship_count", pcRelationshipPayloads(rawPayloads, selectedCharacters).size)
                put("narrative_governance_used", governanceUsed)
                put("prompt_contract_sha256", contract.sourceHash)
                put("context_manifest_id", run.manifest.id)
                put("context_policy_version", run.manifest.policyVersion)
                put("context_index_version", run.manifest.indexVersion)
                put("context_policy_sha256", run.manifest.policySourceHash)
                put("context_request_fingerprint", run.manifest.requestFingerprint)
                put("context_selection_fingerprint", run.manifest.selectionFingerprint)
                put("context_status", run.manifest.status)
                put("context_estimated_input_tokens", run.manifest.estimatedInputTokens)
                put("execution_route", "android_standalone")
                put("write_run_id", run.id)
            })
        }
        return ok("chapter_writer", detail, data)
    }

    private suspend fun characterWriter(
        projectId: String,
        args: JsonObject,
        config: DirectApiConfig,
    ): JsonObject {
        val all = records(projectId)
        val project = all.firstOrNull { it.entity.entityType == "project" }?.payload
            ?: return skipped("character_writer", "项目不存在", JsonObject(emptyMap()))
        val turn = directApi.agentTurn(
            config = config,
            messages = listOf(
                message("system", contract.writerSystem("character", contract.styleContext(project))),
                message(
                    "user",
                    contract.characterWriterUser(
                        requirements = args.string("requirements"),
                        name = args.string("name"),
                        roleType = args.string("role_type"),
                        worldContext = worldContext(all),
                        existingCharacters = existingCharacterList(all, detailed = true),
                    ),
                ),
            ),
            tools = contract.writerOutputTool("character"),
            toolChoice = "required",
            maxOutputTokens = 3_000,
            temperature = 0.8,
        )
        val character = structuredArguments(turn, "create_character", "character")
            ?: return errorResult("character_writer", "角色生成结果解析失败")
        if (character.string("name").isBlank()) return errorResult("character_writer", "角色生成结果缺少角色名")
        return ok(
            "character_writer",
            "已生成角色卡片：${character.string("name")}",
            buildJsonObject { put("character", character) },
        )
    }

    private suspend fun outlineWriter(
        projectId: String,
        args: JsonObject,
        config: DirectApiConfig,
    ): JsonObject {
        val all = records(projectId)
        val rawPayloads = rawRecords(projectId).map(LocalRecord::payload)
        val project = all.firstOrNull { it.entity.entityType == "project" }?.payload
            ?: return skipped("outline_writer", "项目不存在", JsonObject(emptyMap()))
        val manifestId = args.string("context_manifest_id")
        val selectionToken = args.string("context_selection_token")
        val cachedManifest = contextManifests[manifestId]
            ?: return result(
                "outline_writer",
                "needs_confirmation",
                "必须先建立精简规划基线，并让模型检索、复核本次需要的资料",
                buildJsonObject { put("next_tool", "prepare_task_context") },
            )
        val request = cachedManifest.request
        if (request.taskType != "outline_planning") {
            return result(
                "outline_writer",
                "needs_confirmation",
                "context_manifest_id 不属于大纲规划任务",
                buildJsonObject { put("next_tool", "prepare_task_context") },
            )
        }
        val parentId = args.string("parent_id")
        val insertAfterId = args.string("insert_after_id")
        if (parentId != request.parentId || insertAfterId != request.insertAfterId) {
            return result(
                "outline_writer",
                "needs_confirmation",
                "上下文清单中的大纲位置与本次调用不一致",
                buildJsonObject { put("next_tool", "prepare_task_context") },
            )
        }
        outlineDraftStore.latestPending(projectId)?.let { pending ->
            return result(
                "outline_writer",
                "blocked",
                "已有一份大纲草稿等待作者处理，本轮未生成新的规划。",
                outlineDraftData(pending),
            )
        }
        val inputs = manifestInputs(projectId, config.model, request, project, all, rawPayloads)
        val engine = contextEngine("outline_planning")
        val validation = engine.validate(cachedManifest, inputs)
        if (!validation.ready) {
            cacheManifest(validation.current)
            return result(
                "outline_writer",
                validation.status,
                validation.detail,
                buildJsonObject {
                    put("context_status", validation.status)
                    put("context_manifest", validation.current.toJson(includeContent = false))
                },
            )
        }
        val selectedManifest = validation.current
        if (
            selectedManifest.selectionToken.isNullOrBlank() ||
            selectionToken.isBlank() ||
            selectionToken != selectedManifest.selectionToken
        ) {
            return result(
                "outline_writer",
                "needs_confirmation",
                "context_selection_token 缺失或已失效；请使用上一模型步骤返回的令牌",
                buildJsonObject { put("next_tool", "submit_context_evidence") },
            )
        }
        val manifest = selectedManifest.copy(selectionToken = null)
        cacheManifest(manifest)
        val batchCount = request.batchCount.coerceIn(1, 8)
        val turn = directApi.agentTurn(
            config = config,
            messages = listOf(
                message("system", contract.writerSystem("outline", "")),
                message(
                    "user",
                    contract.outlineWriterUser(
                        taskContext = manifest.renderedContext(),
                        batchCount = batchCount,
                    ),
                ),
            ),
            tools = contract.writerOutputTool("outline"),
            toolChoice = "required",
            maxOutputTokens = manifest.outputReserveTokens.coerceAtLeast(1),
            temperature = 0.7,
        )
        val parsed = structuredArguments(turn, "propose_outline_nodes")
            ?: return errorResult("outline_writer", "大纲生成结果解析失败")
        val nodes = parsed["nodes"] as? JsonArray
            ?: return errorResult("outline_writer", "大纲生成结果缺少 nodes")
        if (nodes.isEmpty()) return errorResult("outline_writer", "大纲生成结果没有可审阅节点")
        if (nodes.size > 8) return errorResult("outline_writer", "单次大纲草稿最多包含 8 个节点")
        if (nodes.any { element -> element !is JsonObject }) {
            return errorResult("outline_writer", "大纲生成结果包含无效节点")
        }
        val stored = try {
            outlineDraftStore.save(
                MobileOutlineDraftRun(
                    id = mobileOutlineDraftId(projectId, config.model, manifest),
                    projectId = projectId,
                    model = config.model,
                    parentId = request.parentId,
                    insertAfterId = request.insertAfterId,
                    nodes = nodes,
                    designNotes = (parsed["design_notes"] as? JsonPrimitive)?.contentOrNull.orEmpty(),
                    state = MobileOutlineDraftState.PENDING,
                    manifest = manifest,
                    baseOutlineHash = mobileOutlineTreeHash(
                        rawPayloads.filter { it.string("_record_type") == "outline_node" },
                    ),
                ),
            )
        } catch (invalid: IllegalArgumentException) {
            return errorResult(
                "outline_writer",
                invalid.message ?: "大纲生成结果不符合草稿约束",
            )
        } catch (conflict: MobilePendingOutlineDraftConflict) {
            val pending = outlineDraftStore.latestPending(projectId)
            return result(
                "outline_writer",
                "blocked",
                "已有一份大纲草稿等待作者处理，本轮未生成新的规划。",
                pending?.let(::outlineDraftData)
                    ?: buildJsonObject { put("draft_id", conflict.draftId) },
            )
        }
        return ok(
            "outline_writer",
            "已生成 ${stored.nodes.size} 个可编辑大纲草稿节点；确认前不会写入正式大纲",
            outlineDraftData(stored),
        )
    }

    private fun outlineDraftData(draft: MobileOutlineDraftRun): JsonObject = buildJsonObject {
        put("draft_id", draft.id)
        put("project_id", draft.projectId)
        put("context_manifest_id", draft.manifest.id)
        draft.parentId.takeIf(String::isNotBlank)?.let { put("parent_id", it) }
        draft.insertAfterId.takeIf(String::isNotBlank)?.let { put("insert_after_id", it) }
        put("draft_status", draft.state)
        put("nodes", draft.nodes)
        put("design_notes", draft.designNotes)
        put("context_selection_digest", draft.manifest.selectionFingerprint)
        put("base_outline_hash", draft.baseOutlineHash)
        put("saved_outline_node_ids", JsonArray(draft.savedOutlineNodeIds.map(::JsonPrimitive)))
        put("created_at", draft.createdAt)
        put("updated_at", draft.updatedAt)
        put(
            "next_actions",
            if (draft.state == MobileOutlineDraftState.PENDING) {
                buildJsonArray {
                    add(JsonPrimitive("edit"))
                    add(JsonPrimitive("confirm"))
                    add(JsonPrimitive("confirm_and_write"))
                    add(JsonPrimitive("regenerate"))
                    add(JsonPrimitive("discard"))
                }
            } else {
                JsonArray(emptyList())
            },
        )
        put("execution_route", "android_standalone")
    }

    private suspend fun worldbuildingWriter(
        projectId: String,
        args: JsonObject,
        config: DirectApiConfig,
    ): JsonObject {
        val all = records(projectId)
        val project = all.firstOrNull { it.entity.entityType == "project" }?.payload
            ?: return skipped("worldbuilding_writer", "项目不存在", JsonObject(emptyMap()))
        val dimension = args.string("dimension").takeIf { it in WORLD_DIMENSIONS } ?: "culture"
        val turn = directApi.agentTurn(
            config = config,
            messages = listOf(
                message("system", contract.writerSystem("world", contract.styleContext(project), dimension)),
                message(
                    "user",
                    contract.worldWriterUser(
                        requirements = args.string("requirements"),
                        title = args.string("title"),
                        dimension = dimension,
                        worldContext = worldContext(all),
                    ),
                ),
            ),
            tools = contract.writerOutputTool("world"),
            toolChoice = "required",
            maxOutputTokens = 3_000,
            temperature = 0.8,
        )
        val entry = structuredArguments(turn, "create_worldbuilding_entry", "entry")
            ?: return errorResult("worldbuilding_writer", "世界观条目生成结果解析失败")
        if (entry.string("title").isBlank()) return errorResult("worldbuilding_writer", "世界观生成结果缺少标题")
        return ok(
            "worldbuilding_writer",
            "已生成世界观条目：${entry.string("title")}",
            buildJsonObject { put("entry", entry) },
        )
    }

    private suspend fun createCharacter(projectId: String, args: JsonObject): JsonObject {
        if (args.string("name").isBlank()) return skipped("create_character", "角色名为空")
        val id = UUID.randomUUID().toString()
        var normalized = args
        if (args.string("current_goal").isBlank() && args.string("motivation").isNotBlank()) {
            normalized = normalized.withDerived("current_goal", args.getValue("motivation"))
        }
        if (args.string("active_conflict").isBlank() && args.string("conflict").isNotBlank()) {
            normalized = normalized.withDerived("active_conflict", args.getValue("conflict"))
        }
        val payload = mergeRecord(null, normalized, "character", projectId, id)
            .withDefaults(mapOf("role_type" to JsonPrimitive("supporting"), "is_evolution_tracked" to JsonPrimitive(true)))
        val savedId = saveEntity(projectId, "character", id, payload)
        return ok("create_character", "已创建角色：${payload.string("name")}", clean(payload).withDerived("id", JsonPrimitive(savedId)))
    }

    private suspend fun updateCharacter(projectId: String, args: JsonObject): JsonObject {
        val current = records(projectId, "character").firstOrNull {
            val id = args.string("id")
            if (id.isNotBlank()) it.entity.entityId == id
            else it.payload.string("name") == args.string("name")
        } ?: return skipped("update_character", "未找到角色")
        var normalized = args
        if (args.string("current_goal").isBlank() && args.string("motivation").isNotBlank()) {
            normalized = normalized.withDerived("current_goal", args.getValue("motivation"))
        }
        if (args.string("active_conflict").isBlank() && args.string("conflict").isNotBlank()) {
            normalized = normalized.withDerived("active_conflict", args.getValue("conflict"))
        }
        val payload = mergeRecord(current.payload, normalized, "character", projectId, current.entity.entityId, LOCATOR_FIELDS)
        saveEntity(projectId, "character", current.entity.entityId, payload)
        return ok("update_character", "已更新角色：${payload.string("name")}", clean(payload))
    }

    private suspend fun createOutlineNode(projectId: String, args: JsonObject): JsonObject {
        if (args.string("title").isBlank()) return skipped("create_outline_node", "大纲标题为空")
        val id = UUID.randomUUID().toString()
        val payload = mergeRecord(null, args, "outline", projectId, id)
            .withDefaults(
                mapOf(
                    "node_type" to JsonPrimitive("chapter"),
                    "status" to JsonPrimitive("pending"),
                    "sort_order" to JsonPrimitive(nextSortOrder(records(projectId, "outline"))),
                ),
            )
        val savedId = saveEntity(projectId, "outline", id, payload)
        return ok("create_outline_node", "已创建大纲节点：${payload.string("title")}", clean(payload).withDerived("id", JsonPrimitive(savedId)))
    }

    private suspend fun createOutlineNodes(projectId: String, args: JsonObject): JsonObject {
        val rawNodes = (args["nodes"] as? JsonArray).orEmpty()
        if (rawNodes.isEmpty()) return skipped("create_outline_nodes", "大纲节点列表为空", JsonArray(emptyList()))
        if (rawNodes.size > 8) {
            return errorResult("create_outline_nodes", "单次最多创建 8 个大纲节点；本次未写入任何节点")
        }
        val existing = records(projectId, "outline")
        var sortOrder = nextSortOrder(existing)
        val titleIds = existing.associate { it.payload.string("title") to it.entity.entityId }.toMutableMap()
        val created = mutableListOf<JsonObject>()
        rawNodes.forEach { raw ->
            val node = raw as? JsonObject ?: return@forEach
            val title = node.string("title")
            if (title.isBlank()) return@forEach
            val id = UUID.randomUUID().toString()
            val parentId = node.string("parent_id")
                .ifBlank { titleIds[node.string("parent_title")].orEmpty() }
                .ifBlank { args.string("parent_id") }
            var normalized = node
            if (parentId.isNotBlank()) normalized = normalized.withDerived("parent_id", JsonPrimitive(parentId))
            val payload = mergeRecord(
                null,
                normalized,
                "outline",
                projectId,
                id,
                excluded = setOf("parent_title", "related_characters"),
            ).withDefaults(
                mapOf(
                    "node_type" to JsonPrimitive("chapter"),
                    "status" to JsonPrimitive("pending"),
                    "sort_order" to JsonPrimitive(sortOrder++),
                ),
            )
            val savedId = saveEntity(projectId, "outline", id, payload)
            titleIds[title] = savedId
            created += clean(payload).withDerived("id", JsonPrimitive(savedId))
        }
        return ok(
            "create_outline_nodes",
            "已创建 ${created.size} 个大纲节点",
            buildJsonObject { put("items", JsonArray(created)) },
        )
    }

    private suspend fun updateOutlineNode(projectId: String, args: JsonObject): JsonObject {
        val all = records(projectId, "outline")
        val ids = listOf("id", "outline_node_id", "node_id")
            .map { key -> args.string(key) }
            .firstOrNull(String::isNotBlank)
        val titles = listOf("outline_node_title", "current_title", "old_title", "title")
            .map { key -> args.string(key) }
        val current = all.firstOrNull {
            if (!ids.isNullOrBlank()) it.entity.entityId == ids else it.payload.string("title") in titles
        } ?: return skipped("update_outline_node", "未找到大纲节点")
        val payload = mergeRecord(current.payload, args, "outline", projectId, current.entity.entityId, LOCATOR_FIELDS)
        saveEntity(projectId, "outline", current.entity.entityId, payload)
        return ok("update_outline_node", "已更新大纲节点：${payload.string("title")}", clean(payload))
    }

    private suspend fun createWorldbuilding(projectId: String, args: JsonObject): JsonObject {
        if (args.string("title").isBlank() || args.string("content").isBlank()) {
            return skipped("create_worldbuilding_entry", "世界观标题或内容为空")
        }
        val id = UUID.randomUUID().toString()
        val dimension = args.string("dimension").takeIf { it in WORLD_DIMENSIONS } ?: "culture"
        val normalized = args.withDerived("dimension", JsonPrimitive(dimension))
        val payload = mergeRecord(null, normalized, "world", projectId, id)
            .withDefaults(mapOf("sort_order" to JsonPrimitive(nextSortOrder(records(projectId, "world")))))
        val savedId = saveEntity(projectId, "world", id, payload)
        return ok(
            "create_worldbuilding_entry",
            "已创建世界观：${payload.string("title")}",
            clean(payload).withDerived("id", JsonPrimitive(savedId)),
        )
    }

    private suspend fun updateWorldbuilding(projectId: String, args: JsonObject): JsonObject {
        val current = records(projectId, "world").firstOrNull {
            val id = args.string("id")
            if (id.isNotBlank()) it.entity.entityId == id else it.payload.string("title") == args.string("title")
        } ?: return skipped("update_worldbuilding_entry", "未找到世界观条目")
        val normalized = if (args["dimension"] != null && args.string("dimension") !in WORLD_DIMENSIONS) {
            args.withDerived("dimension", JsonPrimitive("culture"))
        } else {
            args
        }
        val payload = mergeRecord(current.payload, normalized, "world", projectId, current.entity.entityId, LOCATOR_FIELDS)
        saveEntity(projectId, "world", current.entity.entityId, payload)
        return ok("update_worldbuilding_entry", "已更新世界观：${payload.string("title")}", clean(payload))
    }

    private fun manifestInputs(
        projectId: String,
        model: String,
        request: MobileContextRequest,
        project: JsonObject,
        all: List<LocalRecord>,
        rawPayloads: List<JsonObject>,
    ): MobileContextInputs = MobileContextInputs(
        projectId = projectId,
        model = model,
        request = request,
        project = project,
        styleText = contract.styleContext(project),
        primaryRecords = all.map(LocalRecord::payload),
        rawRecords = rawPayloads,
    )

    private fun contextTaskConfig(config: DirectApiConfig, taskType: String): DirectApiConfig =
        config.forTask(
            if (taskType == "outline_planning") {
                DirectApiConfig.TASK_PLANNING
            } else {
                DirectApiConfig.TASK_WRITING
            },
        )

    private fun contextEngine(taskType: String): MobileContextManifestEngine =
        MobileContextManifestEngine(contextPolicies.policy(taskType))

    private fun cacheManifest(manifest: MobileContextManifest) {
        contextManifests[manifest.id] = manifest
        while (contextManifests.size > MAX_CONTEXT_MANIFESTS) {
            contextManifests.remove(contextManifests.keys.first())
        }
    }

    private fun MobileContextManifest.categoryText(category: String, fallback: String): String =
        items.filter { it.category == category }.joinToString("\n\n") { it.content }.ifBlank { fallback }

    private suspend fun rawRecords(projectId: String): List<LocalRecord> =
        loadSnapshot(projectId)
            .asSequence()
            .filter { it.operation == "upsert" }
            .mapNotNull { entity ->
                val payload = entity.payloadJson?.let { raw ->
                    runCatching { json.parseToJsonElement(raw) as? JsonObject }.getOrNull()
                } ?: return@mapNotNull null
                LocalRecord(entity, payload)
            }
            .toList()

    private suspend fun records(projectId: String, entityType: String? = null): List<LocalRecord> {
        val snapshot = loadSnapshot(projectId).filter { it.operation == "upsert" }
        val matching = if (entityType == null) {
            primaryAuthoringSnapshot(snapshot)
        } else {
            snapshot.filter { it.entityType == entityType }
        }
        val ordered = entityType?.let { orderReplicaEntities(it, matching) } ?: matching
        return ordered.asSequence()
            .mapNotNull { entity ->
                val payload = entity.payloadJson?.let {
                    runCatching { json.parseToJsonElement(it) as? JsonObject }.getOrNull()
                } ?: return@mapNotNull null
                LocalRecord(entity, payload)
            }
            .toList()
    }

    private fun orderedChapters(records: List<LocalRecord>): List<LocalRecord> {
        val byKey = records.associateBy { it.entity.key }
        return orderReplicaEntities(
            "chapter",
            records.filter { it.entity.entityType == "chapter" }.map(LocalRecord::entity),
        ).mapNotNull { byKey[it.key] }
    }

    private fun mergeRecord(
        base: JsonObject?,
        changes: JsonObject,
        entityType: String,
        projectId: String,
        entityId: String,
        excluded: Set<String> = emptySet(),
    ): JsonObject = buildJsonObject {
        base?.forEach { (key, value) -> put(key, value) }
        changes.forEach { (key, value) -> if (key !in excluded) put(key, value) }
        put("_record_type", RECORD_TYPES.getValue(entityType))
        put("id", entityId)
        if (entityType != "project") put("project_id", projectId)
    }

    private fun outlinePayload(payload: JsonObject): JsonObject = select(
        payload,
        "id", "parent_id", "node_type", "title", "summary", "status", "sort_order",
        "source_chapter_id", "actual_summary", "planned_summary", "cataloging_status", "metadata",
        "character_names", "character_ids", "characters",
    )

    private fun outlineTree(all: List<LocalRecord>, parentId: String, visited: Set<String>): JsonArray = buildJsonArray {
        all.filter { it.payload.string("parent_id") == parentId && it.entity.entityId !in visited }
            .sortedBy { it.payload.int("sort_order") }
            .forEach { node ->
                add(buildJsonObject {
                    put("id", node.entity.entityId)
                    put("node_type", node.payload.string("node_type"))
                    put("title", node.payload.string("title"))
                    put("children", outlineTree(all, node.entity.entityId, visited + node.entity.entityId))
                })
            }
    }

    private fun countTree(tree: JsonArray): Int = tree.sumOf { raw ->
        val node = raw as? JsonObject ?: return@sumOf 0
        1 + countTree(node["children"] as? JsonArray ?: JsonArray(emptyList()))
    }

    private fun outlineContext(all: List<LocalRecord>, targetId: String): String {
        val outlines = all.filter { it.entity.entityType == "outline" }
        if (outlines.isEmpty()) return "暂无大纲。"
        val selected = if (targetId.isBlank()) outlines else outlines.filter {
            it.entity.entityId == targetId || isDescendant(it, targetId, outlines)
        }
        if (selected.isEmpty()) return "暂无当前大纲节点。"
        return selected.sortedBy { it.payload.int("sort_order") }.joinToString("\n") {
            val p = it.payload
            "- [${p.string("node_type").ifBlank { "chapter" }}] ${p.string("title")}（${p.string("status").ifBlank { "pending" }}）\n  ${p.string("summary").ifBlank { "暂无摘要" }}"
        }
    }

    private fun isDescendant(node: LocalRecord, ancestorId: String, all: List<LocalRecord>): Boolean {
        val parentById = all.associate { it.entity.entityId to it.payload.string("parent_id") }
        var current = node.payload.string("parent_id")
        val visited = mutableSetOf<String>()
        while (current.isNotBlank() && visited.add(current)) {
            if (current == ancestorId) return true
            current = parentById[current].orEmpty()
        }
        return false
    }

    private fun worldContext(all: List<LocalRecord>): String {
        val entries = all.filter { it.entity.entityType == "world" }
            .sortedWith(compareBy<LocalRecord> { it.payload.string("dimension") }.thenBy { it.payload.int("sort_order") })
            .take(32)
        if (entries.isEmpty()) return "暂无世界观设定。"
        return entries.joinToString("\n\n") {
            val p = it.payload
            "【${p.string("dimension").ifBlank { "culture" }}·${p.string("title")}】\n${p.string("content")}".take(2_500)
        }
    }

    private fun characterContext(all: List<LocalRecord>, names: List<String>): String {
        val candidates = all.filter { it.entity.entityType == "character" }
            .filter { names.isEmpty() || it.payload.string("name") in names }
            .take(16)
        if (candidates.isEmpty()) return "未指定角色。"
        return candidates.joinToString("\n\n") {
            val p = it.payload
            buildString {
                append("【${p.string("name")}】\n")
                append("  身份: ${p.string("role_type").ifBlank { "未设定" }}\n")
                append("  性格: ${p.string("personality").ifBlank { "未设定" }.take(300)}\n")
                append("  背景: ${p.string("background").ifBlank { "未设定" }.take(300)}\n")
                append("  当前目标: ${p.string("current_goal").ifBlank { "未设定" }.take(200)}\n")
                append("  当前冲突: ${p.string("active_conflict").ifBlank { "未设定" }.take(200)}")
            }
        }
    }

    private fun recentSummaries(all: List<LocalRecord>, limit: Int): String {
        val chapters = orderedChapters(all).takeLast(limit)
        if (chapters.isEmpty()) return "暂无前文摘要。"
        return chapters.joinToString("\n") {
            val p = it.payload
            val summary = p.string("summary").ifBlank { p.string("content").take(500) }
            "- ${p.string("title")}: $summary"
        }
    }

    private fun existingCharacterList(all: List<LocalRecord>, detailed: Boolean): String {
        val characters = all.filter { it.entity.entityType == "character" }.take(30)
        if (characters.isEmpty()) return "暂无角色。"
        return characters.joinToString("\n") {
            val p = it.payload
            if (detailed) {
                "- ${p.string("name")}（${p.string("role_type").ifBlank { "未设定" }}）: 性格: ${p.string("personality").take(100)}; 背景: ${p.string("background").take(100)}"
            } else {
                "- ${p.string("name")}（${p.string("role_type").ifBlank { "未设定" }}）"
            }
        }
    }

    private fun existingOutlineList(all: List<LocalRecord>): String {
        val outlines = all.filter { it.entity.entityType == "outline" }
        if (outlines.isEmpty()) return "暂无大纲。"
        val byId = outlines.associateBy { it.entity.entityId }
        return outlines.sortedBy { it.payload.int("sort_order") }.joinToString("\n") { node ->
            var depth = 0
            var parent = node.payload.string("parent_id")
            val visited = mutableSetOf<String>()
            while (parent.isNotBlank() && visited.add(parent)) {
                depth += 1
                parent = byId[parent]?.payload?.string("parent_id").orEmpty()
            }
            "${"  ".repeat(depth)}- [${node.payload.string("node_type")}] ${node.payload.string("title")} (${node.payload.string("status").ifBlank { "pending" }})"
        }
    }

    private fun structuredArguments(turn: DirectAgentTurn, tool: String, wrapper: String? = null): JsonObject? {
        val call = turn.toolCalls.firstOrNull { it.name == tool } ?: turn.toolCalls.firstOrNull()
        var parsed = call?.arguments ?: parseJsonObject(turn.content) ?: return null
        if (wrapper != null) (parsed[wrapper] as? JsonObject)?.let { parsed = it }
        return parsed
    }

    private fun parseJsonObject(raw: String): JsonObject? {
        val clean = raw.trim()
            .removePrefix("```json")
            .removePrefix("```")
            .removeSuffix("```")
            .trim()
        val candidate = if (clean.startsWith("{") && clean.endsWith("}")) {
            clean
        } else {
            val start = clean.indexOf('{')
            val end = clean.lastIndexOf('}')
            if (start < 0 || end <= start) return null else clean.substring(start, end + 1)
        }
        return runCatching { json.parseToJsonElement(candidate) as? JsonObject }.getOrNull()
    }

    private fun ok(tool: String, detail: String, data: JsonElement = JsonNull): JsonObject = result(tool, "ok", detail, data)

    private fun skipped(tool: String, detail: String, data: JsonElement = JsonNull): JsonObject =
        result(tool, "skipped", detail, data)

    private fun errorResult(tool: String, detail: String): JsonObject = result(tool, "error", detail, JsonObject(emptyMap()))

    private fun result(tool: String, status: String, detail: String, data: JsonElement): JsonObject = buildJsonObject {
        put("tool", tool)
        put("status", status)
        put("detail", detail)
        put("data", data)
    }

    /** Mirrors PC redact_tool_result_for_model for long chapter drafts. */
    private fun modelToolResult(result: JsonObject): JsonObject {
        if (result.string("tool") != "chapter_writer") return result
        val data = result["data"] as? JsonObject ?: return result
        val content = data.string("content")
        if (content.isBlank()) return result
        val compactData = buildJsonObject {
            data.forEach { (key, value) -> if (key != "content") put(key, value) }
            put("content_preview", content.take(500) + if (content.length > 500) "..." else "")
            put(
                "usage_note",
                "草稿已持久化；这是本轮终点。正式保存、建档、去除 AI 味和质量评分均由作者在界面另行发起。",
            )
        }
        return buildJsonObject {
            result.forEach { (key, value) -> put(key, if (key == "data") compactData else value) }
        }
    }

    private fun event(
        type: String,
        detail: String = "",
        delta: String = "",
        data: JsonElement? = null,
    ): String =
        buildJsonObject {
            put("type", type)
            if (detail.isNotBlank()) put("detail", detail)
            if (delta.isNotBlank()) put("delta", delta)
            data?.let { put("data", it) }
        }.toString()

    private fun message(role: String, content: String): JsonObject = buildJsonObject {
        put("role", role)
        put("content", content)
    }

    private fun assistantToolMessage(
        content: String,
        reasoningContent: String,
        calls: List<DirectAgentToolCall>,
    ): JsonObject = buildJsonObject {
        put("role", "assistant")
        put("content", content)
        if (reasoningContent.isNotBlank()) put("reasoning_content", reasoningContent)
        put("tool_calls", buildJsonArray {
            calls.forEach { call ->
                add(buildJsonObject {
                    put("id", call.id)
                    put("type", "function")
                    put("function", buildJsonObject {
                        put("name", call.name)
                        put("arguments", call.arguments.toString())
                    })
                })
            }
        })
    }

    private fun clean(source: JsonObject): JsonObject = buildJsonObject {
        source.forEach { (key, value) -> if (key != "_record_type") put(key, value) }
    }

    private fun select(source: JsonObject, vararg fields: String): JsonObject = buildJsonObject {
        fields.forEach { field -> source[field]?.let { put(field, it) } }
    }

    private fun jsonStringMap(values: Map<String, String>): JsonObject = buildJsonObject {
        values.forEach { (key, value) -> put(key, value) }
    }

    private fun JsonObject.withDefaults(defaults: Map<String, JsonElement>): JsonObject = buildJsonObject {
        this@withDefaults.forEach { (key, value) -> put(key, value) }
        defaults.forEach { (key, value) ->
            val current = this@withDefaults[key]
            if (current == null || current == JsonNull || (current as? JsonPrimitive)?.contentOrNull.isNullOrBlank()) {
                put(key, value)
            }
        }
    }

    private fun JsonObject.withDerived(key: String, value: JsonElement): JsonObject = buildJsonObject {
        this@withDerived.forEach { (name, element) -> put(name, element) }
        put(key, value)
    }

    private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()

    private fun JsonObject.int(name: String, fallback: Int = 0): Int =
        (get(name) as? JsonPrimitive)?.intOrNull ?: fallback

    private fun JsonObject.limit(fallback: Int, maximum: Int): Int = int("limit", fallback).coerceIn(1, maximum)

    private fun JsonObject.stringList(name: String): List<String> = when (val value = get(name)) {
        is JsonArray -> value.mapNotNull { (it as? JsonPrimitive)?.contentOrNull?.trim() }.filter(String::isNotBlank)
        is JsonPrimitive -> value.contentOrNull.orEmpty().split(',', '，').map(String::trim).filter(String::isNotBlank)
        else -> emptyList()
    }

    private fun nextSortOrder(records: List<LocalRecord>): Int =
        (records.maxOfOrNull { it.payload.int("sort_order") } ?: -1) + 1

    private fun countWords(content: String): Int = content.count { !it.isWhitespace() }

    private data class LocalRecord(val entity: ReplicaEntity, val payload: JsonObject)

    companion object {
        private const val MAX_ITERATIONS = 12
        private const val MAX_CONTEXT_MANIFESTS = 20
        private val WORLD_DIMENSIONS = setOf("geography", "history", "factions", "power_system", "races", "culture")
        private val LOCATOR_FIELDS = setOf(
            "id", "project_id", "chapter_id", "chapter_title", "outline_node_id", "node_id",
            "outline_node_title", "outline_title", "current_title", "old_title",
        )
        private val RECORD_TYPES = mapOf(
            "project" to "project",
            "chapter" to "chapter",
            "outline" to "outline_node",
            "character" to "character",
            "world" to "world_entry",
        )
    }
}

internal fun existingMobileChapterIdForOutline(
    chapters: Iterable<JsonObject>,
    outlineNodeId: String,
): String? {
    if (outlineNodeId.isBlank()) return null
    val chapter = chapters.firstOrNull {
        (it["outline_node_id"] as? JsonPrimitive)?.contentOrNull == outlineNodeId
    } ?: return null
    return (chapter["id"] as? JsonPrimitive)?.contentOrNull?.takeIf(String::isNotBlank)
        ?: "linked-chapter"
}
