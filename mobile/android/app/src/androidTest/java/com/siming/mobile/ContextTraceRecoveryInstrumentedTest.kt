package com.siming.mobile

import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import com.siming.mobile.data.observability.ContextTraceDatabase
import com.siming.mobile.data.observability.TraceRow
import org.junit.Assert.*
import org.junit.Test

class ContextTraceRecoveryInstrumentedTest {
    @Test fun recoveryEndsOrphanedRecordsAndRepairsPreviouslyInterruptedRows() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val db = Room.inMemoryDatabaseBuilder(context, ContextTraceDatabase::class.java).build()
        try {
            val dao = db.traces()
            fun row(id: String) = TraceRow(id = id, scopeKind = "creation_session", scopeId = "session",
                started = 1.0, mode = "summary", correlations = "{}")
            dao.insertTrace(row("orphan"))
            dao.insertTrace(row("old-interrupted").copy(finished = 2.0, captureStatus = "interrupted"))
            dao.insertTrace(row("done").copy(finished = 3.0, status = "completed", captureStatus = "recorded"))
            dao.interrupt(4.0)
            listOf("orphan", "old-interrupted").forEach { id ->
                assertEquals("interrupted", dao.trace(id)!!.status)
                assertEquals("interrupted", dao.trace(id)!!.captureStatus)
            }
            assertEquals(4.0, dao.trace("orphan")!!.finished!!, 0.0)
            assertEquals(2.0, dao.trace("old-interrupted")!!.finished!!, 0.0)
            assertEquals("completed", dao.trace("done")!!.status)
        } finally { db.close() }
    }
}
