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
}
