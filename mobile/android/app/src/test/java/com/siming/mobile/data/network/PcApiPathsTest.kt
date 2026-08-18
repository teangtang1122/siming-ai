package com.siming.mobile.data.network

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class PcApiPathsTest {
    @Test
    fun `novel creation uses the canonical PC V3 routes`() {
        assertEquals("/api/v1/novel-creation/start", PcApiPaths.NOVEL_CREATION_START)
        assertEquals("/api/v1/novel-creation/agent-turn", PcApiPaths.NOVEL_CREATION_AGENT_TURN)
        assertEquals(
  "/api/v1/novel-creation/sessions/session-1/runs",
  PcApiPaths.novelCreationRuns("session-1"),
        )
        assertEquals(
  "/api/v1/novel-creation/runs/run-1",
  PcApiPaths.novelCreationRun("run-1"),
        )
        assertEquals(
  "/api/v1/novel-creation/sessions/session-1/stages/world_style/confirm",
  PcApiPaths.novelCreationStageConfirm("session-1", "world_style"),
        )
        assertEquals("/api/v1/novel-creation/apply", PcApiPaths.NOVEL_CREATION_APPLY)
    }

    @Test
    fun `project and outline management use canonical PC routes`() {
        assertEquals("/api/v1/projects/project-1", PcApiPaths.project("project-1"))
        assertEquals(
            "/api/v1/projects/project-1/outline/reorder",
            PcApiPaths.outlineReorder("project-1"),
        )
    }

    @Test
    fun `creation path parameters reject path injection`() {
        assertFailsWith<IllegalArgumentException> {
  PcApiPaths.novelCreationSession("session-1/../../config")
        }
        assertFailsWith<IllegalArgumentException> {
  PcApiPaths.novelCreationStage("session-1", "final_review?debug=true")
        }
    }
}
