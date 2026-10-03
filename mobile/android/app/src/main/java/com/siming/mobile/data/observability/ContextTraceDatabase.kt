package com.siming.mobile.data.observability

import android.content.Context
import androidx.room.*

@Entity(tableName = "context_traces", indices = [Index(value = ["id"], unique = true)])
internal data class TraceRow(
    @PrimaryKey(autoGenerate = true) val cursor: Long = 0,
    val id: String, val scopeKind: String, val scopeId: String,
    val started: Double, val finished: Double? = null, val status: String = "running",
    val mode: String, val captureStatus: String = "recording", val correlations: String,
)

@Entity(tableName = "context_trace_events", indices = [Index("traceId"), Index(value = ["traceId", "sequence"], unique = true)],
    foreignKeys = [ForeignKey(entity = TraceRow::class, parentColumns = ["id"], childColumns = ["traceId"], onDelete = ForeignKey.CASCADE)])
internal data class TraceEventRow(@PrimaryKey val id: String, val traceId: String, val sequence: Int, val value: String, val content: String?)

@Entity(tableName = "context_trace_correlations", primaryKeys = ["traceId", "value"], indices = [Index("value")],
    foreignKeys = [ForeignKey(entity = TraceRow::class, parentColumns = ["id"], childColumns = ["traceId"], onDelete = ForeignKey.CASCADE)])
internal data class TraceCorrelationRow(val traceId: String, val value: String)

@Dao
internal interface ContextTraceDao {
    @Insert fun insertTrace(row: TraceRow)
    @Insert fun insertEvent(row: TraceEventRow)
    @Insert(onConflict = OnConflictStrategy.IGNORE) fun insertCorrelations(rows: List<TraceCorrelationRow>)
    @Query("UPDATE context_traces SET finished=:finished,status=:status,captureStatus=CASE WHEN captureStatus='partial' THEN 'partial' ELSE :capture END WHERE id=:id")
    fun finish(id: String, finished: Double, status: String, capture: String)
    @Query("UPDATE context_traces SET captureStatus='partial' WHERE id=:id") fun partial(id: String)
    @Query("UPDATE context_traces SET captureStatus='interrupted',status='interrupted',finished=coalesce(finished,:now) WHERE finished IS NULL OR (captureStatus='interrupted' AND status='running')") fun interrupt(now: Double)
    @Query("SELECT * FROM context_traces WHERE (:kind IS NULL OR scopeKind=:kind) AND (:scopeId IS NULL OR scopeId=:scopeId) AND (:correlationId IS NULL OR EXISTS (SELECT 1 FROM context_trace_correlations c WHERE c.traceId=context_traces.id AND c.value=:correlationId)) AND (:before IS NULL OR cursor<:before) ORDER BY cursor DESC LIMIT 30")
    fun list(kind: String?, scopeId: String?, correlationId: String?, before: Long?): List<TraceRow>
    @Query("SELECT * FROM context_traces WHERE id=:id") fun trace(id: String): TraceRow?
    @Query("SELECT value FROM context_trace_events WHERE traceId=:id AND sequence>:after ORDER BY sequence LIMIT 100")
    fun events(id: String, after: Int): List<String>
    @Query("SELECT substr(content,:offset+1,65536) FROM context_trace_events WHERE id=:payloadId AND traceId=:traceId")
    fun payload(traceId: String, payloadId: String, offset: Int): String?
    @Query("SELECT length(content) FROM context_trace_events WHERE id=:payloadId AND traceId=:traceId")
    fun payloadSize(traceId: String, payloadId: String): Int?
    @Query("DELETE FROM context_traces WHERE finished IS NOT NULL") fun clear(): Int
    @Query("DELETE FROM context_traces WHERE finished IS NOT NULL AND started<:cutoff") fun expire(cutoff: Double)
    @Query("DELETE FROM context_traces WHERE cursor=(SELECT min(cursor) FROM context_traces WHERE finished IS NOT NULL)") fun deleteOldest(): Int
}

@Database(entities = [TraceRow::class, TraceEventRow::class, TraceCorrelationRow::class], version = 1, exportSchema = true)
internal abstract class ContextTraceDatabase : RoomDatabase() {
    abstract fun traces(): ContextTraceDao
    companion object {
        fun create(context: Context) = Room.databaseBuilder(context, ContextTraceDatabase::class.java, "context-traces.db")
            .setJournalMode(JournalMode.TRUNCATE).build()
    }
}
