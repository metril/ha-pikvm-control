"""The PiKVM Control integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PikvmApiClient
from .const import (
    CONF_PIKVM_PASS,
    CONF_PIKVM_TOTP_SECRET,
    CONF_PIKVM_URL,
    CONF_PIKVM_USER,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from .coordinator import PikvmDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

SERVICE_SEND_SHORTCUT = "send_shortcut"
SERVICE_TYPE_TEXT = "type_text"

SERVICE_SEND_SHORTCUT_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): str,
        vol.Required("keys"): str,
    }
)

SERVICE_TYPE_TEXT_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): str,
        vol.Required("text"): str,
    }
)


def _get_client_for_device(
    hass: HomeAssistant, device_id: str
) -> PikvmApiClient:
    """Resolve a device_id to its PikvmApiClient."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if device is None:
        raise HomeAssistantError(f"Device {device_id} not found")

    for entry_id in device.config_entries:
        if entry_id in hass.data.get(DOMAIN, {}):
            return hass.data[DOMAIN][entry_id]["client"]

    raise HomeAssistantError(f"No PiKVM integration found for device {device_id}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PiKVM Control from a config entry."""
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, False)
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)

    client = PikvmApiClient(
        session=session,
        url=entry.data[CONF_PIKVM_URL],
        username=entry.data[CONF_PIKVM_USER],
        password=entry.data[CONF_PIKVM_PASS],
        totp_secret=entry.data[CONF_PIKVM_TOTP_SECRET],
        verify_ssl=verify_ssl,
    )

    coordinator = PikvmDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start WebSocket connection for real-time updates
    await coordinator.async_start()

    # Register services if not already registered
    if not hass.services.has_service(DOMAIN, SERVICE_SEND_SHORTCUT):
        async def handle_send_shortcut(call: ServiceCall) -> None:
            client = _get_client_for_device(hass, call.data["device_id"])
            try:
                await client.send_shortcut(call.data["keys"])
            except Exception as err:
                raise HomeAssistantError(str(err)) from err

        async def handle_type_text(call: ServiceCall) -> None:
            client = _get_client_for_device(hass, call.data["device_id"])
            try:
                await client.type_text(call.data["text"])
            except Exception as err:
                raise HomeAssistantError(str(err)) from err

        hass.services.async_register(
            DOMAIN, SERVICE_SEND_SHORTCUT, handle_send_shortcut,
            schema=SERVICE_SEND_SHORTCUT_SCHEMA,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_TYPE_TEXT, handle_type_text,
            schema=SERVICE_TYPE_TEXT_SCHEMA,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a PiKVM Control config entry."""
    data = hass.data[DOMAIN].get(entry.entry_id)
    if data:
        coordinator: PikvmDataUpdateCoordinator = data["coordinator"]
        await coordinator.async_stop()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    # Unregister services if no entries left
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_SEND_SHORTCUT)
        hass.services.async_remove(DOMAIN, SERVICE_TYPE_TEXT)

    return unload_ok
