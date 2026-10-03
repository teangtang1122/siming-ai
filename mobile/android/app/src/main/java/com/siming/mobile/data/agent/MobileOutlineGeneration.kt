package com.siming.mobile.data.agent

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/** Only explicit provider IDs or managed model IDs select local request options. */
internal fun outlineGenerationExtraBody(contract: JsonObject, model: String): JsonObject? {
    val knownLocalModels = (contract["local_model_ids"] as? JsonArray).orEmpty()
        .mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
    if (!model.startsWith("local_llama_cpp:") && model !in knownLocalModels) return null
    return contract["local_extra_body"] as? JsonObject
}
