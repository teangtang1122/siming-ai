package com.siming.mobile

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.Density
import androidx.test.platform.app.InstrumentationRegistry
import com.siming.mobile.data.creation.*
import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.ui.CreationConversationWorkspace
import com.siming.mobile.ui.SimingTheme
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class CreationConversationUiInstrumentedTest {
    @get:Rule val compose = createComposeRule()

    @Test fun longConversationKeepsComposerVisibleAndStageNavigationDoesNotSendAModelRequest() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val source = MobileCreationAgent(context, DirectApiClient()).start(CreationStartInput("author_led", "海灯测试"))
        val turns = (1..40).map { index ->
            CreationAgentTurnRecords.complete(CreationAgentTurnRecords.pending("第 $index 次作者要求"),
                "第 $index 次回复：" + "这是一段已经保存的资料回复。".repeat(20), JsonArray(emptyList()), JsonArray(emptyList()),
                replayable = false, executionRoute = "mobile")
        }
        val session = CreationAgentTurnRecords.withTurns(source, turns)
        val sent = mutableListOf<String>()
        var openedStage: String? = null
        compose.setContent {
            val density = LocalDensity.current
            CompositionLocalProvider(LocalDensity provides Density(density.density, 1.5f)) {
                SimingTheme {
                    CreationConversationWorkspace(Modifier.fillMaxSize().safeDrawingPadding(), session,
                        listOf("constraints" to "创作约束", "concepts" to "创意方向"),
                        running = false, activity = "", replyDelta = "", progressEvents = emptyList(),
                        onBack = {}, onOpenDossier = { openedStage = it }, onSend = { sent.add(it) },
                        onDiscard = {}, onContinueOnPhone = {}, onConfigureApi = {}, onOpenProject = {})
                }
            }
        }
        compose.onNodeWithText("说说你的想法…").assertIsDisplayed()
        compose.onNodeWithText("第 40 次回复：", substring = true).assertIsDisplayed()
        val screenshot = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
        context.getExternalFilesDir(null)?.resolve("creation-conversation-large-font.png")?.outputStream()?.use {
            screenshot.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
        }
        screenshot.recycle()
        compose.onNodeWithText("创作约束 · 待确认").performClick()
        compose.runOnIdle { assertEquals("constraints", openedStage); assertTrue(sent.isEmpty()) }
        compose.onNode(hasSetTextAction()).performTextInput("继续完善灯塔规则")
        compose.onNodeWithText("发送").assertIsDisplayed().performClick()
        compose.runOnIdle { assertEquals(listOf("继续完善灯塔规则"), sent) }
    }
}
