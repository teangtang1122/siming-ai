package com.siming.mobile.data.cataloging

import com.siming.mobile.data.local.LocalCatalogingRun
import kotlinx.serialization.json.*

internal fun catalogingCommitRequest(run: LocalCatalogingRun, contract: CatalogingContract): JsonObject = buildJsonObject {
    val records = Json.parseToJsonElement(run.sourceJson).jsonArray.map { CatalogRecord.fromJson(it.jsonObject) }
    val candidates = Json.parseToJsonElement(run.candidatesJson).jsonArray.map { it.jsonObject.getValue("payload").jsonObject }
    val referencedIds = candidates.flatMap { listOf(it.text("id"), it.text("parent_id")) }.toMutableSet()
    candidates.single { it.text("type") == "chapter_summary" }.let { summary ->
        referencedIds += (summary.objects("character_bindings") + summary.objects("worldbuilding_bindings")).map { it.text("id") }
    }
    val changedKeys = catalogingChangedKeys(run)
    records.filter { (it.entityType to it.id) in changedKeys }.forEach {
        referencedIds += it.payload.text("parent_id")
    }
    put("request_id", run.id); put("chapter_id", run.chapterId); put("chapter_version", run.chapterVersion)
    put("content_sha256", run.contentHash); put("model", run.model)
    put("candidates", JsonArray(candidates))
    put("archive_guards", JsonArray(records.filter { it.id in referencedIds || (it.entityType to it.id) in changedKeys }.mapNotNull { row ->
        val fields = contract.guardFields[row.recordType] as? JsonArray ?: return@mapNotNull null
        buildJsonObject {
            put("entity_type", row.entityType); put("record_type", row.recordType); put("id", row.id)
            put("fields", buildJsonObject { fields.forEach { field ->
                val name = field.jsonPrimitive.content
                put(name, if (name == "ai_config") {
                    records.firstOrNull { it.recordType == "character_ai_config" && it.payload.text("character_id") == row.id }
                        ?.payload?.filterKeys { it in CatalogingProjection.CONFIG_FIELDS || it == "model_override" }?.let(::JsonObject) ?: JsonNull
                } else row.payload[name] ?: JsonNull)
            } })
        }
    }))
}

internal fun catalogingChangedKeys(run: LocalCatalogingRun): Set<Pair<String, String>> =
    run.changesJson?.let { Json.parseToJsonElement(it).jsonObject }?.let { changes ->
        (changes.getValue("upserts").jsonArray + changes.getValue("deletes").jsonArray)
            .map { CatalogRecord.fromJson(it.jsonObject) }.map { it.entityType to it.id }.toSet()
    }.orEmpty()
