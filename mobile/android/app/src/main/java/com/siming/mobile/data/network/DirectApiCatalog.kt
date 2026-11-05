package com.siming.mobile.data.network

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

@Serializable
data class DirectApiProfile(val id: String, val config: DirectApiConfig)

/** One encrypted registry. Task provider IDs and their model overrides are validated together. */
@Serializable
data class DirectApiCatalog(
    val profiles: List<DirectApiProfile> = emptyList(),
    val defaultProfileId: String? = null,
    val taskProfileIds: Map<String, String> = emptyMap(),
) {
    init {
        require(profiles.all { it.id.isNotBlank() } && profiles.map { it.id }.distinct().size == profiles.size)
        require(if (profiles.isEmpty()) defaultProfileId == null else profiles.any { it.id == defaultProfileId })
        require(taskProfileIds.all { (task, id) -> task in DirectApiConfig.taskModelLabels && profiles.any { it.id == id } })
        require(profiles.all { profile -> profile.config.taskModels.keys.all { taskProfileIds[it] == profile.id } })
    }
    fun profile(id: String): DirectApiProfile = profiles.singleOrNull { it.id == id } ?: error("API 配置不存在")
    fun defaultConfig(): DirectApiConfig? = defaultProfileId?.let { profile(it).config }
    fun resolve(task: String): DirectApiConfig? = (taskProfileIds[task] ?: defaultProfileId)
        ?.let { profile(it).config.forTask(task) }
    fun summaries(): List<DirectApiSummary> = profiles.map { it.config.summary().copy(id = it.id) }

    fun upsert(id: String, config: DirectApiConfig): DirectApiCatalog {
        // The API editor changes the endpoint/default model, not global task bindings.
        val existing = profiles.firstOrNull { it.id == id }
        val entry = DirectApiProfile(id, config.copy(taskModels = existing?.config?.taskModels.orEmpty()))
        return copy(profiles = if (existing == null) profiles + entry else profiles.map { if (it.id == id) entry else it },
            defaultProfileId = defaultProfileId ?: id)
    }
    fun selectDefault(id: String): DirectApiCatalog { profile(id); return copy(defaultProfileId = id) }
    fun bind(task: String, id: String?, model: String? = null): DirectApiCatalog {
        require(task in DirectApiConfig.taskModelLabels)
        if (id != null) {
            val config = profile(id).config
            require(!model.isNullOrBlank() && model in (config.availableModels + config.model)) { "请选择此 API 的模型" }
        }
        val bindings = taskProfileIds.toMutableMap().apply { if (id == null) remove(task) else put(task, id) }
        return copy(taskProfileIds = bindings, profiles = profiles.map { entry ->
            val models = entry.config.taskModels.toMutableMap().apply {
                remove(task)
                if (entry.id == id) put(task, requireNotNull(model))
            }
            entry.copy(config = entry.config.copy(taskModels = models))
        })
    }
    fun remove(id: String): DirectApiCatalog {
        profile(id)
        require(id !in taskProfileIds.values) { "此 API 仍被任务使用，请先调整任务绑定" }
        require(id != defaultProfileId || profiles.size == 1) { "请先将另一组 API 设为默认，再删除此配置" }
        return copy(profiles = profiles.filterNot { it.id == id }, defaultProfileId = defaultProfileId.takeUnless { it == id })
    }
}

/** Isolated one-time conversion of the previously stored single-config payload. */
object LegacyDirectApiMigration {
    fun convert(raw: String, json: Json): DirectApiCatalog {
        val config = json.decodeFromString<DirectApiConfig>(raw)
        val id = "legacy-api"
        return DirectApiCatalog(listOf(DirectApiProfile(id, config)), id,
            config.taskModels.keys.associateWith { id })
    }
}


internal fun retainedDirectApiKey(baseUrl: String, apiKey: String, existing: DirectApiConfig?): String {
    if (apiKey.isNotBlank()) return apiKey.trim()
    require(existing != null && baseUrl.trim().trimEnd('/') == existing.baseUrl.trim().trimEnd('/')) {
        "新增 API 或修改地址时请填写对应的 API Key"
    }
    return existing.apiKey
}
