package com.siming.mobile.data.network

import kotlin.test.Test
import kotlin.test.assertEquals

class PcWorldAdvancedPathsTest {
    @Test
    fun `worldbuilding history uses canonical PC routes`() {
        assertEquals(
            "/api/v1/projects/project-1/worldbuilding/world-1/versions",
            PcApiPaths.worldVersions("project-1", "world-1"),
        )
        assertEquals(
            "/api/v1/projects/project-1/worldbuilding/world-1/timeline",
            PcApiPaths.worldTimeline("project-1", "world-1"),
        )
    }
}
