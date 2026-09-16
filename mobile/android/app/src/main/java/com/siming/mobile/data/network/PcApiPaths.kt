package com.siming.mobile.data.network

/**
 * Canonical PC API paths used by the Android client.
 *
 * Optional remote model sessions and cross-device synchronization use these
 * routes. Authoring and document export execute on the phone.
 */
internal object PcApiPaths {
    const val RUNTIME_CAPABILITIES = "/api/v1/runtime/capabilities"
    const val CONTEXT_TRACES = "/api/v1/context-traces"
    fun contextTrace(suffix: String): String = CONTEXT_TRACES + if (suffix.isBlank()) "" else "/$suffix"
    const val PROJECTS = "/api/v1/projects"
    const val PROJECT_PACKAGE_IMPORT = "/api/v1/projects/project-package/import"
    const val PAIRING_COMPLETE = "/api/v1/pairing/complete"
    const val AUTH_REFRESH = "/api/v1/auth/refresh"
    const val SYNC_PROJECTS = "/api/v1/sync/projects"
    const val SYNC_BOOTSTRAP = "/api/v1/sync/bootstrap"
    const val SYNC_PUSH = "/api/v1/sync/push"
    const val SYNC_PULL = "/api/v1/sync/pull"
    const val SYNC_CONFLICTS = "/api/v1/sync/conflicts"
    const val DEVICES_ME = "/api/v1/devices/me"
    const val NOVEL_CREATION = "/api/v1/novel-creation"
    const val NOVEL_CREATION_PRESETS = "$NOVEL_CREATION/presets"
    const val NOVEL_CREATION_SESSIONS = "$NOVEL_CREATION/sessions"
    const val NOVEL_CREATION_START = "$NOVEL_CREATION/start"
    const val NOVEL_CREATION_FINALIZE = "$NOVEL_CREATION/finalize"
    const val NOVEL_CREATION_AGENT_TURN = "$NOVEL_CREATION/agent-turn"

    fun project(projectId: String): String = "$PROJECTS/${segment(projectId)}"

    fun pendingChapterDraft(projectId: String): String =
        "${project(projectId)}/chapter-drafts/pending"

    fun catalogingMobileCommit(projectId: String): String = "${project(projectId)}/cataloging/mobile-commit"

    fun catalogingJob(projectId: String, jobId: String): String =
        "${project(projectId)}/cataloging/${segment(jobId)}"

    fun catalogingCancel(projectId: String, jobId: String): String =
        "${catalogingJob(projectId, jobId)}/cancel"

    fun assistantStream(projectId: String): String =
        "${project(projectId)}/ai/workspace-assistant/stream"

    fun assistantConversations(projectId: String): String =
        "${project(projectId)}/ai/assistant/conversations"

    fun assistantTranscriptImport(projectId: String): String =
        "${assistantConversations(projectId)}/transcript-import"

    fun assistantConversation(projectId: String, conversationId: String): String =
        "${assistantConversations(projectId)}/${segment(conversationId)}"

    fun assistantContextState(projectId: String, conversationId: String): String =
        "${assistantConversation(projectId, conversationId)}/context-state"

    fun assistantCheckpoint(projectId: String, conversationId: String, checkpointId: String): String =
        "${assistantConversation(projectId, conversationId)}/checkpoints/${segment(checkpointId)}"

    fun assistantRuns(projectId: String): String =
        "${project(projectId)}/ai/assistant/runs"

    fun assistantRun(projectId: String, runId: String): String =
        "${assistantRuns(projectId)}/${segment(runId)}"

    fun assistantRunCancel(projectId: String, runId: String): String =
        "${assistantRun(projectId, runId)}/cancel"

    fun novelCreationSession(sessionId: String): String =
        "$NOVEL_CREATION_SESSIONS/${segment(sessionId)}"

    fun novelCreationConversation(sessionId: String, conversationId: String): String =
        "${novelCreationSession(sessionId)}/conversations/${segment(conversationId)}"

    fun novelCreationContextState(sessionId: String, conversationId: String): String =
        "${novelCreationConversation(sessionId, conversationId)}/context-state"

    fun novelCreationCheckpoint(
        sessionId: String,
        conversationId: String,
        checkpointId: String,
    ): String =
        "${novelCreationConversation(sessionId, conversationId)}/checkpoints/${segment(checkpointId)}"

    fun novelCreationRuns(sessionId: String): String =
        "${novelCreationSession(sessionId)}/runs"

    fun novelCreationRun(runId: String): String =
        "$NOVEL_CREATION/runs/${segment(runId)}"

    fun novelCreationStageConfirm(sessionId: String, stage: String): String =
        "${novelCreationSession(sessionId)}/stages/${segment(stage)}/confirm"

    fun novelCreationStage(sessionId: String, stage: String): String =
        "${novelCreationSession(sessionId)}/stages/${segment(stage)}"

    fun conflictResolution(conflictId: String): String =
        "$SYNC_CONFLICTS/${segment(conflictId)}/resolve"

    private fun segment(value: String): String {
        require(value.matches(SEGMENT_PATTERN)) { "API 路径参数格式无效" }
        return value
    }

    private val SEGMENT_PATTERN = Regex("[A-Za-z0-9._:-]{1,64}")
}
