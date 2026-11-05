package com.siming.mobile.data.network

import java.io.IOException
import java.net.InetAddress
import java.net.Proxy
import java.net.UnknownHostException
import java.util.concurrent.atomic.AtomicInteger
import kotlin.test.*
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Test

class DirectConnectionRecoveryTest {
    @Test fun `one failed connection recovers before sending headers`() = runBlocking {
        var calls=0
        val result=recoverDirectConnection(0) { if (++calls==1) throw IOException("connect") else "OK" }
        assertEquals("OK",result)
        assertEquals(2,calls)
    }

    @Test fun `exhaustion stops at two attempts`() = runBlocking {
        var calls=0
        assertFailsWith<DirectApiConnectionException> {
            recoverDirectConnection(0) { calls++; throw IOException("connect") }
        }
        assertEquals(2,calls)
    }

    @Test fun `sent request or received response cannot be replayed here`() = runBlocking {
        var calls=0
        val original=IOException("after headers")
        val caught=assertFailsWith<IOException> {
            recoverDirectConnection(0) { attempt -> calls++; attempt.requestStarted.set(true); throw original }
        }
        assertSame(original,caught)
        assertEquals(1,calls)
    }

    @Test fun `author cancellation interrupts backoff`() = runBlocking {
        var calls=0
        val task=launch { recoverDirectConnection(10_000) { calls++; throw IOException("connect") } }
        yield()
        task.cancelAndJoin()
        assertEquals(1,calls)
    }

    @Test fun `standalone chat and responses recover DNS failure without duplicate requests`() = runBlocking {
        for (protocol in DirectApiConfig.supportedProtocols.filter { it != DirectApiConfig.PROTOCOL_AUTO }) {
            MockWebServer().use { server ->
                server.start()
                val event=if (protocol==DirectApiConfig.PROTOCOL_RESPONSES) {
                    "data: {\"type\":\"response.output_text.delta\",\"delta\":\"OK\"}\n\ndata: {\"type\":\"response.completed\",\"response\":{\"status\":\"completed\",\"output\":[]}}\n\n"
                } else "data: {\"choices\":[{\"delta\":{\"content\":\"OK\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n"
                server.enqueue(MockResponse().setHeader("Content-Type","text/event-stream").setBody(event))
                val lookups=AtomicInteger()
                val client=OkHttpClient.Builder().proxy(Proxy.NO_PROXY).dns(object : okhttp3.Dns {
                    override fun lookup(hostname: String): List<InetAddress> {
                        if (lookups.incrementAndGet()==1) throw UnknownHostException("synthetic initial DNS failure")
                        return listOf(InetAddress.getByName("127.0.0.1"))
                    }
                }).build()
                val api=DirectApiClient(client,allowCleartextForTests=true,retryDelaysMillis=emptyList())
                val config=DirectApiConfig("fixture",server.url("/v1").newBuilder().host("fixture.invalid").build().toString(),"fixture","fixture",protocol)
                val result=api.streamAgentTurn(config,listOf(buildJsonObject { put("role","user");put("content","fixture") }),JsonArray(emptyList()))
                assertEquals("OK",result.content)
                assertEquals(2,lookups.get())
                assertEquals(1,server.requestCount)
            }
        }
    }

    @Test fun `standalone connection exhaustion cannot stack outer retries or protocol fallback`() = runBlocking {
        val lookups=AtomicInteger()
        val client=OkHttpClient.Builder().proxy(Proxy.NO_PROXY).dns(object : okhttp3.Dns {
            override fun lookup(hostname: String): List<InetAddress> { lookups.incrementAndGet(); throw UnknownHostException("synthetic") }
        }).build()
        val api=DirectApiClient(client,allowCleartextForTests=true,retryDelaysMillis=listOf(0,0,0))
        val config=DirectApiConfig("fixture","http://fixture.invalid/v1","fixture","fixture",DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS)
        assertFailsWith<DirectApiConnectionException> {
            api.streamAgentTurn(config,listOf(buildJsonObject { put("role","user");put("content","fixture") }),JsonArray(emptyList()))
        }
        assertEquals(2,lookups.get())
    }
}
