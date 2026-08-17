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
    @Query("SELECT * FROM gateway_connection WHERE id = 1")
    fun observeConnection(): Flow<GatewayConnection?>

    @Query("SELECT * FROM gateway_connection WHERE id = 1")
    suspend fun connection(): GatewayConnection?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun saveConnection(connection: GatewayConnection)

    @Query("DELETE FROM gateway_connection")
    suspend fun deleteConnection()

    @Query(
        "SELECT * FROM replica_entities " +
            "WHERE entityType = 'project' AND operation = 'upsert' " +
            "ORDER BY localModifiedAt DESC",
    )
    fun observeProjects(): Flow<List<ReplicaEntity>>

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

    @Query("SELECT DISTINCT projectId FROM replica_entities WHERE entityType = 'project' AND operation = 'upsert'")
    suspend fun localProjectIds(): List<String>

    @Query(
        "SELECT * FROM sync_outbox WHERE state IN ('pending', 'sending') " +
            "ORDER BY createdAt, rowid LIMIT :limit",
    )
    suspend fun pendingMutations(limit: Int): List<OutboxMutation>

    @Query(
        "SELECT * FROM sync_outbox WHERE projectId = :projectId AND entityType = :entityType " +
            "AND entityId = :entityId AND state = 'pending' ORDER BY createdAt DESC LIMIT 1",
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

    @Query("SELECT COUNT(*) FROM sync_outbox WHERE state IN ('pending', 'sending')")
    fun observePendingCount(): Flow<Int>

    @Query("SELECT COUNT(*) FROM sync_outbox WHERE state IN ('pending', 'sending')")
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
}

@Database(
    entities = [
        ReplicaEntity::class,
        OutboxMutation::class,
        GatewayConnection::class,
        SyncCursor::class,
        LocalConflict::class,
    ],
    version = 2,
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
                .addMigrations(MIGRATION_1_2)
                .build()
                .also { instance = it }
        }

        private val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    "ALTER TABLE gateway_connection " +
                        "ADD COLUMN gatewayEncryptionPublicKey TEXT NOT NULL DEFAULT ''",
                )
            }
        }
    }
}
