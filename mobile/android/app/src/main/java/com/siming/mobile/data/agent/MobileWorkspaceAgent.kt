package com.siming.mobile.data.agent

import android.content.Context
import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.local.orderReplicaEntities
import com.siming.mobile.data.local.primaryAuthoringSnapshot
import com.siming.mobile.data.network.DirectAgentTurn
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
    private val contextPolicy = PcContextManifestPolicy(context.applicationContext).policy
    private val contextEngine = MobileContextManifestEngine(contextPolicy)
    private val chapterWriteStore = MobileChapterWriteStore(context.applicationContext)
    private val json = Json { ignoreUnknownKeys = true }
    private val contextManifests = LinkedHashMap<String, MobileContextManifest>()

    suspend fun run(
        projectId: String,
        scope: String,
        prompt: String,
        config: DirectApiConfig,
        onEvent: suspend (String) -> Unit,
    ) {
        val initialRecords = records(projectId)
        val project = initialRecords.firstOrNull { it.entity.entityType == "project" }?.payload
            ?: error("当前作品副本不存在，无法启动手机独立工作区")
        val messages = mutableListOf(
            message("system", contract.workspaceSystem(scope)),
            message(
                "user",
                contract.initialUserMessage(project, contract.styleContext(project), prompt),
            ),
        )
        onEvent(event("status", "已加载 PC 提示词契约 ${contract.sourceHash.take(12)}，开始执行"))

        var iteration = 0
        while (iteration < MAX_ITERATIONS) {
            val turn = directApi.agentTurn(
                config = config,
                messages = messages,
                tools = contract.toolSchemas,
                maxOutputTokens = 6_000,
                temperature = 0.3,
            )
            messages += turn.assistantMessage
            if (turn.toolCalls.isEmpty()) {
                val content = turn.content.trim()
                require(content.isNotBlank()) { "模型既没有调用 PC 工具，也没有返回最终内容" }
                onEvent(event("content", content = content))
                onEvent(event("done", "任务完成"))
                return
            }

            for (call in turn.toolCalls) {
                val result = if (call.name in contract.toolNames) {
                    try {
                        execute(projectId, call.name, call.arguments, config)
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
        "preview_writing_context" -> previewWritingContext(projectId, args, config)
        "chapter_writer" -> chapterWriter(projectId, args, config)
        "character_writer" -> characterWriter(projectId, args, config)
        "outline_writer" -> outlineWriter(projectId, args, config)
        "worldbuilding_writer" -> worldbuildingWriter(projectId, args, config)
        "create_chapter" -> createChapter(projectId, args)
        "update_chapter" -> updateChapter(projectId, args)
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

    private suspend fun previewWritingContext(
        projectId: String,
        args: JsonObject,
        config: DirectApiConfig,
    ): JsonObject {
        val all = records(projectId)
        val rawPayloads = rawRecords(projectId).map(LocalRecord::payload)
        val project = all.firstOrNull { it.entity.entityType == "project" }?.payload
            ?: return skipped("preview_writing_context", "项目不存在", JsonObject(emptyMap()))
        val request = MobileContextRequest.fromArgs(args, contextPolicy)
        val inputs = manifestInputs(projectId, config.model, request, project, all, rawPayloads)
        val manifest = contextEngine.prepare(inputs)
        cacheManifest(manifestKey(projectId, request), manifest)

        val resolved = resolvePcCharacters(
            rawPayloads,
            request.outlineNodeId.takeIf(String::isNotBlank),
            request.involvedCharacters,
            request.characterLimit,
        )
        val characters = resolved.characters.map(::clean)
        val relationships = pcRelationshipPayloads(rawPayloads, resolved.characters)
        val recent = orderedChapters(all).takeLast(request.recentLimit)
            .map { select(it.payload, "id", "title", "outline_node_id", "word_count", "summary") }
        val outline = manifest.categoryText("target_outline", "暂无当前大纲节点。")
        val worldItems = manifest.items.filter { it.sourceType == "worldbuilding" }
        val world = worldItems.joinToString("\n\n") { it.content }.ifBlank { "暂无世界观设定。" }
        val governance = manifest.categoryText(
            "narrative_governance",
            "Narrative governance: no due or high-risk items.",
        )
        val summaries = manifest.categoryText("previous_summary", "暂无前文摘要。")
        val data = buildJsonObject {
            put("outline_context", outline)
            put("recent_chapters", JsonArray(recent))
            put("recent_summaries_text", summaries)
            put("characters", JsonArray(characters))
            put("relationships", relationships)
            put("world_context", world)
            put("narrative_governance_context", governance)
            put("warnings", JsonArray(manifest.warnings.map(::JsonPrimitive)))
            put("requirements_preview", request.requirements.take(1_000))
            put("resolved_aliases", jsonStringMap(resolved.resolvedAliases))
            put("rag_sections", JsonArray(worldItems.map { it.toJson(includeContent = false) }))
            put("total_used_chars", manifest.estimatedInputChars)
            put("total_estimated_tokens", manifest.estimatedInputTokens)
            put("rag_used", worldItems.isNotEmpty())
            put("fts_available", false)
            put("auto_indexed", false)
            put("context_manifest", manifest.toJson(includeContent = false))
        }
        val detail = if (manifest.status == "ready") {
            "写作上下文预检通过：${characters.size} 个角色、${relationships.size} 条关系、${manifest.warnings.size} 条提示"
        } else {
            "写作上下文需要确认：必选大纲或文风锚点不完整"
        }
        return ok("preview_writing_context", detail, data)
    }

    private suspend fun chapterWriter(
        projectId: String,
        args: JsonObject,
        config: DirectApiConfig,
    ): JsonObject {
        val all = records(projectId)
        val rawPayloads = rawRecords(projectId).map(LocalRecord::payload)
        val project = all.firstOrNull { it.entity.entityType == "project" }?.payload
            ?: return skipped("chapter_writer", "项目不存在", JsonObject(emptyMap()))
        val request = MobileContextRequest.fromArgs(args, contextPolicy)
        val inputs = manifestInputs(projectId, config.model, request, project, all, rawPayloads)
        val key = manifestKey(projectId, request)
        val cached = contextManifests[key]
        val manifest = if (cached == null) {
            contextEngine.prepare(inputs).also { cacheManifest(key, it) }
        } else {
            val validation = contextEngine.validate(cached, inputs)
            if (!validation.ready) {
                return skipped(
                    "chapter_writer",
                    validation.detail,
                    buildJsonObject {
                        put("context_status", validation.status)
                        put("context_manifest", validation.current.toJson(includeContent = false))
                        put("requires_preview", true)
                    },
                )
            }
            validation.current.also { cacheManifest(key, it) }
        }
        if (manifest.status != "ready") {
            return skipped(
                "chapter_writer",
                "写作上下文缺少必选锚点，请先完成预检并确认目标大纲。",
                buildJsonObject {
                    put("context_status", manifest.status)
                    put("context_manifest", manifest.toJson(includeContent = false))
                    put("requires_preview", true)
                },
            )
        }

        val outlineTitle = all.firstOrNull {
            it.entity.entityType == "outline" && it.entity.entityId == request.outlineNodeId
        }?.payload?.string("title").orEmpty()
        val runId = mobileChapterWriteRunId(projectId, config.model, manifest)
        val stored = chapterWriteStore.load(runId)
        if (
            stored != null &&
            stored.content.isNotBlank() &&
            stored.state in setOf(
                MobileChapterWriteState.GENERATED,
                MobileChapterWriteState.COMMITTING,
                MobileChapterWriteState.COMMITTED,
            )
        ) {
            val validation = contextEngine.validate(stored.manifest, inputs)
            if (validation.ready) {
                val recovered = chapterWriteStore.save(stored.copy(manifest = validation.current))
                cacheManifest(key, validation.current)
                return chapterDraftResult(
                    run = recovered,
                    outlineTitle = outlineTitle,
                    rawPayloads = rawPayloads,
                    detail = if (recovered.state == MobileChapterWriteState.COMMITTED) {
                        "已恢复此前提交的章节运行，重复调用不会再次创建章节"
                    } else {
                        "已从本机恢复此前生成的章节草稿"
                    },
                    recovered = true,
                )
            }
        }

        val generating = chapterWriteStore.save(
            MobileChapterWriteRun(
                id = runId,
                projectId = projectId,
                model = config.model,
                title = outlineTitle.ifBlank { args.string("title").ifBlank { "未命名章节" } },
                content = "",
                state = MobileChapterWriteState.GENERATING,
                manifest = manifest,
            ),
        )
        val governance = manifest.categoryText(
            "narrative_governance",
            "Narrative governance: no due or high-risk items.",
        )
        val world = manifest.items.filter { it.sourceType == "worldbuilding" }
            .joinToString("\n\n") { it.content }
            .ifBlank { "暂无世界观设定。" }
        val worldAndGovernance = listOf(world, governance)
            .filter(String::isNotBlank)
            .joinToString("\n\n")
        val requirements = manifest.categoryText("user_requirement", request.requirements)
        val messages = contract.chapterMessages(
            mode = args.string("mode").ifBlank { "quality" },
            project = project,
            outlineContext = manifest.categoryText("target_outline", "暂无当前大纲节点。"),
            worldContext = worldAndGovernance,
            characterProfiles = manifest.categoryText("scene_character", "未指定角色。"),
            recentSummaries = manifest.categoryText("previous_summary", "暂无前文摘要。"),
            requirements = requirements,
        )
        val content = try {
            directApi.complete(
                config = config,
                systemPrompt = messages[0].string("content"),
                userPrompt = messages[1].string("content"),
                maxOutputTokens = 7_000,
                temperature = 0.8,
            ).trim()
        } catch (error: CancellationException) {
            chapterWriteStore.transition(
                generating,
                MobileChapterWriteState.CANCELLED,
                error = "用户取消生成；未写入章节。",
            )
            throw error
        } catch (error: Exception) {
            chapterWriteStore.transition(
                generating,
                MobileChapterWriteState.FAILED,
                error = error.message ?: "章节生成失败",
            )
            throw error
        }
        if (content.isBlank()) {
            chapterWriteStore.transition(
                generating,
                MobileChapterWriteState.FAILED,
                error = "模型返回空正文",
            )
            return errorResult("chapter_writer", "生成的章节正文为空")
        }
        val generated = chapterWriteStore.save(
            generating.copy(
                content = content,
                state = MobileChapterWriteState.GENERATED,
                error = null,
            ),
        )
        return chapterDraftResult(
            run = generated,
            outlineTitle = outlineTitle,
            rawPayloads = rawPayloads,
            detail = "已生成章节正文（${countWords(content)} 字），草稿与 ContextManifest 已持久化",
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
        val resolved = resolvePcCharacters(
            rawPayloads,
            request.outlineNodeId.takeIf(String::isNotBlank),
            request.involvedCharacters,
            request.characterLimit,
        )
        val governance = run.manifest.categoryText(
            "narrative_governance",
            "Narrative governance: no due or high-risk items.",
        )
        val data = buildJsonObject {
            put("draft_id", run.id)
            put("content_ref", run.id)
            put("content", run.content)
            put("word_count", countWords(run.content))
            put("model", run.model)
            put("write_run_state", run.state)
            put("recovered", recovered)
            run.chapterId?.let { put("committed_chapter_id", it) }
            put("context_snapshot", buildJsonObject {
                put("outline_node_id", request.outlineNodeId)
                put("outline_title", outlineTitle)
                put("involved_characters", JsonArray(request.involvedCharacters.map(::JsonPrimitive)))
                put("resolved_aliases", jsonStringMap(resolved.resolvedAliases))
                put("relationship_count", pcRelationshipPayloads(rawPayloads, resolved.characters).size)
                put("narrative_governance_used", governance.isNotBlank())
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
        val project = all.firstOrNull { it.entity.entityType == "project" }?.payload
            ?: return skipped("outline_writer", "项目不存在", JsonObject(emptyMap()))
        val parentId = args.string("parent_id")
        val parent = all.firstOrNull { it.entity.entityType == "outline" && it.entity.entityId == parentId }
        if (parentId.isNotBlank() && parent == null) return skipped("outline_writer", "未找到指定父节点", JsonObject(emptyMap()))
        val batchCount = args.int("batch_count", 1).coerceIn(1, 8)
        val parentContext = parent?.payload?.let {
            "父节点: [${it.string("node_type")}] ${it.string("title")}\n摘要: ${it.string("summary").ifBlank { "无" }}"
        }.orEmpty()
        val turn = directApi.agentTurn(
            config = config,
            messages = listOf(
                message("system", contract.writerSystem("outline", contract.styleContext(project))),
                message(
                    "user",
                    contract.outlineWriterUser(
                        requirements = args.string("requirements"),
                        parentContext = parentContext,
                        existingOutline = existingOutlineList(all),
                        worldContext = worldContext(all),
                        existingCharacters = existingCharacterList(all, detailed = false),
                        batchCount = batchCount,
                    ),
                ),
            ),
            tools = contract.writerOutputTool("outline"),
            toolChoice = "required",
            maxOutputTokens = 4_000,
            temperature = 0.7,
        )
        val parsed = structuredArguments(turn, "create_outline_nodes")
            ?: return errorResult("outline_writer", "大纲生成结果解析失败")
        val nodes = parsed["nodes"] as? JsonArray
            ?: return errorResult("outline_writer", "大纲生成结果缺少 nodes")
        return ok(
            "outline_writer",
            "已生成 ${nodes.size.coerceAtMost(8)} 个大纲节点",
            buildJsonObject {
                put("nodes", JsonArray(nodes.take(8)))
                put("design_notes", parsed["design_notes"] ?: JsonPrimitive(""))
            },
        )
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

    private suspend fun createChapter(projectId: String, args: JsonObject): JsonObject {
        val resolved = resolveDraft(args)
            ?: return errorResult("create_chapter", "章节草稿引用不存在或已过期")
        val run = resolved.run
        if (run != null) {
            val validation = validateChapterRun(projectId, run)
            if (!validation.ready) {
                return skipped(
                    "create_chapter",
                    "写入前 ContextManifest 已失效：${validation.detail}",
                    buildJsonObject {
                        put("context_status", validation.status)
                        put("context_manifest", validation.current.toJson(includeContent = false))
                        put("requires_preview", true)
                    },
                )
            }
        }
        val id = run?.chapterId ?: run?.let { mobileChapterEntityId(projectId, it.id) }
            ?: UUID.randomUUID().toString()
        val existing = records(projectId, "chapter").firstOrNull { it.entity.entityId == id }
        if (existing != null && run != null) {
            if (existing.payload.string("content") != resolved.args.string("content")) {
                return errorResult("create_chapter", "同一手机写章运行对应的章节已存在且正文不同，请先处理版本分岔")
            }
            val committed = chapterWriteStore.transition(
                run,
                MobileChapterWriteState.COMMITTED,
                chapterId = id,
                error = null,
            )
            return ok(
                "create_chapter",
                "章节此前已经写入，本次重试未创建重复章节：${existing.payload.string("title")}",
                clean(existing.payload)
                    .withDerived("id", JsonPrimitive(id))
                    .withDerived("write_run_id", JsonPrimitive(committed.id)),
            )
        }
        val committing = run?.let {
            chapterWriteStore.transition(
                it,
                MobileChapterWriteState.COMMITTING,
                chapterId = id,
                error = null,
            )
        }
        val payload = mergeRecord(
            null,
            resolved.args,
            "chapter",
            projectId,
            id,
            excluded = CHAPTER_CONTROL_FIELDS,
        )
            .withDefaults(mapOf("title" to JsonPrimitive("未命名章节"), "content" to JsonPrimitive("")))
            .withDerived("word_count", JsonPrimitive(countWords(resolved.args.string("content"))))
        val savedId = saveEntity(projectId, "chapter", id, payload)
        committing?.let {
            chapterWriteStore.transition(
                it,
                MobileChapterWriteState.COMMITTED,
                chapterId = savedId,
                error = null,
            )
        }
        return ok(
            "create_chapter",
            "已创建章节：${payload.string("title")}；写章运行已标记为已提交",
            clean(payload)
                .withDerived("id", JsonPrimitive(savedId))
                .let { data ->
                    if (run == null) data else data.withDerived("write_run_id", JsonPrimitive(run.id))
                },
        )
    }

    private suspend fun updateChapter(projectId: String, args: JsonObject): JsonObject {
        val current = findChapter(records(projectId, "chapter"), args)
            ?: return errorResult("update_chapter", "未找到章节，本轮未修改正文")
        val resolved = resolveDraft(args)
            ?: return errorResult("update_chapter", "章节草稿引用不存在或已过期")
        val run = resolved.run
        if (run != null) {
            val validation = validateChapterRun(projectId, run)
            if (!validation.ready) {
                return skipped(
                    "update_chapter",
                    "写入前 ContextManifest 已失效：${validation.detail}",
                    buildJsonObject {
                        put("context_status", validation.status)
                        put("context_manifest", validation.current.toJson(includeContent = false))
                        put("requires_preview", true)
                    },
                )
            }
            if (
                current.payload.string("content") == resolved.args.string("content") &&
                run.chapterId == current.entity.entityId
            ) {
                chapterWriteStore.transition(
                    run,
                    MobileChapterWriteState.COMMITTED,
                    chapterId = current.entity.entityId,
                    error = null,
                )
                return ok(
                    "update_chapter",
                    "章节此前已经更新，本次重试未创建新版本：${current.payload.string("title")}",
                    clean(current.payload).withDerived("write_run_id", JsonPrimitive(run.id)),
                )
            }
        }
        val committing = run?.let {
            chapterWriteStore.transition(
                it,
                MobileChapterWriteState.COMMITTING,
                chapterId = current.entity.entityId,
                error = null,
            )
        }
        val payload = mergeRecord(
            current.payload,
            resolved.args,
            "chapter",
            projectId,
            current.entity.entityId,
            CHAPTER_LOCATOR_FIELDS + CHAPTER_CONTROL_FIELDS,
        ).withDerived(
            "word_count",
            JsonPrimitive(countWords(resolved.args.string("content").ifBlank { current.payload.string("content") })),
        )
        saveEntity(projectId, "chapter", current.entity.entityId, payload)
        committing?.let {
            chapterWriteStore.transition(
                it,
                MobileChapterWriteState.COMMITTED,
                chapterId = current.entity.entityId,
                error = null,
            )
        }
        return ok(
            "update_chapter",
            "已更新章节：${payload.string("title")}；写章运行已标记为已提交",
            clean(payload).let { data ->
                if (run == null) data else data.withDerived("write_run_id", JsonPrimitive(run.id))
            },
        )
    }

    private suspend fun validateChapterRun(
        projectId: String,
        run: MobileChapterWriteRun,
    ): MobileContextValidation {
        if (run.projectId != projectId) {
            val current = run.manifest.copy(status = "stale")
            return MobileContextValidation("stale", "草稿不属于当前作品。", current)
        }
        val all = records(projectId)
        val rawPayloads = rawRecords(projectId).map(LocalRecord::payload)
        val project = all.firstOrNull { it.entity.entityType == "project" }?.payload
            ?: return MobileContextValidation(
                "stale",
                "当前作品副本不存在。",
                run.manifest.copy(status = "stale"),
            )
        val inputs = manifestInputs(
            projectId,
            run.model,
            run.manifest.request,
            project,
            all,
            rawPayloads,
        )
        return contextEngine.validate(run.manifest, inputs)
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
        val rawNodes = (args["nodes"] as? JsonArray).orEmpty().take(8)
        if (rawNodes.isEmpty()) return skipped("create_outline_nodes", "大纲节点列表为空", JsonArray(emptyList()))
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
        orderedChapters = orderedChapters(all).map(LocalRecord::payload),
    )

    private fun manifestKey(projectId: String, request: MobileContextRequest): String =
        "$projectId|${request.outlineNodeId.ifBlank { request.targetChapterId.ifBlank { "unscoped" } }}"

    private fun cacheManifest(key: String, manifest: MobileContextManifest) {
        contextManifests[key] = manifest
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

    private suspend fun resolveDraft(args: JsonObject): ResolvedChapterDraft? {
        val reference = args.string("draft_id").ifBlank { args.string("content_ref") }
        if (reference.isBlank()) return ResolvedChapterDraft(args, null)
        val run = chapterWriteStore.load(reference) ?: return null
        if (
            run.content.isBlank() ||
            run.state !in setOf(
                MobileChapterWriteState.GENERATED,
                MobileChapterWriteState.COMMITTING,
                MobileChapterWriteState.COMMITTED,
            )
        ) {
            return null
        }
        return ResolvedChapterDraft(
            args = args.withDerived("content", JsonPrimitive(run.content)),
            run = run,
        )
    }

    private fun findChapter(chapters: List<LocalRecord>, args: JsonObject): LocalRecord? {
        val id = args.string("id").ifBlank { args.string("chapter_id") }
        if (id.isNotBlank()) return chapters.firstOrNull { it.entity.entityId == id }
        val title = args.string("chapter_title").ifBlank { args.string("title") }
        if (title.isNotBlank()) chapters.firstOrNull { it.payload.string("title") == title }?.let { return it }
        val outline = args.string("outline_node_id")
        return if (outline.isBlank()) null else chapters.firstOrNull { it.payload.string("outline_node_id") == outline }
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
                "后续 create_chapter/update_chapter/evaluate_chapter/detect_* 工具请传 draft_id 或 content_ref，不要复制整章 content。",
            )
        }
        return buildJsonObject {
            result.forEach { (key, value) -> put(key, if (key == "data") compactData else value) }
        }
    }

    private fun event(type: String, detail: String = "", content: String = ""): String =
        buildJsonObject {
            put("type", type)
            if (detail.isNotBlank()) put("detail", detail)
            if (content.isNotBlank()) put("content", content)
        }.toString()

    private fun message(role: String, content: String): JsonObject = buildJsonObject {
        put("role", role)
        put("content", content)
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

    private data class ResolvedChapterDraft(
        val args: JsonObject,
        val run: MobileChapterWriteRun?,
    )

    companion object {
        private const val MAX_ITERATIONS = 30
        private const val MAX_CONTEXT_MANIFESTS = 20
        private val WORLD_DIMENSIONS = setOf("geography", "history", "factions", "power_system", "races", "culture")
        private val LOCATOR_FIELDS = setOf(
            "id", "project_id", "chapter_id", "chapter_title", "outline_node_id", "node_id",
            "outline_node_title", "outline_title", "current_title", "old_title",
        )
        private val CHAPTER_LOCATOR_FIELDS = setOf("id", "project_id", "chapter_id", "chapter_title")
        private val CHAPTER_CONTROL_FIELDS = setOf(
            "draft_id", "content_ref", "skip_style_repair", "rewrite", "rewrite_request_id",
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
