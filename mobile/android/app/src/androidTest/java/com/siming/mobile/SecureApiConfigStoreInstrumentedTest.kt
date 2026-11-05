package com.siming.mobile

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.siming.mobile.data.network.DirectApiConfig
import com.siming.mobile.security.SecureApiConfigStore
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.assertFalse
import kotlinx.serialization.json.put
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SecureApiConfigStoreInstrumentedTest {
    @Test
    fun directApiCredentialRoundTripsWithoutPlaintextStorage() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val preferencesName = "siming_api_test_${java.util.UUID.randomUUID()}"
        val store = SecureApiConfigStore(context, preferencesName)
        val config = DirectApiConfig(
            displayName = "测试 API",
            baseUrl = "https://api.example.test/v1",
            apiKey = "secret-key-that-must-stay-encrypted",
            model = "model-private-name",
            protocol = DirectApiConfig.PROTOCOL_AUTO,
            modelRequestParameters = mapOf("model-private-name" to kotlinx.serialization.json.buildJsonObject { put("temperature", kotlinx.serialization.json.JsonNull) }),
        )

        try {
            store.clear()
            store.update { it.upsert("a", config) }
            val other = config.copy(apiKey = "other-key", baseUrl = "https://other.example.test/v1")
            store.update { it.upsert("b", other).bind(DirectApiConfig.TASK_WRITING, "b", other.model) }
            assertEquals(config, store.read())
            val reopened = SecureApiConfigStore(context, preferencesName)
            assertEquals(other, reopened.catalog().resolve(DirectApiConfig.TASK_WRITING)?.copy(
                contextWindowTokens = other.contextWindowTokens, contextCapacitySource = other.contextCapacitySource,
                taskModels = emptyMap()))
            assertEquals(2, reopened.catalog().profiles.size)
            val beforeFailure = reopened.catalog()
            runCatching { reopened.update { error("test failed update") } }
            assertEquals(beforeFailure, reopened.catalog())

            val raw = context.getSharedPreferences(
                preferencesName,
                android.content.Context.MODE_PRIVATE,
            ).all.values.joinToString()
            assertFalse(raw.contains(config.apiKey))
            assertFalse(raw.contains(config.model))
            assertFalse(raw.contains(config.baseUrl))
        } finally {
            store.clear()
        }
    }
    @Test fun concurrentRegistryUpdatesAcrossStoreInstancesPreserveBothProfiles() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val name = "siming_api_concurrency_${java.util.UUID.randomUUID()}"
        val first = SecureApiConfigStore(context, name)
        val second = SecureApiConfigStore(context, name)
        val pool = java.util.concurrent.Executors.newFixedThreadPool(2)
        val gate = java.util.concurrent.CountDownLatch(1)
        try {
            val tasks = listOf(first, second).mapIndexed { index, store -> pool.submit {
                gate.await()
                store.update { it.upsert("api-$index", DirectApiConfig("API $index",
                    "https://$index.example/v1", "fake-$index", "model")) }
            } }
            gate.countDown()
            tasks.forEach { it.get(10, java.util.concurrent.TimeUnit.SECONDS) }
            assertEquals(setOf("api-0", "api-1"), first.catalog().profiles.map { it.id }.toSet())
        } finally { pool.shutdownNow(); first.clear() }
    }

    @Test fun legacyEncryptedConfigMigratesAtomicallyAndRepeatedOpeningDoesNotDuplicateIt() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val name = "siming_api_migration_${java.util.UUID.randomUUID()}"
        val store = SecureApiConfigStore(context, name)
        val config = DirectApiConfig("legacy", "https://legacy.example/v1", "fake-legacy-key", "default",
            taskModels = mapOf("writing" to "writer"))
        val serializer = kotlinx.serialization.json.Json
        val raw = serializer.encodeToString(DirectApiConfig.serializer(), config)
        val encrypt = SecureApiConfigStore::class.java.getDeclaredMethod("encrypt", String::class.java).apply { isAccessible = true }
        val encrypted = encrypt.invoke(store, raw) as String
        val preferences = context.getSharedPreferences(name, android.content.Context.MODE_PRIVATE)
        try {
            assertTrue(preferences.edit().putString("encrypted_config", encrypted).commit())
            val migrated = SecureApiConfigStore(context, name).catalog()
            assertEquals(config, migrated.defaultConfig())
            assertEquals("writer", migrated.resolve("writing")?.model)
            assertFalse(preferences.contains("encrypted_config"))
            assertTrue(preferences.contains("encrypted_catalog_v1"))
            assertEquals(migrated, SecureApiConfigStore(context, name).catalog())
            assertFalse(preferences.all.values.joinToString().contains("fake-legacy-key"))
            assertTrue(preferences.edit().putString("encrypted_catalog_v1", "invalid")
                .putString("encrypted_config", encrypted).commit())
            assertTrue(runCatching { SecureApiConfigStore(context, name) }.isFailure)
            assertEquals(encrypted, preferences.getString("encrypted_config", null))
        } finally { store.clear() }
    }

}
