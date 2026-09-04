package com.siming.mobile.data.agent

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonPrimitive

class MobileWorkspacePaginationTest {
    @Test
    fun `page honors cursor and returns the next cursor`() {
        val first = mobilePage(listOf("a", "b", "c", "d"), cursor = 1, limit = 2)

        assertEquals(listOf("b", "c"), first.values)
        assertEquals(1, first.cursor)
        assertEquals(3, first.nextCursor)

        val last = mobilePage(listOf("a", "b", "c", "d"), cursor = 3, limit = 2)
        assertEquals(listOf("d"), last.values)
        assertNull(last.nextCursor)
    }

    @Test
    fun `text range honors offset and exposes continuation metadata`() {
        val first = mobileTextRange("0123456789", offset = 3, maxChars = 4)

        assertEquals("3456", first.text)
        assertEquals(3, first.metadata.getValue("offset_chars").jsonPrimitive.int)
        assertEquals(4, first.metadata.getValue("returned_chars").jsonPrimitive.int)
        assertEquals(7, first.metadata.getValue("next_offset_chars").jsonPrimitive.int)
        assertTrue(first.metadata.getValue("has_more").jsonPrimitive.boolean)

        val beyondEnd = mobileTextRange("0123", offset = 20, maxChars = 4)
        assertEquals("", beyondEnd.text)
        assertEquals(20, beyondEnd.metadata.getValue("offset_chars").jsonPrimitive.int)
        assertEquals(0, beyondEnd.metadata.getValue("returned_chars").jsonPrimitive.int)
        assertFalse(beyondEnd.metadata.getValue("has_more").jsonPrimitive.boolean)
    }
}
