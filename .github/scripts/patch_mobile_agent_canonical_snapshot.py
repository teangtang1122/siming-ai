from pathlib import Path

path = Path("mobile/android/app/src/main/java/com/siming/mobile/data/agent/MobileWorkspaceAgent.kt")
text = path.read_text(encoding="utf-8")
old_import = "import com.siming.mobile.data.local.orderReplicaEntities\n"
new_import = "import com.siming.mobile.data.local.orderReplicaEntities\nimport com.siming.mobile.data.local.primaryAuthoringSnapshot\n"
if text.count(old_import) != 1:
    raise RuntimeError(f"import anchor changed: {text.count(old_import)} matches")
text = text.replace(old_import, new_import, 1)
old = '''    private suspend fun records(projectId: String, entityType: String? = null): List<LocalRecord> {
        val matching = loadSnapshot(projectId).asSequence()
            .filter { it.operation == "upsert" && (entityType == null || it.entityType == entityType) }
            .toList()
        val ordered = entityType?.let { orderReplicaEntities(it, matching) } ?: matching
        return ordered.asSequence()'''
new = '''    private suspend fun records(projectId: String, entityType: String? = null): List<LocalRecord> {
        val snapshot = loadSnapshot(projectId).filter { it.operation == "upsert" }
        val matching = if (entityType == null) {
            primaryAuthoringSnapshot(snapshot)
        } else {
            snapshot.filter { it.entityType == entityType }
        }
        val ordered = entityType?.let { orderReplicaEntities(it, matching) } ?: matching
        return ordered.asSequence()'''
if text.count(old) != 1:
    raise RuntimeError(f"records anchor changed: {text.count(old)} matches")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
