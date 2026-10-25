"""Switch platform for PiKVM Control."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PikvmApiClient, PikvmApiError, PikvmAuthError, PikvmConnectionError
from .coordinator import PikvmConfigEntry, PikvmDataUpdateCoordinator
from .entity import (
    PikvmEntity,
    detect_kvm_ports,
    get_kvm_channel_names,
    gpio_display_name,
)

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class PikvmSwitchDescription(SwitchEntityDescription):
    """Describes a PiKVM switch."""

    value_fn: Callable[[dict[str, Any]], bool | None]
    turn_on_fn: Callable[[PikvmApiClient], Coroutine]
    turn_off_fn: Callable[[PikvmApiClient], Coroutine]


SWITCHES: tuple[PikvmSwitchDescription, ...] = (
    PikvmSwitchDescription(
        key="hid_jiggler",
        translation_key="hid_jiggler",
        icon="mdi:mouse-move-vertical",
        value_fn=lambda data: data.get("hid", {}).get("jiggler"),
        turn_on_fn=lambda client: client.set_hid_jiggler(True),
        turn_off_fn=lambda client: client.set_hid_jiggler(False),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PikvmConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PiKVM switch entities."""
    coordinator = entry.runtime_data

    entities: list[SwitchEntity] = [
        PikvmSwitch(coordinator, entry, desc) for desc in SWITCHES
    ]

    # MSD connected switch
    entities.append(PikvmMsdSwitch(coordinator, entry))

    # Add GPIO output channels with switch capability (skip KVM channels)
    gpio_model = coordinator.data.get("gpio_model", {}) if coordinator.data else {}
    gpio_labels = coordinator.data.get("gpio_labels", {}) if coordinator.data else {}
    kvm_channels = get_kvm_channel_names(detect_kvm_ports(gpio_model, gpio_labels))

    for channel_name, config in gpio_model.get("outputs", {}).items():
        if channel_name in kvm_channels:
            continue
        if not config.get("switch", False):
            continue

        if "usb_breaker" in channel_name:
            entities.append(PikvmGpioSwitch(coordinator, entry, channel_name, usb=True))
        elif not channel_name.startswith("__"):
            entities.append(PikvmGpioSwitch(coordinator, entry, channel_name))

    async_add_entities(entities)


class PikvmSwitch(PikvmEntity, SwitchEntity):
    """A PiKVM switch."""

    entity_description: PikvmSwitchDescription

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: PikvmConfigEntry,
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
        except (PikvmAuthError, PikvmConnectionError, PikvmApiError) as err:
            raise HomeAssistantError(f"Failed to control PiKVM: {err}") from err
        self.coordinator.push_partial("hid", jiggler=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        try:
            await self.entity_description.turn_off_fn(self.coordinator.client)
        except (PikvmAuthError, PikvmConnectionError, PikvmApiError) as err:
            raise HomeAssistantError(f"Failed to control PiKVM: {err}") from err
        self.coordinator.push_partial("hid", jiggler=False)


class PikvmGpioSwitch(PikvmEntity, SwitchEntity):
    """A PiKVM GPIO output channel as a switch.

    With ``usb=True`` it is the dedicated "USB Connection" switch backed by the
    ``__v3_usb_breaker__`` channel ("Connect main USB to server" in the web UI).
    """

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: PikvmConfigEntry,
        channel_name: str,
        usb: bool = False,
    ) -> None:
        """Initialize the GPIO switch."""
        super().__init__(coordinator, entry)
        self._channel_name = channel_name
        if usb:
            self._attr_unique_id = f"{entry.entry_id}_usb_connection"
            self._attr_translation_key = "usb_connection"
            self._attr_icon = "mdi:usb"
        else:
            self._attr_unique_id = f"{entry.entry_id}_gpio_out_{channel_name}"
            gpio_labels = (
                coordinator.data.get("gpio_labels", {}) if coordinator.data else {}
            )
            self._attr_name = gpio_display_name(channel_name, gpio_labels)
            self._attr_icon = "mdi:electric-switch"

    @property
    def is_on(self) -> bool | None:
        """Return the GPIO output state."""
        if self.coordinator.data is None:
            return None
        outputs = self.coordinator.data.get("gpio", {}).get("outputs", {})
        return outputs.get(self._channel_name, {}).get("state")

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
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off GPIO channel."""
        await self._set(False)

    async def _set(self, state: bool) -> None:
        """Switch the channel and optimistically update state."""
        try:
            await self.coordinator.client.gpio_switch(self._channel_name, state)
        except (PikvmAuthError, PikvmConnectionError, PikvmApiError) as err:
            raise HomeAssistantError(f"Failed to control PiKVM: {err}") from err
        outputs = (self.coordinator.data or {}).get("gpio", {}).get("outputs", {})
        self.coordinator.push_partial(
            "gpio",
            outputs={
                **outputs,
                self._channel_name: {**outputs.get(self._channel_name, {}), "state": state},
            },
        )


class PikvmMsdSwitch(PikvmEntity, SwitchEntity):
    """Switch to connect/disconnect MSD from the server."""

    _attr_translation_key = "msd_connected"
    _attr_icon = "mdi:usb-flash-drive"

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: PikvmConfigEntry,
    ) -> None:
        """Initialize the MSD switch."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_msd_connected"

    @property
    def is_on(self) -> bool | None:
        """Return the MSD connection state."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("msd", {}).get("connected")

    @property
    def available(self) -> bool:
        """MSD switch is unavailable when no image is selected."""
        if self.coordinator.data is None:
            return False
        msd = self.coordinator.data.get("msd", {})
        return bool(msd.get("image")) and super().available

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Connect MSD to the server."""
        msd = self.coordinator.data.get("msd", {}) if self.coordinator.data else {}
        if not msd.get("image"):
            raise HomeAssistantError(
                "Select an image first using the MSD Image selector"
            )
        try:
            await self.coordinator.client.set_msd_connected(True)
        except (PikvmAuthError, PikvmConnectionError, PikvmApiError) as err:
            raise HomeAssistantError(f"Failed to control PiKVM: {err}") from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disconnect MSD from the server."""
        try:
            await self.coordinator.client.set_msd_connected(False)
        except (PikvmAuthError, PikvmConnectionError, PikvmApiError) as err:
            raise HomeAssistantError(f"Failed to control PiKVM: {err}") from err
