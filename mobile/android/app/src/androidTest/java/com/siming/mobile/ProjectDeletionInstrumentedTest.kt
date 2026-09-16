package com.siming.mobile

import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.siming.mobile.data.MobileProjectPackageFile
import com.siming.mobile.data.MobileProjectPackageWriter
import com.siming.mobile.data.SimingRepository
import com.siming.mobile.data.sha256File
import com.siming.mobile.data.local.GatewayConnection
import com.siming.mobile.data.local.LocalConflict
import com.siming.mobile.data.local.OutboxMutation
import com.siming.mobile.data.local.ProjectDeletionResult
import com.siming.mobile.data.local.ProjectSyncStatus
import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.local.SimingDatabase
import com.siming.mobile.data.local.syncStatus
import java.io.File
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ProjectDeletionInstrumentedTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    private fun project(id: String, revision: Long = 0, dirty: Boolean = false) = ReplicaEntity(
        key = ReplicaEntity.key(id, "project", id), projectId = id, entityType = "project", entityId = id,
        revision = revision, operation = "upsert", payloadJson = """{"_record_type":"project","id":"$id","title":"Deletion fixture"}""",
        contentHash = "hash", serverModifiedAt = "2026-09-14T00:00:00Z", dirty = dirty,
    )

    @Test
    fun anOfflineImportCanBeDeletedWithItsArchiveAndQueuedEditsOnly() = runBlocking {
        val db = Room.inMemoryDatabaseBuilder(context, SimingDatabase::class.java).build()
        val source = File.createTempFile("delete-import-", ".siming-project", context.cacheDir)
        var retained: File? = null
        try {
            val dao = db.dao()
            val repository = SimingRepository(context, db)
            MobileProjectPackageWriter.write("source", listOf(project("source")), null, "full", source)
            val result = repository.importProjectPackage(MobileProjectPackageFile(
                source.name, source, source.length(), sha256File(source),
            ))
            val id = result.projectId
            assertFalse(result.remote)
            val initial = repository.projects.first().single()
            assertFalse(initial.project.dirty)
            assertEquals(0L, initial.project.revision)
            assertEquals(ProjectSyncStatus.LOCAL_ONLY, initial.syncStatus)
            retained = File(dao.projectPackage(id)!!.localFilePath)
            assertTrue(retained.isFile)
            dao.saveMutation(OutboxMutation("edit", id, "chapter", "chapter", "upsert", 0, "{}", "now"))
            dao.saveEntity(project(id).copy(
                key = ReplicaEntity.key(id, "chapter", "chapter"), entityType = "chapter", entityId = "chapter", dirty = true,
            ))
            dao.saveConflict(LocalConflict("closed", id, "chapter", "chapter", "{}", "{}", 0, status = "resolved"))
            dao.saveEntity(project("other", revision = 8))
            assertEquals(2, dao.pendingMutationCount())

            assertEquals(ProjectDeletionResult.LOCAL_ONLY, repository.deleteProject(id))
            assertNull(dao.projectSyncRecord(id))
            assertNull(dao.projectPackage(id))
            assertTrue(dao.projectPackageSnapshot(id).isEmpty())
            assertEquals(0, dao.pendingMutationCount())
            assertNull(dao.pendingMutation(id, "chapter", "chapter"))
            assertFalse(retained.exists())
            db.openHelper.readableDatabase.query("SELECT COUNT(*) FROM local_conflicts").use {
                assertTrue(it.moveToFirst())
                assertEquals(0, it.getInt(0))
            }
            assertEquals(listOf("other"), repository.projects.first().map { it.project.projectId })
            assertEquals(ProjectDeletionResult.ALREADY_ABSENT, repository.deleteProject(id))
        } finally {
            db.close()
            source.delete()
            retained?.delete()
        }
    }

    @Test
    fun aConfiguredButUnavailableGatewayDoesNotReceiveAnUnuploadedProjectForDeletion() = runBlocking {
        val db = Room.inMemoryDatabaseBuilder(context, SimingDatabase::class.java).build()
        val source = File.createTempFile("delete-configured-", ".siming-project", context.cacheDir)
        var retained: File? = null
        try {
            val dao = db.dao()
            val repository = SimingRepository(context, db)
            MobileProjectPackageWriter.write("source", listOf(project("source")), null, "full", source)
            val result = repository.importProjectPackage(MobileProjectPackageFile(
                source.name, source, source.length(), sha256File(source),
            ))
            retained = File(dao.projectPackage(result.projectId)!!.localFilePath)
            dao.saveConnection(GatewayConnection(baseUrl = "http://127.0.0.1:1", gatewayName = "Unavailable fixture",
                gatewayFingerprint = "fixture", deviceId = "fixture", deviceRole = "owner", protocolVersion = 1))
            assertEquals(ProjectDeletionResult.LOCAL_ONLY, repository.deleteProject(result.projectId))
            assertFalse(retained.exists())
            assertEquals(0, dao.pendingMutationCount())
        } finally {
            db.close()
            source.delete()
            retained?.delete()
        }
    }

    @Test
    fun syncedAndUnconfirmedProjectsCanBeRemovedLocallyWithoutARequestToPc() = runBlocking {
        val db = Room.inMemoryDatabaseBuilder(context, SimingDatabase::class.java).build()
        try {
            val dao = db.dao()
            val repository = SimingRepository(context, db)
            dao.saveConnection(GatewayConnection(baseUrl = "http://127.0.0.1:1", gatewayName = "Unavailable fixture",
                gatewayFingerprint = "fixture", deviceId = "fixture", deviceRole = "owner", protocolVersion = 1))
            for (revision in listOf(0L, 8L)) {
                val id = "project-$revision"
                dao.saveEntity(project(id, revision, true))
                dao.saveMutation(OutboxMutation("edit-$revision", id, "project", id, "upsert", revision, "{}", "now", state = "sending", sentPayloadHash = "uncertain"))
                assertEquals(ProjectDeletionResult.LOCAL_ONLY, repository.deleteProject(id))
                assertNull(dao.projectSyncRecord(id))
                assertTrue(id in dao.excludedProjectIds())
                assertNull(dao.pendingMutation(id, "project", id))
            }
        } finally { db.close() }
    }
}
