package com.siming.mobile

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.espresso.Espresso
import org.junit.Rule
import org.junit.Test

/** Exercises real window insets and screen state, without submitting an AI request. */
class MobileNavigationInstrumentedTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @Test fun keyboardDismissalRestoresNavigationAndTabSwitchKeepsUnsubmittedIdea() {
        compose.waitForIdle()
        if (compose.onAllNodesWithText("稍后配置").fetchSemanticsNodes().isNotEmpty()) {
            compose.onNodeWithText("稍后配置").performClick()
        }
        compose.onNodeWithText("立项", useUnmergedTree = true).assertIsDisplayed().performClick()
        compose.onNode(hasSetTextAction() and hasText("你想写什么故事？"))
            .performClick().performTextInput("Unsubmitted interaction check")
        Espresso.pressBack()
        compose.waitUntil(5_000) {
            compose.onAllNodesWithText("书架", useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty()
        }
        compose.onNodeWithText("书架", useUnmergedTree = true).assertIsDisplayed().performClick()
        compose.onNodeWithText("立项", useUnmergedTree = true).performClick()
        compose.onNodeWithText("Unsubmitted interaction check").assertIsDisplayed().performTextClearance()
        compose.onNodeWithText("书架", useUnmergedTree = true).performClick()
    }
}
