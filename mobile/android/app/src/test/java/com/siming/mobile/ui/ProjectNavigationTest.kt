package com.siming.mobile.ui

import org.junit.Assert.assertEquals
import org.junit.Test

class ProjectNavigationTest {
    @Test
    fun `reference sections share one primary destination`() {
        listOf("outline", "character", "world", "foreshadowing", "governance").forEach { section ->
            assertEquals("reference", projectPrimaryKey(section))
        }
    }

    @Test
    fun `writing assistant and tools keep dedicated destinations`() {
        assertEquals("chapter", projectPrimaryKey("chapter"))
        assertEquals("assistant", projectPrimaryKey("assistant"))
        assertEquals("tools", projectPrimaryKey("tools"))
    }
}
