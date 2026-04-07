"""Button platform for PiKVM Control."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PikvmDataUpdateCoordinator
from .entity import PikvmEntity, detect_kvm_ports, get_kvm_channel_names, gpio_display_name


@dataclass(frozen=True)
class PikvmButtonDescription(ButtonEntityDescription):
    """Describes a PiKVM button entity."""

    button_type: str = ""


BUTTONS: tuple[PikvmButtonDescription, ...] = (
    PikvmButtonDescription(
        key="atx_power_short",
        name="ATX Power Short",
        device_class=ButtonDeviceClass.RESTART,
        icon="mdi:power",
        button_type="power",
    ),
    PikvmButtonDescription(
        key="atx_power_long",
        name="ATX Power Long",
        icon="mdi:power-cycle",
        button_type="power_long",
    ),
    PikvmButtonDescription(
        key="atx_reset",
        name="ATX Reset",
        icon="mdi:restart",
        button_type="reset",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PiKVM button entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: PikvmDataUpdateCoordinator = data["coordinator"]

    entities: list[ButtonEntity] = [
        PikvmButton(coordinator, entry, desc) for desc in BUTTONS
    ]

    # Add GPIO output channels with pulse config as buttons (skip internal and KVM channels)
    gpio_model = coordinator.data.get("gpio_model", {}) if coordinator.data else {}
    gpio_labels = coordinator.data.get("gpio_labels", {}) if coordinator.data else {}
    kvm_channels = get_kvm_channel_names(detect_kvm_ports(gpio_model, gpio_labels))

    for channel_name, config in gpio_model.get("outputs", {}).items():
        if channel_name.startswith("__") or channel_name in kvm_channels:
            continue
        if config.get("pulse") and not config.get("switch", False):
            delay = config["pulse"].get("delay", 0)
            entities.append(
                PikvmGpioPulseButton(coordinator, entry, channel_name, delay)
            )

    async_add_entities(entities)


class PikvmButton(PikvmEntity, ButtonEntity):
    """A PiKVM ATX button."""

    entity_description: PikvmButtonDescription

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: ConfigEntry,
        description: PikvmButtonDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    async def async_press(self) -> None:
        """Send ATX button press."""
        try:
            await self.coordinator.client.atx_click(
                self.entity_description.button_type
            )
        except Exception as err:
            raise HomeAssistantError(
                f"Failed to send ATX {self.entity_description.button_type}: {err}"
            ) from err


class PikvmGpioPulseButton(PikvmEntity, ButtonEntity):
    """A PiKVM GPIO output channel as a pulse button."""

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: ConfigEntry,
        channel_name: str,
        delay: float,
    ) -> None:
        """Initialize the GPIO pulse button."""
        super().__init__(coordinator, entry)
        self._channel_name = channel_name
        self._delay = delay
        self._attr_unique_id = f"{entry.entry_id}_gpio_pulse_{channel_name}"
        gpio_labels = coordinator.data.get("gpio_labels", {}) if coordinator.data else {}
        self._attr_name = gpio_display_name(channel_name, gpio_labels)
        self._attr_icon = "mdi:gesture-tap-button"

    async def async_press(self) -> None:
        """Pulse the GPIO channel."""
        try:
            await self.coordinator.client.gpio_pulse(
                self._channel_name, self._delay
            )
        except Exception as err:
            raise HomeAssistantError(
                f"Failed to pulse GPIO {self._channel_name}: {err}"
            ) from err
