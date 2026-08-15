from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def path(name: str) -> Path:
    return ROOT / name


def read(name: str) -> str:
    return path(name).read_text(encoding="utf-8")


def write(name: str, value: str) -> None:
    path(name).write_text(value, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"missing marker: {label}")
    return text.replace(old, new, 1)


def remove_between(text: str, start: str, end: str, label: str) -> str:
    start_at = text.find(start)
    if start_at < 0:
        raise RuntimeError(f"missing start marker: {label}")
    end_at = text.find(end, start_at)
    if end_at < 0:
        raise RuntimeError(f"missing end marker: {label}")
    return text[:start_at] + text[end_at:]


def remove_function(text: str, name: str) -> str:
    pattern = re.compile(
        rf"\n(?:async\s+)?def\s+{re.escape(name)}\s*\([\s\S]*?(?=\n(?:async\s+)?def\s+[A-Za-z_]|\n__all__|\Z)"
    )
    updated, count = pattern.subn("\n", text, count=1)
    if count != 1:
        raise RuntimeError(f"legacy function not found: {name}")
    return updated


def export_creation_agent_contract() -> None:
    name = "scripts/export-mobile-prompt-contract.py"
    text = read(name)
    old_import = """from app.services.novel_creation_prompting import (  # noqa: E402
    COMPACT_CONCEPT_SHAPE,
    CONCEPT_TASK_KINDS,
    CONCEPT_TASK_RULES,
    CONCEPT_USER_INTROS,
    CREATION_STAGE_TASK_RULES,
    CREATION_STAGE_USER_PREFIX,
    CREATION_REPAIR_SYSTEM_PROMPT,
    CREATION_REPAIR_USER_TEMPLATE,
    NOVEL_INTERVIEW_SYSTEM_PROMPT,
    NOVEL_INTERVIEW_USER_TEMPLATE,
)
"""
    new_import = """from app.services.novel_creation_prompting import (  # noqa: E402
    COMPACT_CONCEPT_SHAPE,
    CONCEPT_TASK_KINDS,
    CONCEPT_TASK_RULES,
    CONCEPT_USER_INTROS,
    CREATION_STAGE_TASK_RULES,
    CREATION_STAGE_USER_PREFIX,
    CREATION_REPAIR_SYSTEM_PROMPT,
    CREATION_REPAIR_USER_TEMPLATE,
)
from app.services.novel_creation_agent import (  # noqa: E402
    CREATION_AGENT_TOOLS,
    _system_prompt as creation_agent_system_prompt,
    _tool_schemas as creation_agent_tool_schemas,
)
"""
    text = replace_once(text, old_import, new_import, "mobile export interview import")
    marker = '        "creation": {\n'
    agent_contract = (
        '        "creation_agent": {\n'
        '            "system_template": creation_agent_system_prompt("{{session_id}}"),\n'
        '            "tool_names": sorted(CREATION_AGENT_TOOLS),\n'
        '            "tool_schemas": creation_agent_tool_schemas(),\n'
        '            "max_iterations": 6,\n'
        '        },\n'
    )
    text = replace_once(text, marker, agent_contract + marker, "mobile creation contract")
    for line in (
        '            "interview_max_turns": 8,\n',
        '            "interview_system_prompt": NOVEL_INTERVIEW_SYSTEM_PROMPT,\n',
        '            "interview_user_template": NOVEL_INTERVIEW_USER_TEMPLATE,\n',
    ):
        text = text.replace(line, "")
    write(name, text)


