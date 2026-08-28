package com.siming.mobile.data

import com.siming.mobile.data.local.ReplicaEntity
import java.io.File
import java.security.MessageDigest
import java.util.UUID
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import java.util.zip.ZipFile
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class ProjectPackageTest {
    @Test
    fun validatesStructurePackageAndUsesBackendCompatibleUuidV5() {
        val fixture = interopFixture()
        val file = buildStructurePackage()
        try {
            val validated = MobileProjectPackageValidator(file).validate()
            val requestKey = UUID.fromString(fixture.string("idempotency_key"))
            val (projectId, replicas) = MobileProjectPackageMaterializer.materialize(
                validated,
                requestKey,
                "手机导入副本",
            )

            assertEquals(fixture.string("expected_project_id"), projectId)
            assertEquals(fixture.string("source_project_id"), validated.sourceProjectId)
            assertEquals(
                (fixture["structure_paths"] as JsonArray).map { (it as JsonPrimitive).content },
                structureCollections.map { "data/$it.jsonl" },
            )
            assertEquals("structure", validated.profile)
            assertEquals(1, replicas.size)
            assertEquals("手机导入副本", replicas.single().payload.string("title"))
            assertFalse(replicas.any { it.entityType == "chapter" })
        } finally {
            file.delete()
        }
    }

    @Test
    fun rejectsUnknownProjectFieldsBeforeCreatingLocalReplicas() {
        val file = buildStructurePackage(extraProjectField = true)
        try {
            val error = assertFailsWith<IllegalArgumentException> {
                MobileProjectPackageValidator(file).validate()
            }
            assertTrue(error.message.orEmpty().contains("未知字段"))
        } finally {
            file.delete()
        }
    }

    @Test
    fun rejectsManifestHashMismatch() {
        val file = buildStructurePackage(corruptProjectHash = true)
        try {
            val error = assertFailsWith<IllegalArgumentException> {
                MobileProjectPackageValidator(file).validate()
            }
            assertTrue(error.message.orEmpty().contains("校验失败"))
        } finally {
            file.delete()
        }
    }

    @Test
    fun localWriterKeepsDraftsInFullAndRemovesAllBodySentinelsFromStructure() {
        val projectId = "33333333-3333-4333-8333-333333333333"
        val bodySentinel = "BODY_SENTINEL_9e9d2d"
        val snapshot = listOf(
            replica(projectId, "project", projectId, """{"id":"$projectId","title":"本机作品"}"""),
            replica(
                projectId,
                "chapter",
                "44444444-4444-4444-8444-444444444444",
                """{"id":"44444444-4444-4444-8444-444444444444","project_id":"$projectId","title":"第一章","content":"$bodySentinel 正文","sort_order":1000}""",
            ),
        )
        val draft = MobilePendingChapterDraft(
            draftId = "55555555-5555-4555-8555-555555555555",
            projectId = projectId,
            title = "未保存草稿",
            content = "DRAFT_SENTINEL_4d4d",
        )
        val full = kotlin.io.path.createTempFile("mobile-full-", PROJECT_PACKAGE_EXTENSION).toFile()
        val structure = kotlin.io.path.createTempFile("mobile-structure-", PROJECT_PACKAGE_EXTENSION).toFile()
        try {
            MobileProjectPackageWriter.write(projectId, snapshot, draft, "full", full)
            MobileProjectPackageWriter.write(projectId, snapshot, draft, "structure", structure)

            val validatedFull = MobileProjectPackageValidator(full).validate()
            val validatedStructure = MobileProjectPackageValidator(structure).validate()
            assertEquals(1, validatedFull.coreRows["chapters"].orEmpty().size)
            assertTrue(archiveText(full).contains(bodySentinel))
            assertTrue(archiveText(full).contains("DRAFT_SENTINEL_4d4d"))
            assertEquals("structure", validatedStructure.profile)
            assertFalse(archiveText(structure).contains(bodySentinel))
            assertFalse(archiveText(structure).contains("DRAFT_SENTINEL_4d4d"))
        } finally {
            full.delete()
            structure.delete()
        }
    }

    @Test
    fun importedPackageRewritePreservesHiddenCollectionsAndUsesLatestProjectReplica() {
        val hiddenSentinel = "HIDDEN_CREATION_SENTINEL_7f81"
        val source = buildStructurePackage(creationSessionSentinel = hiddenSentinel)
        val destination = kotlin.io.path.createTempFile("mobile-rewritten-", PROJECT_PACKAGE_EXTENSION).toFile()
        try {
            val validated = MobileProjectPackageValidator(source).validate()
            val requestKey = UUID.fromString("66666666-6666-4666-8666-666666666666")
            val (projectId, replicas) = MobileProjectPackageMaterializer.materialize(
                validated,
                requestKey,
                "首次导入标题",
            )
            val projectReplica = replicas.single { it.entityType == "project" }
            val latestProject = projectReplica.payload.toMutableMap().apply {
                put("title", JsonPrimitive("离线修改后的标题"))
            }
            val snapshot = listOf(
                replica(projectId, "project", projectId, JsonObject(latestProject).toString()),
            )

            MobileProjectPackageWriter.rewriteImported(
                source = source,
                expectedSha256 = sha256File(source),
                idempotencyKey = requestKey,
                projectId = projectId,
                snapshot = snapshot,
                pendingDraft = null,
                profile = "structure",
                destination = destination,
            )

            val rewritten = MobileProjectPackageValidator(destination).validate()
            assertEquals(projectId, rewritten.sourceProjectId)
            assertEquals("离线修改后的标题", rewritten.sourceProjectTitle)
            assertEquals("离线修改后的标题", rewritten.coreRows.getValue("project").single().string("title"))
            assertTrue(archiveText(destination).contains(hiddenSentinel))
            assertFalse(archiveText(destination).contains("source-project"))
        } finally {
            source.delete()
            destination.delete()
        }
    }

    @Test
    fun importedFullPackageRestoresDraftReplicaAndRewriteOverlaysEdits() {
        val sourceProjectId = "77777777-7777-4777-8777-777777777777"
        val sourceChapterId = "88888888-8888-4888-8888-888888888888"
        val originalBody = "ORIGINAL_BODY_SENTINEL_eb27"
        val editedBody = "EDITED_BODY_SENTINEL_1c84"
        val draftBody = "IMPORTED_DRAFT_SENTINEL_31b5"
        val materialBody = "MATERIAL_BODY_SENTINEL_85cf"
        val sourceBase = kotlin.io.path.createTempFile("mobile-import-source-", PROJECT_PACKAGE_EXTENSION).toFile()
        var source = sourceBase
        val full = kotlin.io.path.createTempFile("mobile-import-full-", PROJECT_PACKAGE_EXTENSION).toFile()
        val structure = kotlin.io.path.createTempFile("mobile-import-structure-", PROJECT_PACKAGE_EXTENSION).toFile()
        try {
            MobileProjectPackageWriter.write(
                sourceProjectId,
                listOf(
                    replica(sourceProjectId, "project", sourceProjectId, """{"id":"$sourceProjectId","title":"来源作品"}"""),
                    replica(
                        sourceProjectId,
                        "chapter",
                        sourceChapterId,
                        """{"id":"$sourceChapterId","project_id":"$sourceProjectId","title":"第一章","content":"$originalBody","sort_order":1000}""",
                    ),
                ),
                MobilePendingChapterDraft(
                    draftId = "99999999-9999-4999-8999-999999999999",
                    projectId = sourceProjectId,
                    title = "导入草稿",
                    content = draftBody,
                ),
                "full",
                sourceBase,
            )
            source = addMaterialToFullPackage(sourceBase, sourceProjectId, materialBody)
            val validated = MobileProjectPackageValidator(source).validate()
            val requestKey = UUID.fromString("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
            val (projectId, materialized) = MobileProjectPackageMaterializer.materialize(
                validated,
                requestKey,
                null,
            )
            assertTrue(materialized.any { it.entityType == "chapter_draft" })

            val snapshot = materialized.map { materializedReplica ->
                val payload = materializedReplica.payload.toMutableMap()
                when (materializedReplica.entityType) {
                    "project" -> payload["title"] = JsonPrimitive("手机端新标题")
                    "chapter" -> payload["content"] = JsonPrimitive(editedBody)
                    "chapter_draft" -> payload["status"] = JsonPrimitive("saved")
                }
                replica(
                    projectId,
                    materializedReplica.entityType,
                    materializedReplica.entityId,
                    JsonObject(payload).toString(),
                )
            }
            MobileProjectPackageWriter.rewriteImported(
                source,
                sha256File(source),
                requestKey,
                projectId,
                snapshot,
                null,
                "full",
                full,
            )
            MobileProjectPackageWriter.rewriteImported(
                source,
                sha256File(source),
                requestKey,
                projectId,
                snapshot,
                null,
                "structure",
                structure,
            )

            val rewrittenFull = MobileProjectPackageValidator(full).validate()
            assertEquals("手机端新标题", rewrittenFull.sourceProjectTitle)
            assertEquals(editedBody, rewrittenFull.coreRows.getValue("chapters").single().string("content"))
            assertTrue(rewrittenFull.coreRows["chapter_drafts"].orEmpty().isEmpty())
            assertFalse(archiveText(full).contains(originalBody))
            assertFalse(archiveText(full).contains(draftBody))
            assertTrue(archiveText(full).contains(materialBody))
            assertFalse(ZipFile(full).use { archive ->
                archive.entries().asSequence().any { it.name.contains("source-material") }
            })
            val rewrittenStructure = MobileProjectPackageValidator(structure).validate()
            assertEquals("structure", rewrittenStructure.profile)
            assertFalse(archiveText(structure).contains(originalBody))
            assertFalse(archiveText(structure).contains(editedBody))
            assertFalse(archiveText(structure).contains(draftBody))
            assertFalse(archiveText(structure).contains(materialBody))
        } finally {
            source.delete()
            sourceBase.delete()
            full.delete()
            structure.delete()
        }
    }

    private fun buildStructurePackage(
        extraProjectField: Boolean = false,
        corruptProjectHash: Boolean = false,
        creationSessionSentinel: String? = null,
    ): File {
        val project = linkedMapOf<String, kotlinx.serialization.json.JsonElement>(
            "id" to JsonPrimitive("source-project"),
            "title" to JsonPrimitive("来源作品"),
            "description" to JsonNull,
            "tags" to JsonNull,
            "narrative_perspective" to JsonNull,
            "writing_style" to JsonNull,
            "forbidden_sentence_patterns" to JsonNull,
            "rhetoric_guidelines" to JsonNull,
            "short_sentences" to JsonNull,
            "custom_style_prompt" to JsonNull,
            "daily_word_goal" to JsonNull,
            "created_at" to JsonPrimitive("2026-08-28T00:00:00Z"),
            "updated_at" to JsonPrimitive("2026-08-28T00:00:00Z"),
        )
        if (extraProjectField) project["unexpected"] = JsonPrimitive("blocked")
        val data = structureCollections.associate { key ->
            val row = when (key) {
                "project" -> JsonObject(project)
                "creation_sessions" -> creationSessionSentinel?.let(::creationSession)
                else -> null
            }
            val bytes = row?.let { (Json.encodeToString(it) + "\n").toByteArray() } ?: ByteArray(0)
            "data/$key.jsonl" to bytes
        }
        val entries = data.map { (path, bytes) ->
            JsonObject(
                mapOf(
                    "path" to JsonPrimitive(path),
                    "media_type" to JsonPrimitive("application/x-ndjson"),
                    "size" to JsonPrimitive(bytes.size),
                    "sha256" to JsonPrimitive(
                        if (corruptProjectHash && path == "data/project.jsonl") "0".repeat(64) else sha256(bytes),
                    ),
                    "records" to JsonPrimitive(if (bytes.isEmpty()) 0 else 1),
                ),
            )
        }
        val manifest = JsonObject(
            mapOf(
                "format" to JsonPrimitive("siming-project-package"),
                "format_version" to JsonPrimitive(1),
                "package_id" to JsonPrimitive("22222222-2222-4222-8222-222222222222"),
                "profile" to JsonPrimitive("structure"),
                "producer" to JsonObject(
                    mapOf(
                        "name" to JsonPrimitive("siming"),
                        "app_version" to JsonPrimitive("test"),
                    ),
                ),
                "exported_at" to JsonPrimitive("2026-08-28T00:00:00Z"),
                "source_project" to JsonObject(
                    mapOf(
                        "id" to JsonPrimitive("source-project"),
                        "title" to JsonPrimitive("来源作品"),
                    ),
                ),
                "entries" to JsonArray(entries),
            ),
        )
        val file = kotlin.io.path.createTempFile("project-package-", PROJECT_PACKAGE_EXTENSION).toFile()
        ZipOutputStream(file.outputStream().buffered()).use { archive ->
            data.forEach { (path, bytes) ->
                archive.putNextEntry(ZipEntry(path))
                archive.write(bytes)
                archive.closeEntry()
            }
            archive.putNextEntry(ZipEntry("manifest.json"))
            archive.write((Json.encodeToString(manifest) + "\n").toByteArray())
            archive.closeEntry()
        }
        return file
    }

    private fun creationSession(sentinel: String): JsonObject = JsonObject(
        mapOf(
            "id" to JsonPrimitive("source-creation-session"),
            "source_project_id" to JsonPrimitive("source-project"),
            "created_project_id" to JsonNull,
            "status" to JsonPrimitive("completed"),
            "mode" to JsonPrimitive("internal_llm"),
            "user_brief" to JsonNull,
            "target_audience" to JsonNull,
            "genre" to JsonNull,
            "platform" to JsonNull,
            "schema_version" to JsonPrimitive(1),
            "current_stage" to JsonNull,
            "revision" to JsonPrimitive(1),
            "review_json" to JsonNull,
            "draft_json" to JsonObject(mapOf("author_note" to JsonPrimitive(sentinel))),
            "checkpoints_json" to JsonNull,
            "created_at" to JsonPrimitive("2026-08-28T00:00:00Z"),
            "updated_at" to JsonNull,
            "completed_at" to JsonNull,
        ),
    )

    private fun addMaterialToFullPackage(source: File, projectId: String, body: String): File {
        val sessionId = "source-material-session"
        val materialId = "source-material"
        val assetPath = "assets/materials/$materialId/reference.md"
        val assetBytes = body.toByteArray()
        val session = JsonObject(
            creationSession("material-session").toMutableMap().apply {
                put("id", JsonPrimitive(sessionId))
                put("source_project_id", JsonPrimitive(projectId))
            },
        )
        val material = JsonObject(
            mapOf(
                "id" to JsonPrimitive(materialId),
                "session_id" to JsonPrimitive(sessionId),
                "filename" to JsonPrimitive("reference.md"),
                "media_type" to JsonPrimitive("text/markdown"),
                "file_sha256" to JsonPrimitive(sha256(assetBytes)),
                "size_bytes" to JsonPrimitive(assetBytes.size),
                "input_revision" to JsonPrimitive(1),
                "text_length" to JsonPrimitive(body.length),
                "selection_json" to JsonNull,
                "created_at" to JsonPrimitive("2026-08-28T00:00:00Z"),
                "updated_at" to JsonPrimitive("2026-08-28T00:00:00Z"),
                "completed_at" to JsonNull,
                "asset_path" to JsonPrimitive(assetPath),
            ),
        )
        val replacements = mapOf(
            "data/creation_sessions.jsonl" to (Json.encodeToString(session) + "\n").toByteArray(),
            "data/creation_materials.jsonl" to (Json.encodeToString(material) + "\n").toByteArray(),
        )
        val originalEntries = linkedMapOf<String, ByteArray>()
        val originalManifest = ZipFile(source).use { archive ->
            val entries = archive.entries()
            var manifest: JsonObject? = null
            while (entries.hasMoreElements()) {
                val entry = entries.nextElement()
                val bytes = archive.getInputStream(entry).use { it.readBytes() }
                if (entry.name == "manifest.json") {
                    manifest = Json.parseToJsonElement(bytes.toString(Charsets.UTF_8)) as JsonObject
                } else {
                    originalEntries[entry.name] = bytes
                }
            }
            requireNotNull(manifest)
        }
        replacements.forEach(originalEntries::put)
        originalEntries[assetPath] = assetBytes
        val declarations = (originalManifest["entries"] as JsonArray).map { raw ->
            val declaration = raw as JsonObject
            val path = declaration.string("path")
            val bytes = originalEntries.getValue(path)
            if (path !in replacements) {
                declaration
            } else {
                JsonObject(
                    declaration.toMutableMap().apply {
                        put("size", JsonPrimitive(bytes.size))
                        put("sha256", JsonPrimitive(sha256(bytes)))
                        put("records", JsonPrimitive(1))
                    },
                )
            }
        }.toMutableList()
        declarations += JsonObject(
            mapOf(
                "path" to JsonPrimitive(assetPath),
                "media_type" to JsonPrimitive("text/markdown"),
                "size" to JsonPrimitive(assetBytes.size),
                "sha256" to JsonPrimitive(sha256(assetBytes)),
                "records" to JsonPrimitive(1),
            ),
        )
        val manifest = JsonObject(
            originalManifest.toMutableMap().apply { put("entries", JsonArray(declarations)) },
        )
        val destination = kotlin.io.path.createTempFile("mobile-import-material-", PROJECT_PACKAGE_EXTENSION).toFile()
        ZipOutputStream(destination.outputStream().buffered()).use { archive ->
            originalEntries.forEach { (path, bytes) ->
                archive.putNextEntry(ZipEntry(path))
                archive.write(bytes)
                archive.closeEntry()
            }
            archive.putNextEntry(ZipEntry("manifest.json"))
            archive.write((Json.encodeToString(manifest) + "\n").toByteArray())
            archive.closeEntry()
        }
        return destination
    }

    private fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(bytes)
        .joinToString("") { "%02x".format(it) }

    private fun interopFixture(): JsonObject {
        val stream = requireNotNull(javaClass.classLoader?.getResourceAsStream("project-package-v1-interop.json")) {
            "共享项目包互操作夹具不存在"
        }
        return stream.bufferedReader(Charsets.UTF_8).use { reader ->
            Json.parseToJsonElement(reader.readText()) as JsonObject
        }
    }

    private fun replica(projectId: String, entityType: String, entityId: String, payload: String) = ReplicaEntity(
        key = ReplicaEntity.key(projectId, entityType, entityId),
        projectId = projectId,
        entityType = entityType,
        entityId = entityId,
        revision = 0,
        operation = "upsert",
        payloadJson = payload,
        contentHash = "test",
        serverModifiedAt = "2026-08-28T00:00:00Z",
    )

    private fun archiveText(file: File): String = ZipFile(file).use { archive ->
        buildString {
            val entries = archive.entries()
            while (entries.hasMoreElements()) {
                val entry = entries.nextElement()
                archive.getInputStream(entry).bufferedReader().use { append(it.readText()) }
            }
        }
    }

    private fun JsonObject.string(name: String): String =
        (get(name) as? JsonPrimitive)?.content.orEmpty()

    private companion object {
        val structureCollections = listOf(
            "project",
            "creation_sessions",
            "creation_entities",
            "outline_nodes",
            "characters",
            "character_ai_configs",
            "character_aliases",
            "character_relationships",
            "worldbuilding_entries",
            "worldbuilding_relations",
            "outline_characters",
        )
    }
}
