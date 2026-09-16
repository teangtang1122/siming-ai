package com.siming.mobile.data.network

import kotlin.test.Test
import kotlin.test.assertEquals

class ProjectToolPathsTest {
    @Test
    fun `remote cataloging status and package sync use canonical PC routes`() {
        assertEquals("/api/v1/projects/p1/cataloging/j1", PcApiPaths.catalogingJob("p1", "j1"))
        assertEquals("/api/v1/projects/p1/cataloging/j1/cancel", PcApiPaths.catalogingCancel("p1", "j1"))
        assertEquals("/api/v1/projects/project-package/import", PcApiPaths.PROJECT_PACKAGE_IMPORT)
    }
}
