package com.siming.mobile.data.local

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.Update
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import kotlinx.coroutines.flow.Flow

@Dao
interface SimingDao {
    @Query("SELECT * FROM local_cataloging_runs WHERE id = :id")
    suspend fun catalogingRun(id: String): LocalCatalogingRun?

    @Query("SELECT * FROM local_cataloging_runs WHERE projectId = :projectId ORDER BY createdAt DESC")
    suspend fun catalogingRuns(projectId: String): List<LocalCatalogingRun>

    @Query("SELECT * FROM local_cataloging_runs WHERE projectId = :projectId ORDER BY createdAt DESC")
    fun observeCatalogingRuns(projectId: String): Flow<List<LocalCatalogingRun>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun saveCatalogingRun(run: LocalCatalogingRun)

    @Query("UPDATE local_cataloging_runs SET status = :status, error = :error, updatedAt = :now WHERE id = :id AND status = 'running'")
    suspend fun stopCatalogingRun(id: String, status: String, error: String, now: String)

    @Query("SELECT * FROM local_cataloging_runs WHERE status = 'completed' AND syncState IN ('pending', 'refresh_pending') ORDER BY createdAt, rowid")
    suspend fun pendingCatalogingCommits(): List<LocalCatalogingRun>

    @Query("UPDATE sync_outbox SET catalogingBarrierId = :runId WHERE projectId = :projectId AND catalogingBarrierId IS NULL AND state IN ('pending', 'sending')")
    suspend fun sealCatalogingSourceMutations(projectId: String, runId: String)

    @Query("UPDATE sync_outbox SET baseRevision = :revision WHERE projectId = :projectId AND entityType = :entityType AND entityId = :entityId AND state = 'pending' AND sentPayloadHash IS NULL")
    suspend fun rebaseUnsentMutations(projectId: String, entityType: String, entityId: String, revision: Long)

    @Query("DELETE FROM local_cataloging_runs")
    suspend fun clearCatalogingRuns()

    @Query("DELETE FROM local_cataloging_runs WHERE projectId = :projectId")
    suspend fun deleteProjectCatalogingRuns(projectId: String)

    @Query("SELECT * FROM gateway_connection WHERE id = 1")
    fun observeConnection(): Flow<GatewayConnection?>

    @Query("SELECT * FROM gateway_connection WHERE id = 1")
    suspend fun connection(): GatewayConnection?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun saveConnection(connection: GatewayConnection)

    @Query("DELETE FROM gateway_connection")
    suspend fun deleteConnection()

    @Query(PROJECT_SYNC_RECORDS_QUERY + " ORDER BY project.localModifiedAt DESC")
    fun observeProjects(): Flow<List<ProjectSyncRecord>>

    @Query(PROJECT_SYNC_RECORDS_QUERY + " AND project.projectId = :projectId")
    suspend fun projectSyncRecord(projectId: String): ProjectSyncRecord?

    @Query(
        "SELECT * FROM replica_entities " +
            "WHERE entityType = 'creation_session' AND operation = 'upsert' " +
            "ORDER BY localModifiedAt DESC",
    )
    fun observeCreationDrafts(): Flow<List<ReplicaEntity>>

    @Query(
        "SELECT * FROM replica_entities WHERE projectId = :projectId " +
            "AND entityType = :entityType AND operation = 'upsert' " +
            "ORDER BY localModifiedAt DESC",
    )
    fun observeEntities(projectId: String, entityType: String): Flow<List<ReplicaEntity>>

    @Query("SELECT * FROM replica_entities WHERE key = :key")
    suspend fun entity(key: String): ReplicaEntity?

    @Query(
        "SELECT * FROM replica_entities WHERE projectId = :projectId " +
            "AND operation = 'upsert' ORDER BY localModifiedAt DESC",
    )
    suspend fun projectSnapshot(projectId: String): List<ReplicaEntity>

    @Query(
        "SELECT * FROM replica_entities WHERE projectId = :projectId " +
            "ORDER BY localModifiedAt DESC",
    )
    suspend fun projectPackageSnapshot(projectId: String): List<ReplicaEntity>

    @Query(
        "SELECT * FROM replica_entities WHERE projectId IN (:projectIds) " +
            "AND dirty = 1",
    )
    suspend fun dirtyEntities(projectIds: List<String>): List<ReplicaEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun saveEntity(entity: ReplicaEntity)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun saveEntities(entities: List<ReplicaEntity>)

