from pathlib import Path

path = Path("mobile/android/app/src/main/java/com/siming/mobile/ui/MainViewModel.kt")
text = path.read_text(encoding="utf-8")
text = text.replace(
    "import kotlinx.serialization.json.Json\nimport kotlinx.serialization.json.JsonElement\n",
    "import kotlinx.serialization.json.Json\nimport kotlinx.serialization.json.JsonArray\nimport kotlinx.serialization.json.JsonElement\n",
    1,
)
anchor = '''    fun syncNow() = launchActivity("正在先上传本机修改，再拉取新修订…") {\n        repository.syncNow()\n        "同步完成"\n    }\n\n'''
methods = '''    fun syncNow() = launchActivity("正在先上传本机修改，再拉取新修订…") {\n        repository.syncNow()\n        "同步完成"\n    }\n\n    suspend fun reorderChapters(projectId: String, chapterIds: List<String>): JsonObject =\n        repository.reorderChapters(projectId, chapterIds)\n\n    suspend fun chapterSnapshots(projectId: String, chapterId: String): JsonObject =\n        repository.listChapterSnapshots(projectId, chapterId)\n\n    suspend fun chapterSnapshot(\n        projectId: String,\n        chapterId: String,\n        snapshotId: String,\n    ): JsonObject = repository.getChapterSnapshot(projectId, chapterId, snapshotId)\n\n    suspend fun chapterSnapshotDiff(\n        projectId: String,\n        chapterId: String,\n        fromSnapshotId: String,\n        toSnapshotId: String,\n    ): JsonObject = repository.diffChapterSnapshots(\n        projectId,\n        chapterId,\n        fromSnapshotId,\n        toSnapshotId,\n    )\n\n    suspend fun restoreChapterSnapshot(\n        projectId: String,\n        chapterId: String,\n        snapshotId: String,\n    ): JsonObject = repository.restoreChapterSnapshot(projectId, chapterId, snapshotId)\n\n    suspend fun characterRelationshipNetwork(projectId: String): JsonObject =\n        repository.characterRelationshipNetwork(projectId)\n\n    suspend fun replaceCharacterRelationships(\n        projectId: String,\n        characterId: String,\n        relationships: JsonArray,\n    ): JsonObject = repository.replaceCharacterRelationships(\n        projectId,\n        characterId,\n        relationships,\n    )\n\n    suspend fun characterAiConfig(projectId: String, characterId: String): JsonObject =\n        repository.characterAiConfig(projectId, characterId)\n\n    suspend fun updateCharacterAiConfig(\n        projectId: String,\n        characterId: String,\n        payload: JsonObject,\n    ): JsonObject = repository.updateCharacterAiConfig(projectId, characterId, payload)\n\n    suspend fun characterVersions(projectId: String, characterId: String): JsonObject =\n        repository.characterVersions(projectId, characterId)\n\n    suspend fun characterVersion(\n        projectId: String,\n        characterId: String,\n        versionId: String,\n    ): JsonObject = repository.characterVersion(projectId, characterId, versionId)\n\n'''
if anchor not in text:
    raise RuntimeError("syncNow anchor not found")
text = text.replace(anchor, methods, 1)
notice_anchor = '''    fun reportError(message: String) {\n        uiState.value = uiState.value.copy(error = message)\n    }\n\n'''
notice = '''    fun reportError(message: String) {\n        uiState.value = uiState.value.copy(error = message)\n    }\n\n    fun reportNotice(message: String) {\n        uiState.value = uiState.value.copy(notice = message)\n    }\n\n'''
if notice_anchor not in text:
    raise RuntimeError("reportError anchor not found")
text = text.replace(notice_anchor, notice, 1)
path.write_text(text, encoding="utf-8")
