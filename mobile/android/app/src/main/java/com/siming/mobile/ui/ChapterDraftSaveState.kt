package com.siming.mobile.ui

import com.siming.mobile.data.MobilePendingChapterDraft

internal data class ChapterDraftSaveState(
    val canSave: Boolean,
    val canCatalog: Boolean,
    val hint: String,
)

internal fun chapterDraftSaveState(
    draft: MobilePendingChapterDraft,
    title: String,
    online: Boolean,
    busy: Boolean,
    viewingFormalText: Boolean,
    directApiConfigured: Boolean = false,
): ChapterDraftSaveState {
    val blockedReason = when {
        draft.generating -> "AI 正在写作，生成完成后可审阅并保存。"
        busy -> "正在提交，请稍候。完成后会显示保存结果。"
        viewingFormalText -> "当前正在对比正式正文。返回修订候选后可以保存。"
        draft.versionConflict -> "正式章节版本已变化。请重新生成或人工合并修订候选后再保存。"
        title.isBlank() -> "先填写章节标题。有标题后即可保存。"
        draft.revision && !online -> "修订候选已保留。请连接 Gateway 核对正式章节版本后再保存。"
        else -> null
    }
    return ChapterDraftSaveState(
        canSave = blockedReason == null,
        canCatalog = blockedReason == null && (online || directApiConfigured),
        hint = blockedReason ?: if (online) {
            "正文尚未保存。保存正文只保留本次编辑；保存并建档还会根据正文更新故事资料。"
        } else if (directApiConfigured) {
            "正文尚未保存。保存正文只保留本次编辑；保存并建档会使用手机 API 更新故事资料，完成后可继续下一章。"
        } else {
            "正文尚未保存。可以先保存到手机；配置手机 API 或连接 Gateway 后可建档。"
        },
    )
}
