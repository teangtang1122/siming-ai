package com.siming.mobile.data.network

import kotlin.test.Test
import kotlin.test.assertEquals

class ProjectToolPathsTest {
    @Test
    fun `cataloging and export use canonical PC routes`() {
        assertEquals("/api/v1/projects/p1/cataloging/start", PcApiPaths.catalogingStart("p1"))
        assertEquals("/api/v1/projects/p1/cataloging/j1", PcApiPaths.catalogingJob("p1", "j1"))
        assertEquals("/api/v1/projects/p1/cataloging/j1/stream", PcApiPaths.catalogingStream("p1", "j1"))
        assertEquals("/api/v1/projects/p1/cataloging/j1/cancel", PcApiPaths.catalogingCancel("p1", "j1"))
        assertEquals("/api/v1/projects/p1/export", PcApiPaths.projectExport("p1"))
        assertEquals("/api/v1/projects/p1/export/download/f1", PcApiPaths.projectExportDownload("p1", "f1"))
    }
}
