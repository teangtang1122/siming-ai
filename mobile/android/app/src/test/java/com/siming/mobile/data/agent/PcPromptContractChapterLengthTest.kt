package com.siming.mobile.data.agent

import kotlin.test.Test
import kotlin.test.assertEquals

class PcPromptContractChapterLengthTest {
    @Test
    fun `chapter prompt uses the same length reference as PC`() {
        assertEquals(
            "本次篇幅参考为 2000 个汉字；充分展开场景，标点不计入汉字数。",
            mobileChapterLengthInstruction(2000),
        )
        assertEquals(
            "充分展开场景与人物行动。",
            mobileChapterLengthInstruction(null),
        )
    }
}
