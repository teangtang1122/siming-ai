package com.siming.mobile.data.network

/**
 * Canonical PC API paths used by the Android client.
 *
 * Business operations must use these routes instead of inventing Android-only
 * write endpoints. Pairing and offline synchronization remain Gateway-specific.
 */
internal object PcApiPaths {
    const val RUNTIME_CAPABILITIES = "/api/v1/runtime/capabilities"
    const val PROJECTS = "/api/v1/projects"
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
    const val NOVEL_CREATION_APPLY = "$NOVEL_CREATION/apply"
    const val NOVEL_CREATION_AGENT_TURN = "$NOVEL_CREATION/agent-turn"

    fun project(projectId: String): String = "$PROJECTS/${segment(projectId)}"

    fun authoringCollection(projectId: String, entityType: String): String = when (entityType) {
        "chapter" -> "${project(projectId)}/chapters"
        "outline" -> "${project(projectId)}/outline"
        "character" -> "${project(projectId)}/characters"
        "world" -> "${project(projectId)}/worldbuilding"
        else -> error("PC API 暂不支持资料类型：$entityType")
    }

    fun authoringItem(projectId: String, entityType: String, entityId: String): String = when (entityType) {
        "project" -> project(projectId)
        "chapter" -> "${authoringCollection(projectId, entityType)}/${segment(entityId)}"
        "outline" -> "${authoringCollection(projectId, entityType)}/${segment(entityId)}"
        "character" -> "${authoringCollection(projectId, entityType)}/${segment(entityId)}"
        "world" -> "${authoringCollection(projectId, entityType)}/${segment(entityId)}"
        else -> error("PC API 暂不支持资料类型：$entityType")
    }

    fun assistantStream(projectId: String): String =
        "${project(projectId)}/ai/workspace-assistant/stream"

    fun novelCreationSession(sessionId: String): String =
        "$NOVEL_CREATION_SESSIONS/${segment(sessionId)}"

    fun novelCreationRuns(sessionId: String): String =
        "${novelCreationSession(sessionId)}/runs"

    fun novelCreationRun(runId: String): String =
        "$NOVEL_CREATION/runs/${segment(runId)}"

    fun novelCreationStageConfirm(sessionId: String, stage: String): String =
        "${novelCreationSession(sessionId)}/stages/${segment(stage)}/confirm"

    fun novelCreationStage(sessionId: String, stage: String): String =
        "${novelCreationSession(sessionId)}/stages/${segment(stage)}"

    fun narrativeGovernanceItems(projectId: String): String =
        "${project(projectId)}/narrative-governance/items"

    fun narrativeGovernanceStatus(projectId: String, itemType: String, itemId: String): String =
        "${project(projectId)}/narrative-governance/items/${segment(itemType)}/${segment(itemId)}"

    fun conflictResolution(conflictId: String): String =
        "$SYNC_CONFLICTS/${segment(conflictId)}/resolve"

    private fun segment(value: String): String {
        require(value.matches(SEGMENT_PATTERN)) { "API 路径参数格式无效" }
        return value
    }

    private val SEGMENT_PATTERN = Regex("[A-Za-z0-9._:-]{1,64}")
}
