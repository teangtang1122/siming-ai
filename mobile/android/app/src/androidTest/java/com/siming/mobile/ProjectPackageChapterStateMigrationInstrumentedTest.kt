package com.siming.mobile

import androidx.room.Room
import androidx.sqlite.db.SupportSQLiteDatabase
import androidx.sqlite.db.SupportSQLiteOpenHelper
import androidx.sqlite.db.framework.FrameworkSQLiteOpenHelperFactory
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.siming.mobile.data.agent.mobileCatalogingBlockReason
import com.siming.mobile.data.agent.mobileChapterWritingState
import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.local.SimingDatabase
import java.util.UUID
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ProjectPackageChapterStateMigrationInstrumentedTest {
    @Test
    fun upgradingAnImportedLibraryRepairsOnlyMissingStateAndKeepsAuthorEdits() = runBlocking {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val name = "package-state-upgrade-${UUID.randomUUID()}.db"
        val schema = instrumentation.context.assets
            .open("com.siming.mobile.data.local.SimingDatabase/3.json")
            .bufferedReader().use { Json.parseToJsonElement(it.readText()).jsonObject.getValue("database").jsonObject }
        val old = FrameworkSQLiteOpenHelperFactory().create(
            SupportSQLiteOpenHelper.Configuration.builder(context).name(name)
                .callback(object : SupportSQLiteOpenHelper.Callback(3) {
                    override fun onCreate(db: SupportSQLiteDatabase) {
                        for (value in schema["entities"] as JsonArray) {
                            val entity = value.jsonObject
                            val table = (entity.getValue("tableName") as JsonPrimitive).content
                            db.execSQL((entity.getValue("createSql") as JsonPrimitive).content.replace("\${TABLE_NAME}", table))
                            for (index in entity["indices"] as JsonArray) {
                                db.execSQL((index.jsonObject.getValue("createSql") as JsonPrimitive).content.replace("\${TABLE_NAME}", table))
                            }
                        }
                        for (query in schema["setupQueries"] as JsonArray) db.execSQL((query as JsonPrimitive).content)
                    }
                    override fun onUpgrade(db: SupportSQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit
                }).build(),
        )
        val db = old.writableDatabase
        db.execSQL("INSERT INTO project_packages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", arrayOf(
            "request", "package", "p1", "fixture.siming-project", "/unused", "hash", "full", null,
            "synced", null, 1, 1,
        ))
        fun insert(type: String, id: String, payload: String, dirty: Boolean = false, project: String = "p1") {
            db.execSQL("INSERT INTO replica_entities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", arrayOf(
                ReplicaEntity.key(project, type, id), project, type, id, 7, "upsert", payload, "old-hash",
                "2026-09-01T00:00:00Z", if (dirty) 1 else 0, 0, 123,
            ))
        }
        fun chapter(id: String, prose: String, known: Boolean? = null, project: String = "p1") {
            val payload = JsonObject(mapOf(
                "_record_type" to JsonPrimitive("chapter"), "id" to JsonPrimitive(id),
                "project_id" to JsonPrimitive(project), "content" to JsonPrimitive(prose),
                "title" to JsonPrimitive(id), "current_version" to JsonPrimitive(2),
            ) + (known?.let { mapOf("cataloging_required" to JsonPrimitive(it)) } ?: emptyMap()))
            insert("chapter", id, payload.toString(), dirty = id == "edited" || id == "pending", project = project)
        }
        fun evidence(id: String) {
            insert("chapter_version", "$id-snapshot", """{"_record_type":"chapter_snapshot","chapter_id":"$id","version_number":2,"content":"Original prose","created_at":"2026-09-01T00:00:00Z"}""")
            insert("summary", "$id-summary", """{"_record_type":"chapter_summary","chapter_id":"$id","summary_text":"Archived","updated_at":"2026-09-01T00:00:01Z"}""")
        }
        chapter("complete", "Original prose")
        evidence("complete")
        chapter("edited", "Author edited prose")
        evidence("edited")
        chapter("pending", "Original prose", true)
        evidence("pending")
        chapter("known-complete", "Prose", false)
        chapter("foreign", "Original prose", project = "p2")
        db.execSQL("INSERT INTO sync_outbox (mutationId, projectId, entityType, entityId, operation, baseRevision, " +
            "payloadJson, clientModifiedAt, state, sentPayloadHash, lastError, createdAt) " +
            "VALUES ('edit', 'p1', 'chapter', 'edited', 'upsert', 7, '{}', 'saved-time', 'pending', NULL, NULL, 123)")
        old.close()
        val upgraded = Room.databaseBuilder(context, SimingDatabase::class.java, name)
            .addMigrations(SimingDatabase.MIGRATION_3_4, SimingDatabase.MIGRATION_4_5).build()
        try {
            val snapshot = upgraded.dao().projectSnapshot("p1")
            fun record(id: String) = snapshot.single { it.entityId == id }
            fun payload(id: String) = Json.parseToJsonElement(record(id).payloadJson!!).jsonObject
            assertEquals(5, upgraded.openHelper.readableDatabase.version)
            assertEquals(JsonPrimitive(false), payload("complete")["cataloging_required"])
            assertEquals(JsonPrimitive(true), payload("edited")["cataloging_required"])
            assertEquals(JsonPrimitive(true), payload("pending")["cataloging_required"])
            assertEquals(JsonPrimitive(false), payload("known-complete")["cataloging_required"])
            assertEquals(JsonPrimitive("Author edited prose"), payload("edited")["content"])
            assertEquals(JsonPrimitive(3), payload("edited")["current_version"])
            assertEquals(JsonPrimitive(2), payload("complete")["current_version"])
            assertEquals(JsonPrimitive(2), payload("pending")["current_version"])
            assertTrue(record("edited").dirty)
            assertFalse(record("complete").dirty)
            assertEquals(7L, record("complete").revision)
            assertEquals(123L, record("complete").localModifiedAt)
            assertTrue(record("complete").contentHash != "old-hash")
            assertEquals("old-hash", record("pending").contentHash)
            assertEquals("edit", upgraded.dao().pendingMutations(10).single().mutationId)
            val foreign = upgraded.dao().projectSnapshot("p2").single()
            assertFalse(Json.parseToJsonElement(foreign.payloadJson!!).jsonObject.containsKey("cataloging_required"))
            assertNull(mobileCatalogingBlockReason(mobileChapterWritingState("p1", listOf(record("complete")), null), ""))
            assertNotNull(mobileCatalogingBlockReason(mobileChapterWritingState("p1", snapshot, null), ""))
        } finally {
            upgraded.close()
            context.deleteDatabase(name)
        }
    }
}
