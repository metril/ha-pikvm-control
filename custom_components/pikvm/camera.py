"""Camera platform for PiKVM Control — snapshot-based."""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PikvmApiError, PikvmAuthError, PikvmConnectionError
from .coordinator import PikvmConfigEntry, PikvmDataUpdateCoordinator
from .entity import PikvmEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PikvmConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PiKVM camera entity."""
    coordinator = entry.runtime_data
    async_add_entities([PikvmCamera(coordinator, entry)])


class PikvmCamera(PikvmEntity, Camera):
    """Camera entity that fetches snapshots from PiKVM."""

    _attr_has_entity_name = True
    _attr_translation_key = "screen"
    _attr_icon = "mdi:monitor-screenshot"

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: PikvmConfigEntry,
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
        except (PikvmAuthError, PikvmConnectionError, PikvmApiError):
            _LOGGER.debug("Failed to fetch PiKVM snapshot", exc_info=True)
            return None
