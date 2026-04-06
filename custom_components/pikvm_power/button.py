"""Button platform for PiKVM Power Control."""

from __future__ import annotations

import logging

import aiohttp
import pyotp

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_PIKVM_PASS,
    CONF_PIKVM_TOTP_SECRET,
    CONF_PIKVM_URL,
    CONF_PIKVM_USER,
    CONF_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PiKVM Power button from a config entry."""
    async_add_entities([PikvmPowerButton(entry)])


class PikvmPowerButton(ButtonEntity):
    """Button to trigger ATX power on a PiKVM device."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_icon = "mdi:power"
    _attr_has_entity_name = True
    _attr_name = "ATX Power"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the PiKVM power button."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_atx_power"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="PiKVM",
            manufacturer="PiKVM",
        )

    async def async_press(self) -> None:
        """Send ATX power button press to PiKVM."""
        data = self._entry.data
        url = data[CONF_PIKVM_URL]
        user = data[CONF_PIKVM_USER]
        password = data[CONF_PIKVM_PASS]
        totp_secret = data[CONF_PIKVM_TOTP_SECRET]
        verify_ssl = data.get(CONF_VERIFY_SSL, False)

        totp = pyotp.TOTP(totp_secret)
        full_password = f"{password}{totp.now()}"

        session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)
        auth = aiohttp.BasicAuth(user, full_password)

        try:
            async with session.post(
                f"{url}/api/atx/click?button=power",
                auth=auth,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status in (401, 403):
                    self._entry.async_start_reauth(self.hass)
                    raise HomeAssistantError(
                        f"PiKVM authentication failed (HTTP {resp.status})"
                    )
                if resp.status != 200:
                    raise HomeAssistantError(
                        f"PiKVM API error: HTTP {resp.status}"
                    )
        except aiohttp.ClientError as err:
            raise HomeAssistantError(
                f"Failed to connect to PiKVM: {err}"
            ) from err

        _LOGGER.debug("PiKVM ATX power command sent successfully")
