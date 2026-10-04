"""Select platform for PiKVM Control — KVM port selection."""

from __future__ import annotations

import logging
import sys
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PikvmApiError, PikvmAuthError, PikvmConnectionError
from .coordinator import PikvmConfigEntry, PikvmDataUpdateCoordinator
from .entity import PikvmEntity, detect_kvm_ports

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PikvmConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PiKVM select entities."""
    coordinator = entry.runtime_data

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

    _attr_translation_key = "kvm_port"
    _attr_icon = "mdi:monitor-multiple"

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: PikvmConfigEntry,
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
                except (PikvmAuthError, PikvmConnectionError, PikvmApiError) as err:
                    raise HomeAssistantError(
                        f"Failed to switch KVM to {option}: {err}"
                    ) from err
                return

        raise HomeAssistantError(f"Unknown KVM port: {option}")


class PikvmMsdImageSelect(PikvmEntity, SelectEntity):
    """Select entity for choosing which ISO image to mount via MSD."""

    _attr_translation_key = "msd_image"
    _attr_icon = "mdi:disc"

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: PikvmConfigEntry,
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

        client = self.coordinator.client
        try:
            # Must disconnect before changing params
            if was_connected:
                await client.set_msd_connected(False)

            # Set the image (keep current cdrom/rw settings)
            await client.set_msd_params(
                image=option,
                cdrom=msd.get("cdrom", True),
                rw=msd.get("rw", False),
            )
        except (PikvmAuthError, PikvmConnectionError, PikvmApiError) as err:
            raise HomeAssistantError(
                f"Failed to select MSD image '{option}': {err}"
            ) from err
        finally:
            # Reconnect if it was connected before
            if was_connected:
                try:
                    await client.set_msd_connected(True)
                except (PikvmAuthError, PikvmConnectionError, PikvmApiError) as err:
                    if sys.exc_info()[1] is None:
                        raise HomeAssistantError(
                            f"Failed to reconnect MSD after selecting '{option}': {err}"
                        ) from err
                    # Keep the original error as the reported cause
                    _LOGGER.warning(
                        "Failed to reconnect MSD after selecting '%s': %s", option, err
                    )
