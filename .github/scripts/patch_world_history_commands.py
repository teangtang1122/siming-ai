from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


# Paths
path = Path("mobile/android/app/src/main/java/com/siming/mobile/data/network/PcApiPaths.kt")
text = path.read_text(encoding="utf-8")
anchor = '''    fun characterVersion(projectId: String, characterId: String, versionId: String): String =\n        "${characterVersions(projectId, characterId)}/${segment(versionId)}"\n\n'''
addition = anchor + '''    fun worldVersions(projectId: String, entryId: String): String =\n        "${authoringItem(projectId, "world", entryId)}/versions"\n\n    fun worldTimeline(projectId: String, entryId: String): String =\n        "${authoringItem(projectId, "world", entryId)}/timeline"\n\n'''
text = replace_once(text, anchor, addition, "world paths")
path.write_text(text, encoding="utf-8")

# Gateway API
path = Path("mobile/android/app/src/main/java/com/siming/mobile/data/network/GatewayApi.kt")
text = path.read_text(encoding="utf-8")
anchor = '''    suspend fun listNovelCreationSessions(connection: GatewayConnection): List<JsonObject> {\n'''
methods = '''    suspend fun listWorldVersions(\n        connection: GatewayConnection,\n        projectId: String,\n        entryId: String,\n    ): JsonObject = request<ApiEnvelope<JsonObject>>(\n        connection.baseUrl,\n        PcApiPaths.worldVersions(projectId, entryId),\n    ).data\n\n    suspend fun listWorldTimeline(\n        connection: GatewayConnection,\n        projectId: String,\n        entryId: String,\n    ): JsonObject = request<ApiEnvelope<JsonObject>>(\n        connection.baseUrl,\n        PcApiPaths.worldTimeline(projectId, entryId),\n    ).data\n\n'''
if anchor not in text:
    raise RuntimeError("GatewayApi anchor missing")
text = text.replace(anchor, methods + anchor, 1)
path.write_text(text, encoding="utf-8")

# Repository
path = Path("mobile/android/app/src/main/java/com/siming/mobile/data/SimingRepository.kt")
text = path.read_text(encoding="utf-8")
anchor = '''    suspend fun characterVersion(\n        projectId: String,\n        characterId: String,\n        versionId: String,\n    ): JsonObject = api.getCharacterVersion(\n        requireConnection(),\n        projectId,\n        characterId,\n        versionId,\n    )\n\n'''
addition = anchor + '''    suspend fun worldVersions(projectId: String, entryId: String): JsonObject =\n        api.listWorldVersions(requireConnection(), projectId, entryId)\n\n    suspend fun worldTimeline(projectId: String, entryId: String): JsonObject =\n        api.listWorldTimeline(requireConnection(), projectId, entryId)\n\n'''
text = replace_once(text, anchor, addition, "repository world history")
path.write_text(text, encoding="utf-8")

# ViewModel
path = Path("mobile/android/app/src/main/java/com/siming/mobile/ui/MainViewModel.kt")
text = path.read_text(encoding="utf-8")
anchor = '''    suspend fun characterVersion(\n        projectId: String,\n        characterId: String,\n        versionId: String,\n    ): JsonObject = repository.characterVersion(projectId, characterId, versionId)\n\n'''
addition = anchor + '''    suspend fun worldVersions(projectId: String, entryId: String): JsonObject =\n        repository.worldVersions(projectId, entryId)\n\n    suspend fun worldTimeline(projectId: String, entryId: String): JsonObject =\n        repository.worldTimeline(projectId, entryId)\n\n'''
text = replace_once(text, anchor, addition, "ViewModel world history")
path.write_text(text, encoding="utf-8")
