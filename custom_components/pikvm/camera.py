"""Camera platform for PiKVM Control — snapshot-based."""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PikvmDataUpdateCoordinator
from .entity import PikvmEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PiKVM camera entity."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: PikvmDataUpdateCoordinator = data["coordinator"]
    async_add_entities([PikvmCamera(coordinator, entry)])


class PikvmCamera(PikvmEntity, Camera):
    """Camera entity that fetches snapshots from PiKVM."""

    _attr_has_entity_name = True
    _attr_name = "Screen"
    _attr_icon = "mdi:monitor-screenshot"

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the camera."""
        Camera.__init__(self)
        PikvmEntity.__init__(self, coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_camera"

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Fetch a snapshot from PiKVM."""
        try:
            return await self.coordinator.client.get_snapshot(width, height)
        except Exception:
            _LOGGER.debug("Failed to fetch PiKVM snapshot", exc_info=True)
            return None
