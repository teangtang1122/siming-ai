package com.siming.mobile.data.authoring

import kotlinx.serialization.json.*
import kotlin.test.Test
import kotlin.test.assertEquals

class LocalAuthoringValidationTest {
    @Test fun `local input bounds match the shared desktop schema cases`() {
        val source = requireNotNull(javaClass.classLoader?.getResourceAsStream("mobile-authoring-input-v1.json"))
        val fixtures = source.bufferedReader().use { Json.parseToJsonElement(it.readText()).jsonObject.getValue("cases").jsonArray }
        for (value in fixtures) {
            val case = value.jsonObject
            val outcome = runCatching { validateAuthoringFields(case.getValue("type").jsonPrimitive.content, case.getValue("payload").jsonObject) }
            assertEquals(case.getValue("valid").jsonPrimitive.boolean, outcome.isSuccess, case.getValue("name").jsonPrimitive.content)
        }
    }
}