    @Query(
        "DELETE FROM replica_entities WHERE projectId = :projectId " +
            "AND dirty = 0 AND conflicted = 0",
    )
    suspend fun deleteCleanProjectReplicas(projectId: String)

    @Query("DELETE FROM replica_entities WHERE projectId = :projectId")
    suspend fun deleteProjectReplica(projectId: String)

    @Query("DELETE FROM sync_outbox WHERE projectId = :projectId")
    suspend fun deleteProjectMutations(projectId: String)

    @Query("DELETE FROM local_conflicts WHERE projectId = :projectId")
    suspend fun deleteProjectConflicts(projectId: String)

    @Query("SELECT DISTINCT projectId FROM replica_entities WHERE entityType = 'project' AND operation = 'upsert'")
    suspend fun localProjectIds(): List<String>

    @Query(
        "SELECT * FROM sync_outbox WHERE state IN ('pending', 'sending') " +
            "ORDER BY createdAt, rowid LIMIT :limit",
    )
    suspend fun pendingMutations(limit: Int): List<OutboxMutation>

    @Query(
        "SELECT * FROM sync_outbox WHERE projectId = :projectId AND entityType = :entityType " +
            "AND entityId = :entityId AND state = 'pending' AND catalogingBarrierId IS NULL ORDER BY createdAt DESC LIMIT 1",
    )
    suspend fun pendingMutation(
        projectId: String,
        entityType: String,
        entityId: String,
    ): OutboxMutation?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun saveMutation(mutation: OutboxMutation)

    @Update
    suspend fun updateMutation(mutation: OutboxMutation)

    @Query(
        "UPDATE sync_outbox SET state = 'pending', lastError = :error " +
            "WHERE mutationId = :mutationId AND state = 'sending'",
    )
    suspend fun resetMutationForRetry(mutationId: String, error: String)

    @Query("DELETE FROM sync_outbox WHERE mutationId = :mutationId")
    suspend fun deleteMutation(mutationId: String)

    @Query(
        "SELECT " +
            "(SELECT COUNT(*) FROM sync_outbox WHERE state IN ('pending', 'sending')) + " +
            "(SELECT COUNT(*) FROM project_packages WHERE syncState IN ('pending', 'uploading')) + " +
            "(SELECT COUNT(*) FROM local_cataloging_runs WHERE status = 'completed' AND syncState IN ('pending', 'refresh_pending'))",
    )
    fun observePendingCount(): Flow<Int>

    @Query(
        "SELECT " +
            "(SELECT COUNT(*) FROM sync_outbox WHERE state IN ('pending', 'sending')) + " +
            "(SELECT COUNT(*) FROM project_packages WHERE syncState IN ('pending', 'uploading')) + " +
            "(SELECT COUNT(*) FROM local_cataloging_runs WHERE status = 'completed' AND syncState IN ('pending', 'refresh_pending'))",
    )
    suspend fun pendingMutationCount(): Int

    @Query("SELECT * FROM sync_cursor WHERE id = 1")
    suspend fun cursor(): SyncCursor?

    @Query("SELECT * FROM sync_cursor WHERE id = 1")
    fun observeCursor(): Flow<SyncCursor?>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun saveCursor(cursor: SyncCursor)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun saveConflict(conflict: LocalConflict)

    @Query("SELECT * FROM local_conflicts WHERE status = 'open' ORDER BY createdAt DESC")
    fun observeConflicts(): Flow<List<LocalConflict>>

    @Query("UPDATE local_conflicts SET status = 'resolved' WHERE id = :id")
    suspend fun resolveConflict(id: String)

    @Query("SELECT * FROM local_conflicts WHERE status = 'open'")
    suspend fun openConflictsSnapshot(): List<LocalConflict>

    @Query("SELECT * FROM project_packages WHERE projectId = :projectId LIMIT 1")
    suspend fun projectPackage(projectId: String): StoredProjectPackage?

    @Query("SELECT * FROM project_packages WHERE idempotencyKey = :idempotencyKey LIMIT 1")
    suspend fun projectPackageByKey(idempotencyKey: String): StoredProjectPackage?

    @Query(
        "SELECT * FROM project_packages WHERE syncState IN ('pending', 'uploading') " +
            "ORDER BY createdAt, rowid",
    )
    suspend fun pendingProjectPackages(): List<StoredProjectPackage>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun saveProjectPackage(projectPackage: StoredProjectPackage)

