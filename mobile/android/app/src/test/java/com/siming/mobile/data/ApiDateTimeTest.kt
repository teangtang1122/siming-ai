package com.siming.mobile.data

import java.time.ZoneId
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull

class ApiDateTimeTest {
    @Test
    fun `PC and mobile use the same saved instant and local display`() {
        val stream = requireNotNull(javaClass.classLoader?.getResourceAsStream("api-timestamps-v1-interop.json"))
        val fixture = stream.bufferedReader(Charsets.UTF_8).use {
            Json.parseToJsonElement(it.readText()).jsonObject
        }
        fixture.getValue("cases").jsonArray.forEach { item ->
            val row = item.jsonObject
            val label = row.getValue("id").jsonPrimitive.content
            val input = row.getValue("input").jsonPrimitive.contentOrNull
            val zone = ZoneId.of(row.getValue("zone").jsonPrimitive.content)
            assertEquals(row.getValue("epoch_ms").jsonPrimitive.longOrNull, parseApiDateTime(input)?.toEpochMilli(), label)
            assertEquals(row.getValue("display").jsonPrimitive.contentOrNull, formatApiDateTime(input, zone), label)
        }
    }
}
