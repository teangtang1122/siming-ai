package com.siming.mobile.data.agent

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

class MobileContextDeliveryTest {
    @Test
    fun `source failures retain bounded Unicode reasons and total count`() {
        val reason = "𠀀😀".repeat(500)
        val receipt = mobileContextSelectionDiagnostics(List(20) { reason })
        assertEquals(20, receipt.getValue("validation_error_count").jsonPrimitive.int)
        assertTrue(receipt.getValue("validation_errors_has_more").jsonPrimitive.boolean)
        val errors = receipt.getValue("validation_errors") as JsonArray
        assertEquals(6, errors.size)
        errors.forEach { error ->
            assertEquals("𠀀😀".repeat(120), error.jsonObject.getValue("reason").jsonPrimitive.content)
        }
        assertTrue(receipt.toString().toByteArray(Charsets.UTF_8).size < 16 * 1024)
    }

    @Test
    fun `pages preserve complete source text and Unicode code point cursors`() {
        listOf("汉字与档案。".repeat(4000), "𠀀😀\u0000\n\"".repeat(4000), "").forEach { text ->
            var args = buildJsonObject { put("content_limit", 7000) }
            val received = StringBuilder()
            while (true) {
                val page = mobileContextPage(text, args)
                val part = page.getValue("text").jsonPrimitive.content
                assertEquals(mobileSha256(text), page.getValue("sha256").jsonPrimitive.content)
                assertEquals(text.codePointCount(0, text.length), page.getValue("total_chars").jsonPrimitive.int)
                assertEquals(received.codePointCount(0, received.length), page.getValue("cursor").jsonPrimitive.int)
                assertTrue(JsonPrimitive(part).toString().toByteArray(Charsets.UTF_8).size <= 20 * 1024)
                received.append(part)
                if (!page.getValue("has_more").jsonPrimitive.boolean) {
                    assertEquals(JsonNull, page["next_cursor"])
                    break
                }
                assertTrue(page.getValue("next_cursor").jsonPrimitive.int > page.getValue("cursor").jsonPrimitive.int)
                args = buildJsonObject {
                    put("content_cursor", page.getValue("next_cursor"))
                    put("content_limit", 7000)
                    put("expected_context_sha256", page.getValue("sha256"))
                }
            }
            assertEquals(text, received.toString())
        }
    }

    @Test
    fun `invalid cursors limits and stale hashes fail explicitly`() {
        val invalid = listOf(
            "content_cursor" to JsonPrimitive(-1), "content_cursor" to JsonPrimitive(10),
            "content_cursor" to JsonPrimitive("1"), "content_limit" to JsonPrimitive(0),
            "content_limit" to JsonPrimitive(7001), "content_limit" to JsonPrimitive(true),
            "expected_context_sha256" to JsonPrimitive("old-hash"),
        )
        invalid.forEach { (key, value) ->
            assertFailsWith<IllegalArgumentException> { mobileContextPage("正文", JsonObject(mapOf(key to value))) }
        }
    }

    @Test
    fun `default Chinese context stays within eight serial pages`() {
        val text = "林澄核对处置附件与来源限制。".repeat(3000)
        var args = JsonObject(emptyMap())
        var pages = 0
        while (true) {
            val page = mobileContextPage(text, args)
            pages += 1
            if (!page.getValue("has_more").jsonPrimitive.boolean) break
            args = buildJsonObject {
                put("content_cursor", page.getValue("next_cursor"))
                put("content_limit", page.getValue("limit"))
                put("expected_context_sha256", page.getValue("sha256"))
            }
        }
        assertTrue(pages <= 8)
    }

    @Test
    fun `selection token stays gated until every persisted page is delivered in order`() {
        val text = "逐页证据不可跳过。".repeat(2_000) + "最终证据"
        val token = "mobile-selection-token"
        var manifest = MobileContextManifest(
            id = "manifest-1",
            projectId = "project-1",
            model = "test-model",
            policyVersion = 1,
            indexVersion = 1,
            policySourceHash = "policy",
            status = "ready",
            request = MobileContextRequest(outlineNodeId = "outline-1", requirements = "测试"),
            requestFingerprint = "request",
            selectionFingerprint = "selection",
            contextWindowTokens = 128_000,
            inputBudgetTokens = 100_000,
            softInputTargetTokens = 32_000,
            outputReserveTokens = 8_000,
            safetyMarginTokens = 1_024,
            items = emptyList(),
            coverage = emptyMap(),
            warnings = emptyList(),
            selectionToken = token,
        )
        val first = mobileContextPage(text, buildJsonObject { put("content_limit", 1000) })
        manifest = beginMobileContextDelivery(manifest, first)

        assertFalse(mobileContextDeliveryReady(manifest, token))
        assertFalse(manifest.toJson(includeContent = true).getValue("context_delivery").toString().contains(token))
        manifest = MobileContextManifest.fromJson(manifest.toJson(includeContent = true), manifest.request)
        assertEquals("pending", manifest.contextDelivery?.status)

        var args = buildJsonObject {
            put("content_cursor", first.getValue("next_cursor"))
            put("content_limit", first.getValue("limit"))
            put("expected_context_sha256", first.getValue("sha256"))
        }
        listOf(
            buildJsonObject {
                put("content_cursor", first.getValue("next_cursor").jsonPrimitive.int + 1)
                put("content_limit", first.getValue("limit"))
                put("expected_context_sha256", first.getValue("sha256"))
            },
            buildJsonObject {
                put("content_cursor", first.getValue("next_cursor"))
                put("content_limit", first.getValue("limit").jsonPrimitive.int + 1)
                put("expected_context_sha256", first.getValue("sha256"))
            },
            buildJsonObject {
                put("content_cursor", first.getValue("next_cursor"))
                put("content_limit", first.getValue("limit"))
                put("expected_context_sha256", "wrong")
            },
        ).forEach { invalid ->
            assertFailsWith<IllegalArgumentException> {
                deliverMobileNextContextPage(manifest, text, invalid)
            }
        }

        val received = StringBuilder(first.getValue("text").jsonPrimitive.content)
        var finalPage = JsonObject(emptyMap())
        var finalArgs = JsonObject(emptyMap())
        while (true) {
            finalArgs = args
            val advanced = deliverMobileNextContextPage(manifest, text, args)
            manifest = advanced.manifest
            finalPage = advanced.page
            received.append(finalPage.getValue("text").jsonPrimitive.content)
            if (!finalPage.getValue("has_more").jsonPrimitive.boolean) break
            assertFalse(mobileContextDeliveryReady(manifest, token))
            args = mobileContextPageArguments(manifest, finalPage)
        }

        assertEquals(text, received.toString())
        assertTrue(mobileContextDeliveryReady(manifest, token))
        assertEquals(text.codePointCount(0, text.length), manifest.contextDelivery?.deliveredUntil)
        val replay = deliverMobileNextContextPage(manifest, text, finalArgs)
        assertEquals(finalPage, replay.page)
        assertEquals(manifest.contextDelivery, replay.state)
        assertFailsWith<IllegalArgumentException> {
            deliverMobileNextContextPage(
                manifest,
                text,
                buildJsonObject {
                    put("content_cursor", finalArgs.getValue("content_cursor").jsonPrimitive.int - 1)
                    put("content_limit", finalArgs.getValue("content_limit"))
                    put("expected_context_sha256", finalArgs.getValue("expected_context_sha256"))
                },
            )
        }
    }
}
