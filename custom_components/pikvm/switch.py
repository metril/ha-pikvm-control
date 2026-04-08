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

from .api import PikvmApiClient
from .const import DOMAIN
from .coordinator import PikvmDataUpdateCoordinator
from .entity import PikvmEntity, detect_kvm_ports, get_kvm_channel_names, gpio_display_name


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
            entities.append(PikvmUsbConnectionSwitch(coordinator, entry, channel_name))
        elif not channel_name.startswith("__"):
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
        self._optimistic_update(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        try:
            await self.entity_description.turn_off_fn(self.coordinator.client)
        except Exception as err:
            raise HomeAssistantError(str(err)) from err
        self._optimistic_update(False)

    def _optimistic_update(self, state: bool) -> None:
        """Optimistically update coordinator state after successful API call.

        Needed because the WebSocket may not push back all state changes
        (e.g., jiggler state is not included in hid_state WS events).
        """
        if self.coordinator.data is None:
            return
        key = self.entity_description.key
        if key == "hid_jiggler":
            self.coordinator.data.setdefault("hid", {})["jiggler"] = state
        self.coordinator.async_set_updated_data(self.coordinator.data)


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
        gpio_labels = coordinator.data.get("gpio_labels", {}) if coordinator.data else {}
        self._attr_name = gpio_display_name(channel_name, gpio_labels)
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


class PikvmMsdSwitch(PikvmEntity, SwitchEntity):
    """Switch to connect/disconnect MSD from the server."""

    _attr_name = "MSD Connected"
    _attr_icon = "mdi:usb-flash-drive"

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: ConfigEntry,
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
        except Exception as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disconnect MSD from the server."""
        try:
            await self.coordinator.client.set_msd_connected(False)
        except Exception as err:
            raise HomeAssistantError(str(err)) from err


class PikvmUsbConnectionSwitch(PikvmEntity, SwitchEntity):
    """Switch to connect/disconnect main USB to the server.

    Maps to the __v3_usb_breaker__ (or similar) GPIO channel.
    This is the "Connect main USB to server" toggle in the PiKVM web UI.
    """

    _attr_name = "USB Connection"
    _attr_icon = "mdi:usb"

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: ConfigEntry,
        channel_name: str,
    ) -> None:
        """Initialize the USB connection switch."""
        super().__init__(coordinator, entry)
        self._channel_name = channel_name
        self._attr_unique_id = f"{entry.entry_id}_usb_connection"

    @property
    def is_on(self) -> bool | None:
        """Return True when USB is connected to server."""
        if self.coordinator.data is None:
            return None
        outputs = self.coordinator.data.get("gpio", {}).get("outputs", {})
        channel = outputs.get(self._channel_name, {})
        return channel.get("state")

    @property
    def available(self) -> bool:
        """Return if the USB breaker channel is online."""
        if self.coordinator.data is None:
            return False
        outputs = self.coordinator.data.get("gpio", {}).get("outputs", {})
        channel = outputs.get(self._channel_name, {})
        return channel.get("online", False) and super().available

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Connect USB to the server."""
        try:
            await self.coordinator.client.gpio_switch(self._channel_name, True)
        except Exception as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disconnect USB from the server."""
        try:
            await self.coordinator.client.gpio_switch(self._channel_name, False)
        except Exception as err:
            raise HomeAssistantError(str(err)) from err
