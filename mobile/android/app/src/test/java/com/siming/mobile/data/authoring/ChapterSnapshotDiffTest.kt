package com.siming.mobile.data.authoring

import kotlinx.serialization.json.*
import kotlin.test.Test
import kotlin.test.assertEquals

class ChapterSnapshotDiffTest {
    @Test fun `chapter differences use the same fixtures as the desktop SequenceMatcher`() {
        val source = requireNotNull(javaClass.classLoader?.getResourceAsStream("mobile-chapter-diff-v1.json"))
        val cases = source.bufferedReader().use { Json.parseToJsonElement(it.readText()).jsonObject.getValue("cases").jsonArray }
        cases.forEachIndexed { index, value ->
            val fixture = value.jsonObject
            val result = chapterSnapshotDiff(buildJsonObject { put("content", fixture.getValue("from")) }, buildJsonObject { put("content", fixture.getValue("to")) })
            assertEquals(fixture["changes"], result["changes"], "fixture $index")
            assertEquals(fixture["total_changes"], result["total_changes"], "fixture $index")
        }
    }
}
