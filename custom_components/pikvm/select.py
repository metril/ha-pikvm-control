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

    entities: list[SelectEntity] = []

    if kvm_ports:
        entities.append(PikvmKvmPortSelect(coordinator, entry, kvm_ports))

    # MSD image select (always add if MSD is available)
    msd = coordinator.data.get("msd", {})
    if msd.get("enabled", True):
        entities.append(PikvmMsdImageSelect(coordinator, entry))

    async_add_entities(entities)


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


class PikvmMsdImageSelect(PikvmEntity, SelectEntity):
    """Select entity for choosing which ISO image to mount via MSD."""

    _attr_name = "MSD Image"
    _attr_icon = "mdi:disc"

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the MSD image select."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_msd_image"

    @property
    def options(self) -> list[str]:
        """Return available ISO images."""
        if self.coordinator.data is None:
            return []
        return self.coordinator.data.get("msd", {}).get("images", [])

    @property
    def current_option(self) -> str | None:
        """Return the currently selected image."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("msd", {}).get("image")

    async def async_select_option(self, option: str) -> None:
        """Select an image: disconnect → set params → reconnect."""
        msd = self.coordinator.data.get("msd", {}) if self.coordinator.data else {}
        was_connected = msd.get("connected", False)

        try:
            # Must disconnect before changing params
            if was_connected:
                await self.coordinator.client.set_msd_connected(False)

            # Set the image (keep current cdrom/rw settings)
            await self.coordinator.client.set_msd_params(
                image=option,
                cdrom=msd.get("cdrom", True),
                rw=msd.get("rw", False),
            )

            # Reconnect if it was connected before
            if was_connected:
                await self.coordinator.client.set_msd_connected(True)

        except Exception as err:
            raise HomeAssistantError(
                f"Failed to select MSD image '{option}': {err}"
            ) from err
