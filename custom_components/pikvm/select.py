"""Select platform for PiKVM Control — KVM port selection."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PikvmDataUpdateCoordinator
from .entity import PikvmEntity, detect_kvm_ports

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PiKVM select entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: PikvmDataUpdateCoordinator = data["coordinator"]

    if coordinator.data is None:
        return

    gpio_model = coordinator.data.get("gpio_model", {})
    gpio_labels = coordinator.data.get("gpio_labels", {})
    kvm_ports = detect_kvm_ports(gpio_model, gpio_labels)

    if kvm_ports:
        async_add_entities([PikvmKvmPortSelect(coordinator, entry, kvm_ports)])


class PikvmKvmPortSelect(PikvmEntity, SelectEntity):
    """Select entity for KVM port switching."""

    _attr_name = "KVM Port"
    _attr_icon = "mdi:monitor-multiple"

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: ConfigEntry,
        ports: list[dict[str, Any]],
    ) -> None:
        """Initialize the KVM port select."""
        super().__init__(coordinator, entry)
        self._ports = ports
        self._attr_unique_id = f"{entry.entry_id}_kvm_port"
        self._attr_options = [p["label"] for p in ports]

    @property
    def current_option(self) -> str | None:
        """Return the currently active KVM port."""
        if self.coordinator.data is None:
            return None

        inputs = self.coordinator.data.get("gpio", {}).get("inputs", {})
        for port in self._ports:
            channel_state = inputs.get(port["led_channel"], {})
            if channel_state.get("state"):
                return port["label"]

        return None

    async def async_select_option(self, option: str) -> None:
        """Switch to the selected KVM port."""
        for port in self._ports:
            if port["label"] == option:
                try:
                    await self.coordinator.client.gpio_pulse(
                        port["button_channel"], port["pulse_delay"]
                    )
                except Exception as err:
                    raise HomeAssistantError(
                        f"Failed to switch KVM to {option}: {err}"
                    ) from err
                return

        raise HomeAssistantError(f"Unknown KVM port: {option}")