    @Query("DELETE FROM project_packages WHERE projectId = :projectId")
    suspend fun deleteProjectPackage(projectId: String)

    @Query(
        "DELETE FROM sync_outbox WHERE projectId = :projectId AND entityType = :entityType " +
            "AND entityId = :entityId AND state = 'conflict'",
    )
    suspend fun deleteConflictMutation(projectId: String, entityType: String, entityId: String)

    @Query("DELETE FROM replica_entities")
    suspend fun clearReplicas()

    @Query("DELETE FROM sync_outbox")
    suspend fun clearOutbox()

    @Query("DELETE FROM sync_cursor")
    suspend fun clearCursor()

    @Query("DELETE FROM local_conflicts")
    suspend fun clearConflicts()

    @Query("DELETE FROM project_packages")
    suspend fun clearProjectPackages()
}

@Database(
    entities = [
        ReplicaEntity::class,
        OutboxMutation::class,
        GatewayConnection::class,
        SyncCursor::class,
        LocalConflict::class,
        StoredProjectPackage::class,
        LocalCatalogingRun::class,
    ],
    version = 5,
    exportSchema = true,
)
abstract class SimingDatabase : RoomDatabase() {
    abstract fun dao(): SimingDao

    companion object {
        @Volatile private var instance: SimingDatabase? = null

        fun get(context: Context): SimingDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(
                context.applicationContext,
                SimingDatabase::class.java,
                "siming-mobile.db",
            )
                .addMigrations(MIGRATION_1_2, MIGRATION_2_3, MIGRATION_3_4, MIGRATION_4_5)
                .build()
                .also { instance = it }
        }

        internal val MIGRATION_3_4 = object : Migration(3, 4) {
            override fun migrate(db: SupportSQLiteDatabase) {
                restoreImportedChapterCatalogingState(db)
            }
        }

        internal val MIGRATION_4_5 = object : Migration(4, 5) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE sync_outbox ADD COLUMN catalogingBarrierId TEXT")
                advanceQueuedLegacyChapterVersions(db)
                db.execSQL("CREATE TABLE IF NOT EXISTS `local_cataloging_runs` (`id` TEXT NOT NULL, `projectId` TEXT NOT NULL, `chapterId` TEXT NOT NULL, `chapterVersion` INTEGER NOT NULL, `contentHash` TEXT NOT NULL, `status` TEXT NOT NULL, `sourceFingerprint` TEXT NOT NULL, `sourceJson` TEXT NOT NULL, `candidatesJson` TEXT NOT NULL, `transcriptJson` TEXT NOT NULL, `changesJson` TEXT, `error` TEXT, `model` TEXT NOT NULL, `attempt` INTEGER NOT NULL, `syncState` TEXT NOT NULL, `createdAt` TEXT NOT NULL, `updatedAt` TEXT NOT NULL, PRIMARY KEY(`id`))")
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_local_cataloging_runs_projectId_chapterId` ON `local_cataloging_runs` (`projectId`, `chapterId`)")
            }
        }

        private val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    "ALTER TABLE gateway_connection " +
                        "ADD COLUMN gatewayEncryptionPublicKey TEXT NOT NULL DEFAULT ''",
                )
            }
        }

        private val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    "CREATE TABLE IF NOT EXISTS `project_packages` (" +
                        "`idempotencyKey` TEXT NOT NULL, " +
                        "`packageId` TEXT NOT NULL, " +
                        "`projectId` TEXT NOT NULL, " +
                        "`originalFilename` TEXT NOT NULL, " +
                        "`localFilePath` TEXT NOT NULL, " +
                        "`packageSha256` TEXT NOT NULL, " +
                        "`profile` TEXT NOT NULL, " +
                        "`requestedTitle` TEXT, " +
                        "`syncState` TEXT NOT NULL, " +
                        "`lastError` TEXT, " +
                        "`createdAt` INTEGER NOT NULL, " +
                        "`uploadedAt` INTEGER, " +
                        "PRIMARY KEY(`idempotencyKey`))",
                )
                db.execSQL(
                    "CREATE UNIQUE INDEX IF NOT EXISTS `index_project_packages_projectId` " +
                        "ON `project_packages` (`projectId`)",
                )
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS `index_project_packages_syncState_createdAt` " +
                        "ON `project_packages` (`syncState`, `createdAt`)",
                )
            }
        }
    }
}
