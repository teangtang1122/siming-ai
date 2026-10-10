package com.siming.mobile.ui

import org.junit.Assert.assertEquals
import org.junit.Test

class ProjectNavigationTest {
    @Test
    fun `reference sections share one primary destination`() {
        listOf("reference", "character", "world", "foreshadowing", "governance", "tools").forEach { section ->
            assertEquals("reference", projectPrimaryKey(section))
        }
    }

    @Test
    fun `writing outline and assistant keep dedicated destinations`() {
        assertEquals("chapter", projectPrimaryKey("chapter"))
        assertEquals("assistant", projectPrimaryKey("assistant"))
        assertEquals("outline", projectPrimaryKey("outline"))
    }
}
