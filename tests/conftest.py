"""Stub Home Assistant so the pikvm package modules import without HA."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PKG_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "pikvm"


def _mod(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


def _stub_optional() -> None:
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        class _E(Exception):
            status = 0

        _mod("aiohttp", ClientSession=object, ClientError=_E, WSServerHandshakeError=_E,
             ClientWebSocketResponse=object, BasicAuth=lambda *a, **k: a,
             ClientTimeout=lambda **k: k,
             WSMsgType=types.SimpleNamespace(TEXT=1, ERROR=2, CLOSE=3, CLOSING=4, CLOSED=5))
    try:
        import pyotp  # noqa: F401
    except ImportError:
        class _TOTP:
            def __init__(self, secret): ...
            def now(self): return "000000"
            def interval(self): return 30

        _mod("pyotp", TOTP=_TOTP)


class _DataUpdateCoordinator:
    def __init__(self, hass, logger, *, config_entry=None, name=None, update_interval=None, **kw):
        self.hass = hass
        self.config_entry = config_entry
        self.data = None

    def __class_getitem__(cls, item):
        return cls

    def async_set_updated_data(self, data):
        self.data = data


def _stub_ha() -> None:
    _mod("homeassistant")
    _mod("homeassistant.config_entries", ConfigEntry=MagicMock)
    _mod("homeassistant.core", HomeAssistant=MagicMock, callback=lambda f: f)
    _mod("homeassistant.exceptions", ConfigEntryAuthFailed=type("ConfigEntryAuthFailed", (Exception,), {}))
    helpers = _mod("homeassistant.helpers", device_registry=MagicMock())
    _mod("homeassistant.helpers.device_registry")
    _mod("homeassistant.helpers.event", async_call_later=lambda h, d, a: (lambda: None))
    _mod("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=_DataUpdateCoordinator,
         UpdateFailed=type("UpdateFailed", (Exception,), {}))
    helpers.__path__ = []


_stub_optional()
_stub_ha()
pkg = types.ModuleType("pikvm_pkg")
pkg.__path__ = [str(PKG_DIR)]
sys.modules["pikvm_pkg"] = pkg


@pytest.fixture
def coord():
    from pikvm_pkg.coordinator import PikvmDataUpdateCoordinator

    entry = MagicMock()
    entry.title, entry.entry_id, entry.options = "t", "e", {}
    c = PikvmDataUpdateCoordinator(MagicMock(), entry, MagicMock())
    c.data = {**c._state}
    c.pubs = []
    orig = c.async_set_updated_data

    def spy(d):
        c.pubs.append(d)
        orig(d)

    c.async_set_updated_data = spy
    return c