def remove_backend_interview_flow() -> None:
    name = "backend/app/routers/novel_creation.py"
    text = read(name)
    text = text.replace("    advance_novel_creation_interview,\n", "")
    text = remove_between(
        text,
        "class NovelCreationInterviewNextRequest(BaseModel):\n",
        "class NovelCreationReviewRequest(BaseModel):\n",
        "interview request model",
    )
    text = remove_between(
        text,
        '@router.post("/novel-creation/sessions/{session_id}/interview/next")\n',
        '@router.post("/novel-creation/review")\n',
        "interview endpoint",
    )
    text = remove_between(
        text,
        "class RefreshQuestionRequest(BaseModel):\n",
        "class SystemChatRequest(BaseModel):\n",
        "refresh-question endpoint",
    )

    class_start = text.index("class CreationAgentRequest(BaseModel):\n")
    endpoint_start = text.index('@router.post("/novel-creation/agent-turn")\n', class_start)
    new_request = '''class CreationAgentRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=1_000_000)
    model: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    model_route: Literal["pc", "mobile"] = "pc"
    mobile_provider: MobileProviderEnvelope | None = Field(default=None, repr=False, exclude=True)
    local_cli_permission_grant: Literal["chat_only", "creation_agent_once"] = "chat_only"
    local_cli_read_permission_grant: Literal["none", "read_once"] = "none"
    local_cli_read_paths: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def require_mobile_provider_envelope(self) -> "CreationAgentRequest":
        if self.model_route == "mobile" and self.mobile_provider is None:
            raise ValueError("选择手机模型线路时必须提供加密凭据")
        if self.model_route == "pc" and self.mobile_provider is not None:
            raise ValueError("PC 模型线路不能携带手机模型凭据")
        return self


'''
    text = text[:class_start] + new_request + text[endpoint_start:]

    endpoint_start = text.index('@router.post("/novel-creation/agent-turn")\n')
    endpoint_end = text.index('@router.post("/novel-creation/conversation-command")\n', endpoint_start)
    new_endpoint = '''@router.post("/novel-creation/agent-turn")
async def creation_agent_turn(
    payload: CreationAgentRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    session = novel_creation_session_store(db).session(payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    from ..services.novel_creation_agent import run_creation_agent

    request_provider = _resolve_mobile_creation_provider(
        db,
        payload,
        request,
        binding_id=session.id,
    )

    async def run_agent() -> dict[str, Any]:
        return await run_creation_agent(
            db,
            session=session,
            message=payload.message,
            model=payload.model,
            history=payload.history,
            local_cli_write_granted=payload.local_cli_permission_grant == "creation_agent_once",
            local_cli_read_paths=(
                list(payload.local_cli_read_paths)
                if payload.local_cli_read_permission_grant == "read_once" else []
            ),
        )

    if request_provider is None:
        result = await run_agent()
    else:
        from ..modules.model_runtime.application.request_override import use_request_provider
        with use_request_provider(request_provider):
            result = await run_agent()
    return ApiResponse.success(data=result)


'''
    text = text[:endpoint_start] + new_endpoint + text[endpoint_end:]
    write(name, text)

    name = "backend/app/bootstrap/http_security.py"
    text = read(name)
    text = text.replace(
        '    "/api/v1/novel-creation/sessions/{session_id}/interview/next": frozenset({"POST"}),\n',
        '    "/api/v1/novel-creation/agent-turn": frozenset({"POST"}),\n',
    )
    text = text.replace(
        '            path.endswith("/interview/next") or path.endswith("/runs")\n',
        '            path.endswith("/agent-turn") or path.endswith("/runs")\n',
    )
    write(name, text)

    name = "backend/app/services/workspace/tools/novel_creation.py"
    text = read(name)
    text, count = re.subn(
        r"from \.\.\.novel_creation_interview import \([\s\S]*?\)\n",
        "",
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("novel_creation_interview import block not found")
    text = remove_function(text, "advance_novel_creation_interview")
    text = remove_function(text, "refresh_question_options")
    write(name, text)

    name = "backend/app/services/novel_creation_prompting.py"
    text = read(name)
    start = text.index("NOVEL_INTERVIEW_SYSTEM_PROMPT = (")
    end = text.index("COMPACT_CONCEPT_SHAPE", start)
    text = text[:start] + text[end:]
    start = text.index("def build_novel_interview_messages(")
    end = text.index("def build_compact_concept_messages(", start)
    text = text[:start] + text[end:]
    for export in (
        '    "NOVEL_INTERVIEW_SYSTEM_PROMPT",\n',
        '    "NOVEL_INTERVIEW_USER_TEMPLATE",\n',
        '    "build_novel_interview_messages",\n',
    ):
        text = text.replace(export, "")
    write(name, text)

    # Keep the compact-stage regression suite, but drop tests for the deleted
    # dynamic-interview control plane.
    name = "backend/tests/test_novel_creation_compact_flow.py"
    text = read(name)
    text = text.replace(
        "from app.services.workspace.tools.novel_creation import advance_novel_creation_interview, apply_novel_blueprint\n",
        "from app.services.workspace.tools.novel_creation import apply_novel_blueprint\n",
    )
    text = text.replace("from app.services.novel_creation_interview import INTERVIEW_CLI_TIMEOUT_SECONDS\n", "")
    start = text.index("def test_interview_ready_state_never_calls_full_blueprint_generation():\n")
    end = text.index("def test_compact_concept_run_limits_output_and_keeps_legacy_blueprints_empty():\n", start)
    text = text[:start] + text[end:]
    write(name, text)

    for old in (
        "backend/app/services/novel_creation_interview.py",
        "backend/tests/test_novel_creation_interview.py",
    ):
        target = path(old)
        if target.exists():
            target.unlink()


def update_android_direct_api() -> None:
    name = "mobile/android/app/src/main/java/com/siming/mobile/data/network/DirectApi.kt"
    text = read(name)
    old = """        maxOutputTokens: Int = 4_000,
        temperature: Double = 0.3,
    ): DirectAgentTurn {
"""
    new = """        maxOutputTokens: Int = 4_000,
        temperature: Double = 0.3,
        extraBody: JsonObject? = null,
    ): DirectAgentTurn {
"""
    text = replace_once(text, old, new, "DirectApi.agentTurn")
    text = text.replace(
        "responsesAgentPayload(config, messages, tools, toolChoice, maxOutputTokens, temperature)",
        "responsesAgentPayload(config, messages, tools, toolChoice, maxOutputTokens, temperature, extraBody)",
    )
    text = text.replace(
        "chatAgentPayload(config, messages, tools, toolChoice, maxOutputTokens, temperature)",
        "chatAgentPayload(config, messages, tools, toolChoice, maxOutputTokens, temperature, extraBody)",
    )

    # Patch both private payload builders by function name, not by generic
    # parameter sequence, so future formatting changes do not hit the wrong one.
    text, count = re.subn(
        r"(private fun chatAgentPayload\([\s\S]*?maxOutputTokens: Int,\n\s*temperature: Double,)(\n\s*\): JsonObject = buildJsonObject \{)",
        r"\1\n        extraBody: JsonObject?,\2",
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("chatAgentPayload signature not found")
    chat_start = text.index("private fun chatAgentPayload(")
    response_start = text.index("private fun responsesAgentPayload(", chat_start)
    chat = text[chat_start:response_start]
    chat = replace_once(
        chat,
        '        put("stream", false)\n',
        '        put("stream", false)\n        extraBody?.forEach { (key, value) -> put(key, value) }\n',
        "chatAgentPayload body",
    )
    text = text[:chat_start] + chat + text[response_start:]

    text, count = re.subn(
        r"(private fun responsesAgentPayload\([\s\S]*?maxOutputTokens: Int,\n\s*temperature: Double,)(\n\s*\): JsonObject \{)",
        r"\1\n        extraBody: JsonObject?,\2",
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("responsesAgentPayload signature not found")
    response_start = text.index("private fun responsesAgentPayload(")
    response_end = text.index("private fun parseChatAgentTurn", response_start)
    response = text[response_start:response_end]
    response = replace_once(
        response,
        '            put("stream", false)\n',
        '            put("stream", false)\n            extraBody?.forEach { (key, value) -> put(key, value) }\n',
        "responsesAgentPayload body",
    )
    text = text[:response_start] + response + text[response_end:]
    write(name, text)


def remove_android_interview_flow() -> None:
    name = "mobile/android/app/src/main/java/com/siming/mobile/data/creation/MobileCreationAgent.kt"
    text = read(name)
    text = remove_between(text, "    suspend fun interview(\n", "    suspend fun generateStage(\n", "MobileCreationAgent.interview")
    text = remove_between(text, "    private fun withInterview(\n", "    private fun writeStage(\n", "MobileCreationAgent.withInterview")
    marker = "    fun confirmStage(source: JsonObject, stage: String, editedData: JsonObject? = null): JsonObject {"
    helper = '''    internal fun replaceArtifact(
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

'''
    text = replace_once(text, marker, helper + marker, "MobileCreationAgent.replaceArtifact")
    write(name, text)

    name = "mobile/android/app/src/main/java/com/siming/mobile/data/creation/PcCreationPromptContract.kt"
    text = read(name)
    text, count = re.subn(
        r"\n\s*val interviewMaxTurns: Int =[\s\S]*?\n\s*val stageOrder:",
        "\n    val stageOrder:",
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("PcCreationPromptContract interview fields not found")
    text, count = re.subn(
        r"\n\s*fun interviewMessages\([\s\S]*?\n\s*fun conceptMessages\(",
        "\n\n    fun conceptMessages(",
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("PcCreationPromptContract.interviewMessages not found")
    text = text.replace('        val interview = draft.objectValue("interview")\n', "")
    text = text.replace(
        '            put("interview_history", interview["history"] ?: JsonArray(emptyList()))\n            put("interview_reason", interview.string("reason"))',
        '            put("interview_history", draft["agent_history"] ?: JsonArray(emptyList()))\n            put("interview_reason", "")',
    )
    write(name, text)

    for old in (
        "mobile/android/app/src/main/java/com/siming/mobile/data/creation/MobileCreationInterviewReliability.kt",
        "mobile/android/app/src/test/java/com/siming/mobile/data/creation/MobileCreationInterviewReliabilityTest.kt",
    ):
        target = path(old)
        if target.exists():
            target.unlink()

    name = "mobile/android/app/src/main/java/com/siming/mobile/data/network/PcApiPaths.kt"
    text = read(name)
    text = text.replace(
        '    const val NOVEL_CREATION_APPLY = "$NOVEL_CREATION/apply"\n',
        '    const val NOVEL_CREATION_APPLY = "$NOVEL_CREATION/apply"\n    const val NOVEL_CREATION_AGENT_TURN = "$NOVEL_CREATION/agent-turn"\n',
    )
    text, count = re.subn(
        r"\n\s*fun novelCreationInterview\([\s\S]*?\n\s*fun novelCreationRuns",
        "\n\n    fun novelCreationRuns",
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("PcApiPaths.novelCreationInterview not found")
    write(name, text)

    name = "mobile/android/app/src/main/java/com/siming/mobile/data/network/GatewayApi.kt"
    text = read(name)
    text, count = re.subn(
        r"\n\s*suspend fun advanceNovelCreationInterview\([\s\S]*?\n\s*suspend fun startNovelCreationRun",
        "\n\n    suspend fun startNovelCreationRun",
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("GatewayApi.advanceNovelCreationInterview not found")
    marker = "    suspend fun startNovelCreationRun("
    agent_method = '''    suspend fun novelCreationAgentTurn(
        connection: GatewayConnection,
        payload: JsonObject,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.NOVEL_CREATION_AGENT_TURN,
        "POST",
        json.encodeToString(payload),
    ).data

'''
    text = replace_once(text, marker, agent_method + marker, "GatewayApi.novelCreationAgentTurn")
    write(name, text)

    # Update the API path regression test instead of leaving an assertion for
    # the deleted interview route.
    name = "mobile/android/app/src/test/java/com/siming/mobile/data/network/PcApiPathsTest.kt"
    text = read(name)
    text = re.sub(
        r'\s*assertEquals\(\s*"/api/v1/novel-creation/sessions/session-1/interview/next",\s*PcApiPaths\.novelCreationInterview\("session-1"\),\s*\)\n',
        "",
        text,
        count=1,
    )
    if "NOVEL_CREATION_AGENT_TURN" not in text:
        insertion = '        assertEquals("/api/v1/novel-creation/agent-turn", PcApiPaths.NOVEL_CREATION_AGENT_TURN)\n'
        marker = "    }\n"
        pos = text.find(marker, text.find("@Test"))
        if pos < 0:
            raise RuntimeError("PcApiPathsTest test body not found")
        text = text[:pos] + insertion + text[pos:]
    write(name, text)


def migrate_android_repository() -> None:
    name = "mobile/android/app/src/main/java/com/siming/mobile/data/SimingRepository.kt"
    text = read(name)
    text = text.replace(
        "import com.siming.mobile.data.creation.MobileCreationAgent\n",
        "import com.siming.mobile.data.creation.MobileCreationAgent\nimport com.siming.mobile.data.creation.MobileCreationConversationAgent\n",
    )
    marker = "    private val mobileCreationAgent by lazy { MobileCreationAgent(appContext, directApi) }\n"
    addition = marker + '''    private val mobileCreationConversationAgent by lazy {
        MobileCreationConversationAgent(
            context = appContext,
            stageAgent = mobileCreationAgent,
            directApi = directApi,
            persistSession = ::saveCreationSession,
            finalizeSession = { session ->
                saveCreationSession(session)
                val projectId = archiveCreation(session.string("id")) {}
                loadCreationSession(session.string("id")) to projectId
            },
        )
    }
'''
    text = replace_once(text, marker, addition, "mobileCreationConversationAgent")

    start = text.index("    suspend fun advanceCreationInterview(\n")
    end = text.index("    suspend fun archiveCreation(\n", start)
    replacement = '''    suspend fun runCreationAgentTurn(
        sessionId: String,
        message: String,
        onProgress: suspend (String) -> Unit = {},
    ): JsonObject {
        require(message.isNotBlank()) { "请输入你想告诉 AI 的内容" }
        val current = loadCreationSession(sessionId)
        val history = creationAgentHistory(current)
        val userHistory = history + agentHistoryMessage("user", message)
        saveCreationSession(withCreationAgentHistory(current, userHistory))
        val route = creationRoute(current)
        val gatewayExecution = creationHost(current) == CREATION_HOST_GATEWAY
        val updated = when {
            route == CreationExecutionRoute.Pc || gatewayExecution -> {
                val connection = requireConnection()
                val mobileProvider = if (route == CreationExecutionRoute.MobileKey) {
                    mobileProviderPayload(connection, sessionId)
                } else null
                onProgress(
                    if (mobileProvider == null) "PC Creation Agent 正在读取并增量写入…"
                    else "手机 Key 正在驱动 PC Creation Agent…"
                )
                val result = api.novelCreationAgentTurn(
                    connection,
                    buildJsonObject {
                        put("session_id", sessionId)
                        put("message", message)
                        put("history", JsonArray(history.takeLast(12)))
                        put("model_route", if (mobileProvider == null) "pc" else "mobile")
                        mobileProvider?.let { put("mobile_provider", it) }
                    },
                )
                val reply = result.string("reply").ifBlank { "已完成本轮立项工具调用" }
                val fresh = tagCreationRoute(
                    api.getNovelCreationSession(connection, sessionId),
                    route,
                    CREATION_HOST_GATEWAY,
                )
                withCreationAgentHistory(
                    fresh,
                    userHistory + agentHistoryMessage("assistant", reply),
                )
            }
            else -> {
                onProgress("手机 Creation Agent 正在读取资料并执行工具…")
                val result = mobileCreationConversationAgent.run(
                    source = current,
                    message = message,
                    history = history,
                    config = resolvedDirectConfig(),
                    onProgress = onProgress,
                )
                withCreationAgentHistory(
                    result.session,
                    userHistory + agentHistoryMessage("assistant", result.reply),
                )
            }
        }
        saveCreationSession(updated)
        return updated
    }

'''
    text = text[:start] + replacement + text[end:]
    text = re.sub(
        r"\n\s*private fun interviewHistoryWithAnswer\([\s\S]*?(?=\n\s*private fun |\n\s*suspend fun |\n\s*fun )",
        "\n",
        text,
        count=1,
    )
    marker = "    private suspend fun saveCreationSession(session: JsonObject) {"
    helpers = '''    private fun creationAgentHistory(session: JsonObject): List<JsonObject> =
        (session.draft()["agent_history"] as? JsonArray)
            .orEmpty()
            .mapNotNull { it as? JsonObject }

    private fun agentHistoryMessage(role: String, content: String): JsonObject = buildJsonObject {
        put("id", UUID.randomUUID().toString())
        put("role", role)
        put("content", content)
        put("created_at", Instant.now().toString())
    }

    private fun withCreationAgentHistory(session: JsonObject, history: List<JsonObject>): JsonObject {
        val draft = session.draft().toMutableMap()
        draft["agent_history"] = JsonArray(history.takeLast(40))
        return JsonObject(session.toMutableMap().apply { put("draft", JsonObject(draft)) })
    }

'''
    text = replace_once(text, marker, helpers + marker, "creation agent history")
    write(name, text)


def migrate_android_ui() -> None:
    name = "mobile/android/app/src/main/java/com/siming/mobile/ui/MainViewModel.kt"
    text = read(name)
    start = text.index("    fun beginCreation(input: CreationStartInput, route: CreationExecutionRoute) {\n")
    end = text.index("    fun archiveCreation(sessionId: String, onArchived: (String) -> Unit) {\n", start)
    replacement = '''    fun beginCreation(input: CreationStartInput, route: CreationExecutionRoute) {
        launchCreation("正在建立对话式立项会话…") {
            val started = repository.beginCreation(input, route)
            val sessionId = started["id"]?.jsonPrimitive?.contentOrNull
                ?: error("立项草稿缺少 id")
            uiState.value = uiState.value.copy(activeCreationId = sessionId)
            repository.runCreationAgentTurn(sessionId, input.brief) { activity ->
                uiState.value = uiState.value.copy(creationActivity = activity)
            }
            "Creation Agent 已边聊边写入第一轮立项资料"
        }
    }

    fun resumeCreation(sessionId: String) {
        uiState.value = uiState.value.copy(activeCreationId = sessionId, error = null)
    }

    fun closeCreation() {
        uiState.value = uiState.value.copy(activeCreationId = null, creationActivity = "")
    }

    fun sendCreationMessage(sessionId: String, message: String) {
        if (message.isBlank()) return
        launchCreation("Creation Agent 正在处理…") {
            repository.runCreationAgentTurn(sessionId, message) { activity ->
                uiState.value = uiState.value.copy(creationActivity = activity)
            }
            "本轮已完成；确定事实已立即写入结构化立项资料"
        }
    }

'''
    text = text[:start] + replacement + text[end:]
    write(name, text)

    name = "mobile/android/app/src/main/java/com/siming/mobile/ui/CreationScreen.kt"
    text = read(name)
    old = '''        active != null -> CreationWorkspace(
            modifier = modifier,
            session = active,
            stages = stages,
            running = ui.creationRunning,
            activity = ui.creationActivity,
            onBack = viewModel::closeCreation,
            onAnswer = { answer, skip -> viewModel.answerCreationInterview(active.string("id"), answer, skip) },
            onGenerate = { stage, instruction -> viewModel.generateCreationStage(active.string("id"), stage, instruction) },
            onConfirm = { stage, edited -> viewModel.confirmCreationStage(active.string("id"), stage, edited) },
            onArchive = { viewModel.archiveCreation(active.string("id"), onOpenProject) },
            onDiscard = { viewModel.discardCreation(active.string("id")) },
        )'''
    new = '''        active != null -> CreationConversationWorkspace(
            modifier = modifier,
            session = active,
            stages = stages,
            running = ui.creationRunning,
            activity = ui.creationActivity,
            onBack = viewModel::closeCreation,
            onSend = { message -> viewModel.sendCreationMessage(active.string("id"), message) },
            onDiscard = { viewModel.discardCreation(active.string("id")) },
            onOpenProject = onOpenProject,
        )'''
    text = replace_once(text, old, new, "CreationScreen active route")
    replacements = {
        "AI 通过追问，与我一起找到可持续的创意": "AI 边聊边写入资料，与我一起找到可持续的创意",
        "两条线路使用同一套 V3 提示词和建档结构。": "两条线路使用同一套对话式 Creation Agent 提示词、工具和建档结构。",
        "PC 同源提示词": "PC 同源 Agent",
        "接下来 AI 会动态采访 → 生成一套创意方向 → 搭建世界、角色、地点与卷纲 → 你可以先建档，也可以继续生成可选的前三章细纲。": "接下来直接进入对话式立项：AI 每轮先读当前资料，把确定事实立即写入，再问最有价值的下一件事；角色、世界观和大纲不再有强制顺序。",
        "司命会追问真正影响故事的分岔，再把结果变成可编辑、可确认、可正式入库的作品资料。": "司命会像 PC 端一样边聊边读取和写入作品资料；你每确认一个事实，它就立即进入结构化草稿。",
        'listOf("动态采访", "结构化生成", "逐步可撤回", "一键建档")': 'listOf("即时写入", "按需追问", "任意顺序", "一键建档")',
    }
    for old_text, new_text in replacements.items():
        text = text.replace(old_text, new_text)
    write(name, text)


def fix_mobile_conversation_agent() -> None:
    name = "mobile/android/app/src/main/java/com/siming/mobile/data/creation/MobileCreationConversationAgent.kt"
    text = read(name)
    text = text.replace(
        '        var finalReply = ""\n        repeat(contract.maxIterations) { iteration ->',
        '        var finalReply = ""\n        var iteration = 0\n        while (iteration < contract.maxIterations && finalReply.isBlank()) {',
    )
    text = text.replace("                return@repeat\n", "                break\n")
    marker = "            }\n        }\n\n        if (finalReply.isBlank() && toolResults.isNotEmpty()) {"
    if marker in text:
        text = text.replace(
            marker,
            "            }\n            iteration += 1\n        }\n\n        if (finalReply.isBlank() && toolResults.isNotEmpty()) {",
            1,
        )
    text = text.replace(
        '                if (!(validation["ready"] as? JsonPrimitive)?.contentOrNull.toBoolean()) {',
        '                if ((validation["ready"] as? JsonPrimitive)?.contentOrNull?.toBooleanStrictOrNull() != true) {',
    )
    write(name, text)


def add_mobile_contract_test() -> None:
    name = "mobile/android/app/src/test/java/com/siming/mobile/data/creation/PcCreationAgentContractTest.kt"
    write(name, '''package com.siming.mobile.data.creation

import kotlin.test.Test
import kotlin.test.assertTrue

class PcCreationAgentContractTest {
    @Test
    fun currentPcCreationAgentPromptRequiresImmediateIncrementalWrites() {
        val raw = javaClass.classLoader!!.getResource("pc_workspace_prompt_contract.json")!!.readText()
        val contract = PcCreationAgentContract(raw)
        val prompt = contract.systemPrompt("session-test")
        assertTrue("立即增量写入" in prompt)
        assertTrue("不要把数据积攒到“采访结束”后才生成" in prompt)
        assertTrue("patch_creation_artifact" in contract.toolNames)
        assertTrue("generate_creation_artifact" in contract.toolNames)
    }
}
''')


def main() -> None:
    export_creation_agent_contract()
    remove_backend_interview_flow()
    update_android_direct_api()
    remove_android_interview_flow()
    migrate_android_repository()
    migrate_android_ui()
    fix_mobile_conversation_agent()
    add_mobile_contract_test()
    print("creation-agent parity migration applied")


if __name__ == "__main__":
    main()
