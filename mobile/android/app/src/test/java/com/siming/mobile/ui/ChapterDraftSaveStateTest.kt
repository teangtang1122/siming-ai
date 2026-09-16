package com.siming.mobile.ui

import com.siming.mobile.data.MobilePendingChapterDraft
import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class ChapterDraftSaveStateTest {
    private val draft = MobilePendingChapterDraft(
        draftId = "draft-1",
        projectId = "project-1",
        title = "雾里的回信",
        content = "作者准备继续修改的正文。",
        executionRoute = "android_standalone",
    )

    @Test
    fun `saving without a model remains available and cataloging explains setup`() {
        val state = chapterDraftSaveState(draft, draft.title, online = false, busy = false, viewingFormalText = false)
        assertTrue(state.canSave)
        assertFalse(state.canCatalog)
        assertTrue(state.hint.contains("保存到手机"))
        assertTrue(state.hint.contains("API"))
    }

    @Test
    fun `configured phone can explicitly save and catalog without a gateway`() {
        val state = chapterDraftSaveState(draft, draft.title, false, false, false, directApiConfigured = true)
        assertTrue(state.canSave)
        assertTrue(state.canCatalog)
        assertTrue(state.hint.contains("尚未保存"))
    }

    @Test
    fun `a saved gateway does not enable cataloging without phone API`() {
        val state = chapterDraftSaveState(draft, draft.title, online = true, busy = false, viewingFormalText = false)
        assertTrue(state.canSave)
        assertFalse(state.canCatalog)
        assertTrue(state.hint.contains("尚未保存"))
    }

    @Test
    fun `disconnected revisions can be saved with a local version check`() {
        val revision = draft.copy(draftKind = "revision", baseChapterVersion = 2, targetChapterCurrentVersion = 2)
        val state = chapterDraftSaveState(revision, revision.title, online = false, busy = false, viewingFormalText = false)
        assertTrue(state.canSave)
        assertFalse(state.canCatalog)
        assertTrue(state.hint.contains("保存到手机"))
    }

    @Test
    fun `conflicts comparison and an in-flight save all disable both write actions with a reason`() {
        val conflict = draft.copy(draftKind = "revision", baseChapterVersion = 1, targetChapterCurrentVersion = 2)
        val states = listOf(
            chapterDraftSaveState(conflict, draft.title, true, false, false),
            chapterDraftSaveState(draft, draft.title, true, false, true),
            chapterDraftSaveState(draft, draft.title, true, true, false),
            chapterDraftSaveState(draft, "  ", true, false, false),
            chapterDraftSaveState(draft.copy(status = "generating"), draft.title, true, false, false),
        )
        states.forEach { state ->
            assertFalse(state.canSave)
            assertFalse(state.canCatalog)
            assertTrue(state.hint.isNotBlank())
        }
    }
}
