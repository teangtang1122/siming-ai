package com.siming.mobile.data.network

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

class PcCanonicalCommandResultTest {
    @Test
    fun `refresh failure keeps successful command payload`() {
        val result = buildJsonObject {
            put("id", "chapter-1")
            put("current_version", 5)
        }.withMobileRefreshFailure("网络暂时不可用")

        assertEquals("chapter-1", result.getValue("id").jsonPrimitive.content)
        assertEquals(5, result.getValue("current_version").jsonPrimitive.content.toInt())
        assertTrue(result.getValue(MOBILE_REFRESH_PENDING_FIELD).jsonPrimitive.content.toBoolean())
        assertEquals("网络暂时不可用", result.mobileRefreshWarning())
    }

    @Test
    fun `blank refresh failure still reports a stable pending message`() {
        val result = buildJsonObject {
            put("id", "chapter-1")
        }.withMobileRefreshFailure("   ")

        assertTrue(result.getValue(MOBILE_REFRESH_PENDING_FIELD).jsonPrimitive.content.toBoolean())
        assertEquals("手机副本刷新失败", result.mobileRefreshWarning())
    }
}
