"""Switch platform for PiKVM Control."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

import re

from .api import PikvmApiClient
from .const import DOMAIN
from .coordinator import PikvmDataUpdateCoordinator
from .entity import PikvmEntity


@dataclass(frozen=True)
class PikvmSwitchDescription(SwitchEntityDescription):
    """Describes a PiKVM switch."""

    value_fn: Callable[[dict[str, Any]], bool | None] = lambda data: None
    turn_on_fn: Callable[[PikvmApiClient], Coroutine] = None  # type: ignore[assignment]
    turn_off_fn: Callable[[PikvmApiClient], Coroutine] = None  # type: ignore[assignment]


SWITCHES: tuple[PikvmSwitchDescription, ...] = (
    PikvmSwitchDescription(
        key="hid_jiggler",
        name="HID Jiggler",
        icon="mdi:mouse-move-vertical",
        value_fn=lambda data: data.get("hid", {}).get("jiggler"),
        turn_on_fn=lambda client: client.set_hid_jiggler(True),
        turn_off_fn=lambda client: client.set_hid_jiggler(False),
    ),
    PikvmSwitchDescription(
        key="hid_connected",
        name="USB Keyboard & Mouse",
        icon="mdi:keyboard",
        value_fn=lambda data: data.get("hid", {}).get("connected"),
        turn_on_fn=lambda client: client.set_hid_connected(True),
        turn_off_fn=lambda client: client.set_hid_connected(False),
    ),
    PikvmSwitchDescription(
        key="msd_connected",
        name="MSD Connected",
        icon="mdi:usb-flash-drive",
        value_fn=lambda data: data.get("msd", {}).get("connected"),
        turn_on_fn=lambda client: client.set_msd_connected(True),
        turn_off_fn=lambda client: client.set_msd_connected(False),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PiKVM switch entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: PikvmDataUpdateCoordinator = data["coordinator"]

    entities: list[SwitchEntity] = [
        PikvmSwitch(coordinator, entry, desc) for desc in SWITCHES
    ]

    # Add GPIO output channels with switch capability (skip internal channels)
    gpio_model = coordinator.data.get("gpio_model", {}) if coordinator.data else {}
    for channel_name, config in gpio_model.get("outputs", {}).items():
        if not channel_name.startswith("__") and config.get("switch", False):
            entities.append(PikvmGpioSwitch(coordinator, entry, channel_name))

    async_add_entities(entities)


class PikvmSwitch(PikvmEntity, SwitchEntity):
    """A PiKVM switch."""

    entity_description: PikvmSwitchDescription

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: ConfigEntry,
        description: PikvmSwitchDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return the switch state."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        try:
            await self.entity_description.turn_on_fn(self.coordinator.client)
        except Exception as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        try:
            await self.entity_description.turn_off_fn(self.coordinator.client)
        except Exception as err:
            raise HomeAssistantError(str(err)) from err


class PikvmGpioSwitch(PikvmEntity, SwitchEntity):
    """A PiKVM GPIO output channel as a switch."""

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: ConfigEntry,
        channel_name: str,
    ) -> None:
        """Initialize the GPIO switch."""
        super().__init__(coordinator, entry)
        self._channel_name = channel_name
        self._attr_unique_id = f"{entry.entry_id}_gpio_out_{channel_name}"
        # Clean up channel name: strip chN_ prefix, title case
        clean = re.sub(r"^ch\d+_", "", channel_name).replace("_", " ").title()
        self._attr_name = f"GPIO {clean}"
        self._attr_icon = "mdi:electric-switch"

    @property
    def is_on(self) -> bool | None:
        """Return the GPIO output state."""
        if self.coordinator.data is None:
            return None
        outputs = self.coordinator.data.get("gpio", {}).get("outputs", {})
        channel = outputs.get(self._channel_name, {})
        return channel.get("state")

    @property
    def available(self) -> bool:
        """Return if the GPIO channel is online."""
        if self.coordinator.data is None:
            return False
        outputs = self.coordinator.data.get("gpio", {}).get("outputs", {})
        channel = outputs.get(self._channel_name, {})
        return channel.get("online", False) and super().available

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on GPIO channel."""
        try:
            await self.coordinator.client.gpio_switch(self._channel_name, True)
        except Exception as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off GPIO channel."""
        try:
            await self.coordinator.client.gpio_switch(self._channel_name, False)
        except Exception as err:
            raise HomeAssistantError(str(err)) from err
