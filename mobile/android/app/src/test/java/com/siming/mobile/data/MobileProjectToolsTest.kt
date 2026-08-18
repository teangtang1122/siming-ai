package com.siming.mobile.data

import com.siming.mobile.data.local.ReplicaEntity
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class MobileProjectToolsTest {
    @Test
    fun `local txt export keeps chapter order and content`() {
        val project = entity("p|project|p", "project", "p", "{\"title\":\"测试/小说\"}")
        val first = entity("p|chapter|1", "chapter", "1", "{\"title\":\"第一章\",\"content\":\"正文一\"}")
        val second = entity("p|chapter|2", "chapter", "2", "{\"title\":\"第二章\",\"content\":\"正文二\"}")
        val file = buildLocalNovelExport(project, listOf(first, second))
        val text = file.bytes.toString(Charsets.UTF_8)
        assertEquals("测试_小说.txt", file.filename)
        assertTrue(text.indexOf("第一章") < text.indexOf("第二章"))
        assertTrue(text.contains("正文一"))
        assertTrue(text.contains("正文二"))
    }

    private fun entity(key: String, type: String, id: String, payload: String) = ReplicaEntity(
        key = key,
        projectId = "p",
        entityType = type,
        entityId = id,
        revision = 0,
        operation = "upsert",
        payloadJson = payload,
        contentHash = "hash",
        serverModifiedAt = "2026-08-18T00:00:00Z",
    )
}
