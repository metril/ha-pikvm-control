"""The PiKVM Control integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import PikvmApiClient, PikvmApiError, PikvmAuthError, PikvmConnectionError
from .const import (
    CONF_HTTP_TIMEOUT,
    CONF_PIKVM_PASS,
    CONF_PIKVM_TOTP_SECRET,
    CONF_PIKVM_URL,
    CONF_PIKVM_USER,
    CONF_VERIFY_SSL,
    DEFAULT_HTTP_TIMEOUT,
    DOMAIN,
)
from .coordinator import PikvmConfigEntry, PikvmDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

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
        vol.Optional("keymap", default="en"): str,
    }
)


def _get_client_for_device(hass: HomeAssistant, device_id: str) -> PikvmApiClient:
    """Resolve a device_id to its PikvmApiClient."""
    dev_reg = dr.async_get(hass)
    if dev_reg.async_get(device_id) is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"device_id": device_id},
        )

    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        device_ids = {
            device.id
            for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
        }
        if device_id in device_ids:
            return entry.runtime_data.client

    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="entry_not_loaded",
    )


def _action_failed(err: Exception) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="action_failed",
        translation_placeholders={"error": str(err)},
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration services."""

    async def handle_send_shortcut(call: ServiceCall) -> None:
        client = _get_client_for_device(hass, call.data["device_id"])
        try:
            await client.send_shortcut(call.data["keys"])
        except (PikvmAuthError, PikvmConnectionError, PikvmApiError) as err:
            raise _action_failed(err) from err

    async def handle_type_text(call: ServiceCall) -> None:
        client = _get_client_for_device(hass, call.data["device_id"])
        try:
            await client.type_text(call.data["text"], call.data["keymap"])
        except (PikvmAuthError, PikvmConnectionError, PikvmApiError) as err:
            raise _action_failed(err) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_SHORTCUT,
        handle_send_shortcut,
        schema=SERVICE_SEND_SHORTCUT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_TYPE_TEXT,
        handle_type_text,
        schema=SERVICE_TYPE_TEXT_SCHEMA,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PikvmConfigEntry) -> bool:
    """Set up PiKVM Control from a config entry."""
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, False)
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)

    client = PikvmApiClient(
        session=session,
        url=entry.data[CONF_PIKVM_URL],
        username=entry.data[CONF_PIKVM_USER],
        password=entry.data[CONF_PIKVM_PASS],
        totp_secret=entry.data.get(CONF_PIKVM_TOTP_SECRET) or None,
        verify_ssl=verify_ssl,
        http_timeout=entry.options.get(CONF_HTTP_TIMEOUT, DEFAULT_HTTP_TIMEOUT),
    )

    coordinator = PikvmDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PikvmConfigEntry) -> bool:
    """Unload a PiKVM Control config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_stop()
    return unload_ok
