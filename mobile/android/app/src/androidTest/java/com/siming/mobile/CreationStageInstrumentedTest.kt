package com.siming.mobile

import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import com.siming.mobile.data.SimingRepository
import com.siming.mobile.data.creation.CreationStartInput
import com.siming.mobile.data.creation.MobileCreationAgent
import com.siming.mobile.data.local.*
import com.siming.mobile.data.network.DirectApiClient
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class CreationStageInstrumentedTest {
    @Test fun authorCanEditConfirmAndReopenConstraintsWithoutAnyGatewayOrApi() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        for (paired in listOf(false, true)) {
            val db = Room.inMemoryDatabaseBuilder(context, SimingDatabase::class.java).build()
            try {
                if (paired) db.dao().saveConnection(GatewayConnection(baseUrl = "http://127.0.0.1:1",
                    gatewayName = "Unavailable", gatewayFingerprint = "fixture", deviceId = "fixture", deviceRole = "owner", protocolVersion = 1))
                val agent = MobileCreationAgent(context, DirectApiClient())
                val started = agent.start(CreationStartInput("author_led", "手机独立的原创故事"))
                val source = JsonObject(started + ("draft" to JsonObject(started.getValue("draft").jsonObject + mapOf(
                    "execution_route" to JsonPrimitive("mobile"), "execution_host" to JsonPrimitive("device")))))
                val id = source.getValue("id").jsonPrimitive.content
                val key = ReplicaEntity.key("__novel_creation__", "creation_session", id)
                db.dao().saveEntity(ReplicaEntity(key, "__novel_creation__", "creation_session", id, 0,
                    "upsert", source.toString(), "fixture", "2026-09-17T00:00:00Z"))
                val repo = SimingRepository(context, db)
                val edited = JsonObject(source.getValue("draft").jsonObject.getValue("form").jsonObject +
                    ("brief" to JsonPrimitive("作者修改后的创作约束")))
                val saved = withTimeout(5_000) { repo.updateCreationStage(id, "constraints", edited) }
                assertEquals(1, saved.getValue("revision").jsonPrimitive.int)
                val confirmed = withTimeout(5_000) { repo.confirmCreationStage(id, "constraints", edited) }
                assertEquals(2, confirmed.getValue("revision").jsonPrimitive.int)
                val reopened = withTimeout(5_000) { SimingRepository(context, db).getCreationSession(id) }
                val draft = reopened.getValue("draft").jsonObject
                assertEquals(edited, draft.getValue("form"))
                assertEquals("confirmed", draft.getValue("stages").jsonObject.getValue("constraints").jsonObject.getValue("status").jsonPrimitive.content)
                assertTrue(runCatching { repo.generateCreationStage(id, "constraints") }.isFailure)
                assertTrue(runCatching { repo.updateCreationStage(id, "unknown", edited) }.isFailure)
                assertEquals(reopened.getValue("revision"), repo.getCreationSession(id).getValue("revision"))
            } finally { db.close() }
        }
    }
}
