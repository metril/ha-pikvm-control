"""Binary sensor platform for PiKVM Control."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PikvmDataUpdateCoordinator
from .entity import PikvmEntity, gpio_display_name


@dataclass(frozen=True)
class PikvmBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a PiKVM binary sensor."""

    value_fn: Callable[[dict[str, Any]], bool | None] = lambda data: None


BINARY_SENSORS: tuple[PikvmBinarySensorDescription, ...] = (
    PikvmBinarySensorDescription(
        key="power_led",
        name="Power LED",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda data: data.get("atx", {}).get("leds", {}).get("power"),
    ),
    PikvmBinarySensorDescription(
        key="hdd_activity",
        name="HDD Activity",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda data: data.get("atx", {}).get("leds", {}).get("hdd"),
    ),
    PikvmBinarySensorDescription(
        key="undervoltage",
        name="Undervoltage",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("system", {}).get("throttling", {}).get("undervoltage"),
    ),
    PikvmBinarySensorDescription(
        key="freq_capped",
        name="Frequency Capped",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("system", {}).get("throttling", {}).get("freq_capped"),
    ),
    PikvmBinarySensorDescription(
        key="throttled",
        name="Throttled",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("system", {}).get("throttling", {}).get("throttled"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PiKVM binary sensor entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: PikvmDataUpdateCoordinator = data["coordinator"]

    entities: list[BinarySensorEntity] = [
        PikvmBinarySensor(coordinator, entry, desc) for desc in BINARY_SENSORS
    ]

    # Add GPIO input channels as binary sensors (skip internal channels)
    gpio_model = coordinator.data.get("gpio_model", {}) if coordinator.data else {}
    for channel_name in gpio_model.get("inputs", {}):
        if not channel_name.startswith("__"):
            entities.append(PikvmGpioInputSensor(coordinator, entry, channel_name))

    async_add_entities(entities)


class PikvmBinarySensor(PikvmEntity, BinarySensorEntity):
    """A PiKVM binary sensor."""

    entity_description: PikvmBinarySensorDescription

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: ConfigEntry,
        description: PikvmBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return the sensor state."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)


class PikvmGpioInputSensor(PikvmEntity, BinarySensorEntity):
    """A PiKVM GPIO input channel as a binary sensor."""

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: ConfigEntry,
        channel_name: str,
    ) -> None:
        """Initialize the GPIO input binary sensor."""
        super().__init__(coordinator, entry)
        self._channel_name = channel_name
        self._attr_unique_id = f"{entry.entry_id}_gpio_in_{channel_name}"
        gpio_labels = coordinator.data.get("gpio_labels", {}) if coordinator.data else {}
        self._attr_name = gpio_display_name(channel_name, gpio_labels)
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        """Return the GPIO input state."""
        if self.coordinator.data is None:
            return None
        inputs = self.coordinator.data.get("gpio", {}).get("inputs", {})
        channel = inputs.get(self._channel_name, {})
        return channel.get("state")

    @property
    def available(self) -> bool:
        """Return if the GPIO channel is online."""
        if self.coordinator.data is None:
            return False
        inputs = self.coordinator.data.get("gpio", {}).get("inputs", {})
        channel = inputs.get(self._channel_name, {})
        return channel.get("online", False) and super().available
