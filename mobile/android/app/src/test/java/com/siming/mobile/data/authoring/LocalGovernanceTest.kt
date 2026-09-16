package com.siming.mobile.data.authoring

import kotlinx.serialization.json.*
import kotlin.test.Test
import kotlin.test.assertEquals

class LocalGovernanceTest {
    @Test fun `governance lifecycle uses the same acceptance cases on phone and PC`() {
        val source = requireNotNull(javaClass.classLoader?.getResourceAsStream("mobile-governance-transitions-v1.json"))
        val cases = source.bufferedReader().use { Json.parseToJsonElement(it.readText()).jsonObject.getValue("cases").jsonArray }
        cases.forEachIndexed { index, value ->
            val fixture = value.jsonObject
            val input = JsonObject(fixture.getValue("values").jsonObject + ("status" to fixture.getValue("to")))
            val result = runCatching { localGovernanceTransition(fixture.text("from"), input, "2026-09-16T00:00:00Z") }
            assertEquals(fixture.getValue("valid").jsonPrimitive.boolean, result.isSuccess, "fixture $index: ${result.exceptionOrNull()}")
        }
    }
}
