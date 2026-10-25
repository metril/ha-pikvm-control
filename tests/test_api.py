from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from pikvm_pkg.api import PikvmApiClient


class FakeResp:
    status = 200

    async def json(self):
        return {"ok": True, "result": {}}

    async def read(self):
        return b""

    async def text(self):
        return ""


class FakeCtx:
    async def __aenter__(self):
        return FakeResp()

    async def __aexit__(self, *a):
        return False


def make():
    session = MagicMock()
    session.request = MagicMock(return_value=FakeCtx())
    session.ws_connect = AsyncMock(return_value="ws")
    return PikvmApiClient(session, "https://pikvm/", "admin", "pw"), session


async def test_set_streamer_params_only_non_none():
    c, s = make()
    await c.set_streamer_params(desired_fps=30, quality=None)
    args, kw = s.request.call_args
    assert args == ("POST", "https://pikvm/api/streamer/set_params")
    assert kw["params"] == {"desired_fps": "30"}


async def test_set_hid_output_mouse():
    c, s = make()
    await c.set_hid_output(mouse="usb_rel")
    args, kw = s.request.call_args
    assert args == ("POST", "https://pikvm/api/hid/set_params")
    assert kw["params"] == {"mouse_output": "usb_rel"}


async def test_connect_ws_stream_param():
    c, s = make()
    assert await c.connect_ws(stream=True) == "ws"
    assert s.ws_connect.call_args.args[0] == "wss://pikvm/api/ws"
    assert s.ws_connect.call_args.kwargs["params"] == {"stream": "1"}
    await c.connect_ws()
    assert s.ws_connect.call_args.kwargs["params"] == {"stream": "0"}
