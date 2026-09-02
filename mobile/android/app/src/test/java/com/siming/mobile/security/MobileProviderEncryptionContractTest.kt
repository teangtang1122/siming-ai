package com.siming.mobile.security

import com.siming.mobile.data.network.DirectApiConfig
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.int
import kotlinx.serialization.json.long

class MobileProviderEncryptionContractTest {
    @Test
    fun `encrypted provider plaintext includes the explicit capacity profile`() {
        val payload = MobileProviderEncryption.providerPlaintext(
            config = DirectApiConfig(
                displayName = "phone",
                baseUrl = "https://example.com/v1/",
                apiKey = "secret",
                model = "model-a",
                protocol = DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS,
                contextWindowTokens = 128_000,
                maxOutputTokens = 8_000,
                safetyMarginTokens = 4_096,
            ),
            issuedAt = 1234L,
        )

        assertEquals("https://example.com/v1", (payload["base_url"] as JsonPrimitive).content)
        assertEquals(128_000, (payload["context_window_tokens"] as JsonPrimitive).int)
        assertEquals(8_000, (payload["max_output_tokens"] as JsonPrimitive).int)
        assertEquals(4_096, (payload["safety_margin_tokens"] as JsonPrimitive).int)
        assertEquals(1234L, (payload["issued_at"] as JsonPrimitive).long)
        assertFalse("capacity" in payload.keys)
    }

    @Test
    fun `unknown capacity uses 256k fallback before a provider envelope is created`() {
        val config = DirectApiConfig(
            displayName = "phone",
            baseUrl = "https://example.com/v1",
            apiKey = "secret",
            model = "model-a",
            contextWindowTokens = null,
        )

        val payload = MobileProviderEncryption.providerPlaintext(config, issuedAt = 1234L)
        assertEquals(
            DirectApiConfig.DEFAULT_CONTEXT_WINDOW_TOKENS,
            (payload["context_window_tokens"] as JsonPrimitive).int,
        )
        assertEquals(
            DirectApiConfig.DEFAULT_AGENT_OUTPUT_TOKENS,
            (payload["max_output_tokens"] as JsonPrimitive).int,
        )
    }
}
