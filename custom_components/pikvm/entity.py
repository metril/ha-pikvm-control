"""Base entity for PiKVM Control."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_PIKVM_URL, DOMAIN
from .coordinator import PikvmDataUpdateCoordinator


def gpio_display_name(channel_name: str, labels: dict[str, str]) -> str:
    """Get display name for a GPIO channel with 'GPIO:' prefix for grouping."""
    if channel_name in labels:
        return f"GPIO: {labels[channel_name]}"
    return f"GPIO: {channel_name}"


class PikvmEntity(CoordinatorEntity[PikvmDataUpdateCoordinator]):
    """Base entity for PiKVM devices."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="PiKVM",
            configuration_url=entry.data.get(CONF_PIKVM_URL),
        )
