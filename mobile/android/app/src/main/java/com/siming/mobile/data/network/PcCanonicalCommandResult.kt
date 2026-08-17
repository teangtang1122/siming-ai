package com.siming.mobile.data.network

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

internal const val MOBILE_REFRESH_PENDING_FIELD = "_mobile_refresh_pending"
internal const val MOBILE_REFRESH_WARNING_FIELD = "_mobile_refresh_warning"

/** Preserve a successful PC write while recording that the local replica still needs refresh. */
internal fun JsonObject.withMobileRefreshFailure(message: String): JsonObject = buildJsonObject {
    this@withMobileRefreshFailure.forEach { (key, value) -> put(key, value) }
    put(MOBILE_REFRESH_PENDING_FIELD, true)
    put(MOBILE_REFRESH_WARNING_FIELD, message.trim().ifBlank { "手机副本刷新失败" })
}

internal fun JsonObject.mobileRefreshWarning(): String =
    (get(MOBILE_REFRESH_WARNING_FIELD) as? JsonPrimitive)?.contentOrNull.orEmpty()
