"""Diagnostics support for PiKVM Control."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_PIKVM_PASS,
    CONF_PIKVM_TOTP_SECRET,
    CONF_PIKVM_URL,
    CONF_PIKVM_USER,
)
from .coordinator import PikvmConfigEntry

TO_REDACT = {
    CONF_PIKVM_PASS,
    CONF_PIKVM_TOTP_SECRET,
    CONF_PIKVM_URL,
    CONF_PIKVM_USER,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PikvmConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return {
        "entry": {
            "data": async_redact_data(entry.data, TO_REDACT),
            "options": dict(entry.options),
        },
        "data": entry.runtime_data.data,
    }
