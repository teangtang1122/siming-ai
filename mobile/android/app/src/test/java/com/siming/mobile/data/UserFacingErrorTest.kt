package com.siming.mobile.data

import com.siming.mobile.data.network.GatewayHttpException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import kotlin.test.Test
import kotlin.test.assertEquals

class UserFacingErrorTest {
    @Test
    fun `connection failures become actionable Chinese messages`() {
        assertEquals(
            "无法连接服务器，请确认网络和服务运行状态后重试",
            ConnectException("Failed to connect to /192.168.1.2:8000").toUserFacingMessage(),
        )
        assertEquals(
            "找不到服务器，请检查 API 或 Gateway 地址后重试",
            UnknownHostException("example.invalid").toUserFacingMessage(),
        )
        assertEquals(
            "请求超时，未收到完整响应，请稍后重试",
            SocketTimeoutException("timeout").toUserFacingMessage(),
        )
    }

    @Test
    fun `gateway responses keep the server's localized guidance`() {
        assertEquals(
            "设备授权已失效，请重新连接 Gateway",
            GatewayHttpException(401, "设备授权已失效，请重新连接 Gateway")
                .toUserFacingMessage(),
        )
    }
}
