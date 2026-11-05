"""Record origin delivery without treating proxy CONNECT as a model request."""
from __future__ import annotations

import inspect
import httpx

DELIVERY_STATE_KEY = "siming_origin_delivery"


def request_not_sent(error: BaseException) -> bool:
    try:
        state = error.request.extensions.get(DELIVERY_STATE_KEY)
    except (AttributeError, RuntimeError):
        return False
    return isinstance(state, dict) and state.get("started") is False


class ModelDeliveryMixin:
    async def _send_single_request(self, request: httpx.Request) -> httpx.Response:
        # Redirect requests copy extensions; the nested state remains shared.
        state = request.extensions.setdefault(DELIVERY_STATE_KEY, {"started": False})
        if not request.extensions.get("siming_delivery_trace_attached"):
            previous = request.extensions.get("trace")

            async def trace(name, info):
                if name.endswith("send_request_headers.started"):
                    origin_request = info.get("request")
                    if getattr(origin_request, "method", None) not in (b"CONNECT", "CONNECT"):
                        state["started"] = True
                if previous:
                    result = previous(name, info)
                    if inspect.isawaitable(result):
                        await result

            request.extensions["trace"] = trace
            request.extensions["siming_delivery_trace_attached"] = True
        response = await super()._send_single_request(request)
        state["started"] = True  # Also covers cached/intercepted and redirect responses.
        return response
