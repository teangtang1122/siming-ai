package com.siming.mobile.data.network

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class PcAdvancedPathsTest {
    @Test
    fun `chapter advanced commands use canonical PC routes`() {
        val project = "project-1"
        val chapter = "chapter-1"
        val snapshot = "snapshot-1"

        assertEquals("/api/v1/projects/project-1/chapters/reorder", PcApiPaths.chapterReorder(project))
        assertEquals(
            "/api/v1/projects/project-1/chapters/chapter-1/snapshots",
            PcApiPaths.chapterSnapshots(project, chapter),
        )
        assertEquals(
            "/api/v1/projects/project-1/chapters/chapter-1/snapshots/diff",
            PcApiPaths.chapterSnapshotDiff(project, chapter),
        )
        assertEquals(
            "/api/v1/projects/project-1/chapters/chapter-1/snapshots/snapshot-1",
            PcApiPaths.chapterSnapshot(project, chapter, snapshot),
        )
        assertEquals(
            "/api/v1/projects/project-1/chapters/chapter-1/restore/snapshot-1",
            PcApiPaths.chapterRestore(project, chapter, snapshot),
        )
    }

    @Test
    fun `character advanced commands use canonical PC routes`() {
        val project = "project-1"
        val character = "character-1"
        val version = "version-1"

        assertEquals(
            "/api/v1/projects/project-1/characters/relationships",
            PcApiPaths.characterRelationshipNetwork(project),
        )
        assertEquals(
            "/api/v1/projects/project-1/characters/character-1/relationships",
            PcApiPaths.characterRelationships(project, character),
        )
        assertEquals(
            "/api/v1/projects/project-1/characters/character-1/ai-config",
            PcApiPaths.characterAiConfig(project, character),
        )
        assertEquals(
            "/api/v1/projects/project-1/characters/character-1/versions",
            PcApiPaths.characterVersions(project, character),
        )
        assertEquals(
            "/api/v1/projects/project-1/characters/character-1/versions/version-1",
            PcApiPaths.characterVersion(project, character, version),
        )
    }

    @Test
    fun `advanced paths reject unsafe path segments`() {
        assertFailsWith<IllegalArgumentException> {
            PcApiPaths.chapterSnapshot("project-1", "../chapter", "snapshot-1")
        }
        assertFailsWith<IllegalArgumentException> {
            PcApiPaths.characterVersion("project-1", "character-1", "version/escape")
        }
    }
}
