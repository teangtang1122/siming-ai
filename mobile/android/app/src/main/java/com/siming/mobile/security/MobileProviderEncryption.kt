package com.siming.mobile.security

import android.util.Base64
import com.google.crypto.tink.subtle.X25519
import com.siming.mobile.data.local.GatewayConnection
import com.siming.mobile.data.network.DirectApiConfig
import com.siming.mobile.data.network.MobileProviderEnvelope
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** Encrypt a phone-owned API key for one Gateway assistant request. */
object MobileProviderEncryption {
    fun seal(
        config: DirectApiConfig,
        connection: GatewayConnection,
        projectId: String,
    ): MobileProviderEnvelope {
        require(connection.gatewayEncryptionPublicKey.isNotBlank()) {
            "当前配对版本不支持手机 Key 线路，请断开后重新扫描 Gateway 二维码"
        }
        val gatewayPublic = decode(connection.gatewayEncryptionPublicKey)
        require(gatewayPublic.size == 32) { "Gateway 加密公钥格式无效，请重新配对" }
        val ephemeralPrivate = X25519.generatePrivateKey()
        val ephemeralPublic = X25519.publicFromPrivate(ephemeralPrivate)
        val sharedSecret = X25519.computeSharedSecret(ephemeralPrivate, gatewayPublic)
        val key = hkdfSha256(sharedSecret, ENVELOPE_INFO, 32)
        val nonce = ByteArray(12).also(SecureRandom()::nextBytes)
        val associatedData =
            "siming-mobile-provider-v1:${connection.deviceId}:$projectId".toByteArray(Charsets.UTF_8)
        val plaintext = Json.encodeToString(
            providerPlaintext(config, issuedAt = System.currentTimeMillis()),
        ).toByteArray(Charsets.UTF_8)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
        cipher.updateAAD(associatedData)
        val ciphertext = cipher.doFinal(plaintext)
        return MobileProviderEnvelope(
            ephemeralPublicKey = encode(ephemeralPublic),
            nonce = encode(nonce),
            ciphertext = encode(ciphertext),
        )
    }

    /** Request-scoped provider and capacity profile; this object is encrypted as one envelope. */
    internal fun providerPlaintext(
        config: DirectApiConfig,
        issuedAt: Long,
    ): JsonObject {
        val effectiveConfig = config.withContextWindowFallback()
        val contextWindow = requireNotNull(effectiveConfig.contextWindowTokens)
        return buildJsonObject {
            put("base_url", effectiveConfig.baseUrl.trim().trimEnd('/'))
            put("api_key", effectiveConfig.apiKey.trim())
            put("model", effectiveConfig.model.trim())
            put(
                "protocol",
                if (effectiveConfig.protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
                    DirectApiConfig.PROTOCOL_RESPONSES
                } else {
                    DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS
                },
            )
            put("context_window_tokens", contextWindow)
            put("max_output_tokens", effectiveConfig.maxOutputTokens)
            put("safety_margin_tokens", effectiveConfig.safetyMarginTokens)
            put(
                "capacity_assurance",
                if (
                    effectiveConfig.contextCapacitySource ==
                    DirectApiConfig.CONTEXT_CAPACITY_FALLBACK
                ) {
                    "unverified"
                } else {
                    "conservative"
                },
            )
            put("issued_at", issuedAt)
        }
    }

    private fun hkdfSha256(input: ByteArray, info: ByteArray, length: Int): ByteArray {
        val extract = Mac.getInstance("HmacSHA256")
        extract.init(SecretKeySpec(ByteArray(32), "HmacSHA256"))
        val pseudoRandomKey = extract.doFinal(input)
        val output = ByteArray(length)
        var previous = ByteArray(0)
        var offset = 0
        var counter = 1
        while (offset < length) {
            val expand = Mac.getInstance("HmacSHA256")
            expand.init(SecretKeySpec(pseudoRandomKey, "HmacSHA256"))
            expand.update(previous)
            expand.update(info)
            expand.update(counter.toByte())
            previous = expand.doFinal()
            val count = minOf(previous.size, length - offset)
            previous.copyInto(output, offset, 0, count)
            offset += count
            counter += 1
        }
        return output
    }

    private fun decode(value: String): ByteArray = Base64.decode(
        value.padEnd((value.length + 3) / 4 * 4, '='),
        Base64.URL_SAFE or Base64.NO_WRAP,
    )

    private fun encode(value: ByteArray): String = Base64.encodeToString(
        value,
        Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING,
    )

    private val ENVELOPE_INFO = "siming-mobile-provider-envelope-v1".toByteArray(Charsets.UTF_8)
}
