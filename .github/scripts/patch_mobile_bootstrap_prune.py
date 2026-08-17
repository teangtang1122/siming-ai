from pathlib import Path

path = Path("mobile/android/app/src/main/java/com/siming/mobile/data/SimingRepository.kt")
text = path.read_text(encoding="utf-8")
old = '''        database.withTransaction {
            for (snapshot in response.entities) {'''
new = '''        database.withTransaction {
            // bootstrap is a full authoritative snapshot for enabled projects.
            // Remove only clean/non-conflicted replicas first so stale rows
            // from older mobile schemas disappear, while offline edits and
            // conflict branches remain available for upload/resolution.
            projectIds.forEach { projectId ->
                dao.deleteCleanProjectReplicas(projectId)
            }
            for (snapshot in response.entities) {'''
if text.count(old) != 1:
    raise RuntimeError(f"bootstrap anchor changed: {text.count(old)} matches")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
