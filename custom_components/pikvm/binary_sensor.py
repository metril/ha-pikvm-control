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
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import CONF_HDD_HOLD_TIME, DEFAULT_HDD_HOLD_TIME, DOMAIN
from .coordinator import PikvmDataUpdateCoordinator
from .entity import PikvmEntity, detect_kvm_ports, get_kvm_channel_names, gpio_display_name


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

    entities: list[BinarySensorEntity] = []
    for desc in BINARY_SENSORS:
        if desc.key == "hdd_activity":
            entities.append(PikvmHddActivityBinarySensor(coordinator, entry, desc))
        else:
            entities.append(PikvmBinarySensor(coordinator, entry, desc))

    # Add GPIO input channels as binary sensors (skip internal and KVM channels)
    gpio_model = coordinator.data.get("gpio_model", {}) if coordinator.data else {}
    gpio_labels = coordinator.data.get("gpio_labels", {}) if coordinator.data else {}
    kvm_channels = get_kvm_channel_names(detect_kvm_ports(gpio_model, gpio_labels))

    for channel_name in gpio_model.get("inputs", {}):
        if channel_name.startswith("__") or channel_name in kvm_channels:
            continue
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


class PikvmHddActivityBinarySensor(PikvmBinarySensor):
    """HDD activity sensor with hold timer to smooth rapid flickering."""

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: ConfigEntry,
        description: PikvmBinarySensorDescription,
    ) -> None:
        """Initialize the HDD activity sensor."""
        super().__init__(coordinator, entry, description)
        self._hold_timer: CALLBACK_TYPE | None = None
        self._entry = entry

    @property
    def is_on(self) -> bool | None:
        """Return the held sensor state."""
        return self._attr_is_on

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator data update with hold timer logic."""
        if self.coordinator.data is None:
            self._cancel_timer()
            self._attr_is_on = None
            self.async_write_ha_state()
            return

        raw_value = self.entity_description.value_fn(self.coordinator.data)

        if raw_value:
            self._attr_is_on = True
            self._cancel_timer()
            hold_time = self._entry.options.get(
                CONF_HDD_HOLD_TIME, DEFAULT_HDD_HOLD_TIME
            )
            self._hold_timer = async_call_later(
                self.hass, hold_time, self._timer_expired
            )
            self.async_write_ha_state()
        # If raw_value is False/None, do nothing — the timer will handle OFF

    def _timer_expired(self, _now: Any) -> None:
        """Handle hold timer expiration."""
        self._hold_timer = None
        self._attr_is_on = False
        self.async_write_ha_state()

    def _cancel_timer(self) -> None:
        """Cancel any active hold timer."""
        if self._hold_timer is not None:
            self._hold_timer()
            self._hold_timer = None

    async def async_will_remove_from_hass(self) -> None:
        """Cancel timer on entity removal."""
        self._cancel_timer()
        await super().async_will_remove_from_hass()


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
