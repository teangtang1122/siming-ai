package com.siming.mobile

import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.Density
import com.siming.mobile.ui.*
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/** Interaction coverage on the phone; no API calls or changes to the author's database. */
class MobileInteractionInstrumentedTest {
    @get:Rule val compose = createComposeRule()

    @Composable private fun LargeText(content: @Composable () -> Unit) {
        val density = LocalDensity.current
        CompositionLocalProvider(LocalDensity provides Density(density.density, 1.45f)) {
            SimingTheme(content)
        }
    }

    @Test fun composerAndLastStageAreReachableWithLargeText() {
        var selected: String? = null
        compose.setContent {
            LargeText {
                CreationStageSheet(listOf(
                    "constraints" to "创作约束", "concepts" to "创意方向", "style" to "文风与世界观",
                    "characters" to "角色与关系", "places" to "地点与势力", "outline" to "全书主线与卷纲",
                    "opening" to "前 3 章细纲", "review" to "最终审阅",
                ).map { CreationStageItem(it.first, it.second, "pending") },
                    onSelected = { selected = it }, onDismiss = {})
            }
        }
        compose.onNodeWithText("最终审阅").performScrollTo().assertIsDisplayed().performClick()
        compose.runOnIdle { assertEquals("review", selected) }
    }

    @Test fun editingCreationFieldsPreservesTypesAndUnknownDataUntilExplicitSave() {
        val initial = buildJsonObject {
            put("genre", "玄幻")
            put("target_words", 600000)
            put("ready", false)
            put("extensions", buildJsonObject { put("opaque", "保留") })
        }
        var saved: JsonObject? = null
        compose.setContent {
            LargeText { CreationArtifactEditor("创作约束", initial, false, {}, { saved = it }) }
        }
        compose.onNodeWithText("玄幻").performTextReplacement("悬疑")
        compose.runOnIdle { assertNull(saved) }
        compose.onNodeWithText("600000").performTextReplacement("不是数字")
        compose.onNodeWithText("保存修改").assertIsNotEnabled()
        compose.onNodeWithText("不是数字").performTextReplacement("90000")
        compose.onNodeWithText("保存修改").assertIsEnabled().assertIsDisplayed().performClick()
        compose.runOnIdle {
            assertEquals("悬疑", saved!!["genre"]!!.jsonPrimitive.content)
            assertEquals(90000, saved!!["target_words"]!!.jsonPrimitive.int)
            assertFalse(saved!!["target_words"]!!.jsonPrimitive.isString)
            assertEquals(initial["extensions"], saved!!["extensions"])
            assertEquals(initial["ready"], saved!!["ready"])
        }
    }

    @Test fun chapterDirectoryExposesWritingActionsWithoutScrolling() {
        var create = 0
        var assistant = 0
        compose.setContent {
            LargeText {
                Box(Modifier.fillMaxSize().safeDrawingPadding()) {
                    ChapterWorkspace(emptyList(), emptyList(), {}, {},
                        onCreate = { create++ }, onOpenAssistant = { assistant++ }, onOpenCataloging = {})
                }
            }
        }
        compose.onNodeWithText("手动新建").assertIsDisplayed().performClick()
        compose.onNodeWithText("AI 写作").assertIsDisplayed().performClick()
        compose.runOnIdle { assertEquals(1, create); assertEquals(1, assistant) }
    }
}
