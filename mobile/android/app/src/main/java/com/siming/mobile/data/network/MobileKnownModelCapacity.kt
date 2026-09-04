package com.siming.mobile.data.network

import java.net.URI

/**
 * Versioned capacity facts for exact models served from first-party endpoints.
 *
 * A familiar model name on an arbitrary OpenAI-compatible proxy is not enough
 * evidence: the proxy may route that name to a different deployment.  Mobile
 * therefore requires both an exact official hostname and an exact model ID.
 */
internal data class MobileKnownModelCapacity(
    val contextWindowTokens: Int,
    val maxOutputTokens: Int,
    val source: String,
)

internal object MobileKnownModelCapacityCatalog {
    private const val OPENAI_SOURCE = "openai_model_docs_2026_08_30"
    private const val DEEPSEEK_SOURCE = "deepseek_model_docs_2026_08_30"
    private const val GEMINI_SOURCE = "gemini_model_docs_2026_08_30"
    private const val QWEN_SOURCE = "qwen_model_docs_2026_08_30"

    private val openAi1050k = setOf(
        "gpt-5.6",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.5-pro",
        "gpt-5.4",
        "gpt-5.4-pro",
    )
    private val openAi400k = setOf(
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-5.1",
        "gpt-5.2",
        "gpt-5.2-pro",
        "gpt-5.2-codex",
        "gpt-5.3-codex",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
    )
    private val openAi1047k = setOf(
        "gpt-4.1",
        "gpt-4.1-2025-04-14",
        "gpt-4.1-mini",
        "gpt-4.1-mini-2025-04-14",
        "gpt-4.1-nano",
        "gpt-4.1-nano-2025-04-14",
    )
    private val openAi128k = setOf(
        "gpt-4o",
        "gpt-4o-2024-05-13",
        "gpt-4o-2024-08-06",
        "gpt-4o-2024-11-20",
        "gpt-4o-mini",
        "gpt-4o-mini-2024-07-18",
    )
    private val gemini1048k = setOf(
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    )

    private val deepSeekDocumentedModels = setOf(
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    )

    private val deepSeekLegacyAliases = mapOf(
        "deepseek-v3" to "deepseek-v4-flash",
    )

    fun resolve(baseUrl: String, model: String): MobileKnownModelCapacity? {
        val provider = providerForOfficialEndpoint(baseUrl) ?: return null
        val normalizedModel = canonicalModel(provider, model)
        return when {
            provider == "openai" && normalizedModel in openAi1050k ->
                MobileKnownModelCapacity(1_050_000, 128_000, OPENAI_SOURCE)
            provider == "openai" && normalizedModel in openAi400k ->
                MobileKnownModelCapacity(400_000, 128_000, OPENAI_SOURCE)
            provider == "openai" && normalizedModel in openAi1047k ->
                MobileKnownModelCapacity(1_047_576, 32_768, OPENAI_SOURCE)
            provider == "openai" && normalizedModel in openAi128k ->
                MobileKnownModelCapacity(128_000, 16_384, OPENAI_SOURCE)
            provider == "deepseek" && normalizedModel in deepSeekDocumentedModels ->
                MobileKnownModelCapacity(1_000_000, 384_000, DEEPSEEK_SOURCE)
            provider == "gemini" && normalizedModel in gemini1048k ->
                MobileKnownModelCapacity(1_048_576, 65_536, GEMINI_SOURCE)
            provider == "qwen" && normalizedModel == "qwen-max" ->
                MobileKnownModelCapacity(32_768, 8_192, QWEN_SOURCE)
            provider == "qwen" && normalizedModel in setOf(
                "qwen-flash",
            ) -> MobileKnownModelCapacity(1_000_000, 32_768, QWEN_SOURCE)
            provider == "qwen" && normalizedModel == "qwen-plus" ->
                MobileKnownModelCapacity(131_072, 8_192, QWEN_SOURCE)
            provider == "qwen" && normalizedModel in setOf(
                "qwen3.7-max",
                "qwen3.7-max-us",
                "qwen3.7-plus",
                "qwen3.7-plus-us",
                "qwen3.7-flash",
                "qwen3.8-max",
                "qwen3.8-flash",
            ) -> MobileKnownModelCapacity(1_000_000, 65_536, QWEN_SOURCE)
            provider == "qwen" && normalizedModel == "qwen3-max" ->
                MobileKnownModelCapacity(262_144, 65_536, QWEN_SOURCE)
            else -> null
        }
    }

    fun canonicalModelForOfficialEndpoint(baseUrl: String, model: String): String {
        val provider = providerForOfficialEndpoint(baseUrl) ?: return model.trim()
        return canonicalModel(provider, model)
    }

    fun applyIfKnown(config: DirectApiConfig, model: String = config.model): DirectApiConfig? {
        val provider = providerForOfficialEndpoint(config.baseUrl) ?: return null
        val normalizedModel = canonicalModel(provider, model)
        val capacity = resolve(config.baseUrl, normalizedModel) ?: return null
        val remainingOutput = capacity.contextWindowTokens - config.safetyMarginTokens - 1
        if (remainingOutput <= 0) return null
        return config.copy(
            model = normalizedModel,
            contextWindowTokens = capacity.contextWindowTokens,
            contextCapacitySource = DirectApiConfig.CONTEXT_CAPACITY_CATALOG,
            maxOutputTokens = minOf(
                config.maxOutputTokens,
                capacity.maxOutputTokens,
                remainingOutput,
            ),
        )
    }

    private fun providerForOfficialEndpoint(baseUrl: String): String? {
        val endpoint = runCatching { URI(baseUrl.trim()) }.getOrNull() ?: return null
        if (!endpoint.scheme.equals("https", ignoreCase = true)) return null
        val host = endpoint.host?.lowercase()?.removeSuffix(".") ?: return null
        return when (host) {
            "api.openai.com" -> "openai"
            "api.deepseek.com" -> "deepseek"
            "generativelanguage.googleapis.com" -> "gemini"
            "dashscope.aliyuncs.com",
            "dashscope-intl.aliyuncs.com",
            "dashscope-us.aliyuncs.com",
            "dashscope-eu.aliyuncs.com" -> "qwen"
            else -> null
        }
    }

    private fun canonicalModel(provider: String, model: String): String {
        val normalized = if (provider == "gemini") {
            model.trim().removePrefix("models/")
        } else {
            model.trim()
        }
        return if (provider == "deepseek") {
            deepSeekLegacyAliases[normalized] ?: normalized
        } else {
            normalized
        }
    }
}
