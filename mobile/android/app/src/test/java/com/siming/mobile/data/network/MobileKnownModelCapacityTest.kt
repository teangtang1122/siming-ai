package com.siming.mobile.data.network

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class MobileKnownModelCapacityTest {
    @Test
    fun `official endpoint and exact model resolve documented capacity`() {
        val capacity = MobileKnownModelCapacityCatalog.resolve(
            "https://api.openai.com/v1",
            "gpt-4o",
        )

        assertEquals(128_000, capacity?.contextWindowTokens)
        assertEquals(16_384, capacity?.maxOutputTokens)
    }

    @Test
    fun `same model name on a proxy remains unverified`() {
        assertNull(
            MobileKnownModelCapacityCatalog.resolve(
                "https://proxy.example/v1",
                "gpt-4o",
            ),
        )
    }

    @Test
    fun `official hostname without https remains unverified`() {
        assertNull(
            MobileKnownModelCapacityCatalog.resolve(
                "http://api.openai.com/v1",
                "gpt-4o",
            ),
        )
    }

    @Test
    fun `regional qwen alias uses the documented conservative lower bound`() {
        val capacity = MobileKnownModelCapacityCatalog.resolve(
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen-plus",
        )

        assertEquals(131_072, capacity?.contextWindowTokens)
        assertEquals(8_192, capacity?.maxOutputTokens)
    }

    @Test
    fun `unknown model on an official endpoint remains unverified`() {
        assertNull(
            MobileKnownModelCapacityCatalog.resolve(
                "https://api.openai.com/v1",
                "gpt-4o-custom",
            ),
        )
    }

    @Test
    fun `legacy deepseek app alias uses official current model capacity`() {
        val config = DirectApiConfig(
            displayName = "DeepSeek",
            baseUrl = "https://api.deepseek.com/v1",
            apiKey = "test-key",
            model = "deepseek-v3",
        )

        val resolved = MobileKnownModelCapacityCatalog.applyIfKnown(config)

        assertEquals("deepseek-v4-flash", resolved?.model)
        assertEquals(1_000_000, resolved?.contextWindowTokens)
        assertEquals(6_000, resolved?.maxOutputTokens)
    }

    @Test
    fun `legacy deepseek app alias is canonical even with an author profile`() {
        val config = DirectApiConfig(
            displayName = "DeepSeek",
            baseUrl = "https://api.deepseek.com/v1",
            apiKey = "test-key",
            model = "deepseek-v3",
            contextWindowTokens = 128_000,
        )

        assertEquals(
            "deepseek-v4-flash",
            MobileKnownModelCapacityCatalog.canonicalModelForOfficialEndpoint(
                config.baseUrl,
                config.model,
            ),
        )
    }

    @Test
    fun `deepseek alias on a compatible proxy remains unverified`() {
        assertNull(
            MobileKnownModelCapacityCatalog.resolve(
                "https://proxy.example/v1",
                "deepseek-v3",
            ),
        )
    }

    @Test
    fun `official hostname with trailing dns dot remains verified`() {
        val capacity = MobileKnownModelCapacityCatalog.resolve(
            "https://api.deepseek.com./v1",
            "deepseek-v4-flash",
        )

        assertEquals(1_000_000, capacity?.contextWindowTokens)
    }
}
