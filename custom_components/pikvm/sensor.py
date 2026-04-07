"""Sensor platform for PiKVM Control."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PikvmDataUpdateCoordinator
from .entity import PikvmEntity


@dataclass(frozen=True)
class PikvmSensorDescription(SensorEntityDescription):
    """Describes a PiKVM sensor."""

    value_fn: Callable[[dict[str, Any]], float | None] = lambda data: None


SENSORS: tuple[PikvmSensorDescription, ...] = (
    PikvmSensorDescription(
        key="cpu_temp",
        name="CPU Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: data.get("system", {}).get("cpu_temp"),
    ),
    PikvmSensorDescription(
        key="cpu_usage",
        name="CPU Usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cpu-64-bit",
        value_fn=lambda data: data.get("system", {}).get("cpu_percent"),
    ),
    PikvmSensorDescription(
        key="mem_usage",
        name="Memory Usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:memory",
        value_fn=lambda data: data.get("system", {}).get("mem_percent"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PiKVM sensor entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: PikvmDataUpdateCoordinator = data["coordinator"]

    async_add_entities(
        PikvmSensor(coordinator, entry, desc) for desc in SENSORS
    )


class PikvmSensor(PikvmEntity, SensorEntity):
    """A PiKVM sensor."""

    entity_description: PikvmSensorDescription

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: ConfigEntry,
        description: PikvmSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
