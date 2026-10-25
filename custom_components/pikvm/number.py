"""Number platform for PiKVM Control — streamer settings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PikvmApiError, PikvmAuthError, PikvmConnectionError
from .coordinator import PikvmConfigEntry, PikvmDataUpdateCoordinator
from .entity import PikvmEntity


@dataclass(frozen=True)
class PikvmNumberDescription(NumberEntityDescription):
    """Describes a PiKVM streamer number."""

    param: str = ""
    feature: str | None = None
    fallback_min: float = 0
    fallback_max: float = 100


NUMBERS: tuple[PikvmNumberDescription, ...] = (
    PikvmNumberDescription(
        key="desired_fps",
        translation_key="desired_fps",
        param="desired_fps",
        native_unit_of_measurement="fps",
        fallback_min=0,
        fallback_max=70,
        icon="mdi:video",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.SLIDER,
        native_step=1,
    ),
    PikvmNumberDescription(
        key="quality",
        translation_key="quality",
        param="quality",
        feature="quality",
        native_unit_of_measurement=PERCENTAGE,
        fallback_min=1,
        fallback_max=100,
        icon="mdi:image-filter-hdr",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.SLIDER,
        native_step=1,
    ),
    PikvmNumberDescription(
        key="h264_bitrate",
        translation_key="h264_bitrate",
        param="h264_bitrate",
        feature="h264",
        native_unit_of_measurement="kbit/s",
        fallback_min=25,
        fallback_max=20000,
        icon="mdi:speedometer",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        mode=NumberMode.SLIDER,
        native_step=1,
    ),
    PikvmNumberDescription(
        key="h264_gop",
        translation_key="h264_gop",
        param="h264_gop",
        feature="h264",
        fallback_min=0,
        fallback_max=60,
        icon="mdi:group",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        mode=NumberMode.SLIDER,
        native_step=1,
    ),
)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PikvmConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PiKVM number entities."""
    coordinator = entry.runtime_data
    streamer = (coordinator.data or {}).get("streamer", {})
    features = streamer.get("features", {})

    async_add_entities(
        PikvmStreamerNumber(coordinator, entry, desc)
        for desc in NUMBERS
        if desc.feature is None or features.get(desc.feature)
    )


class PikvmStreamerNumber(PikvmEntity, NumberEntity):
    """A PiKVM streamer parameter."""

    entity_description: PikvmNumberDescription

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: PikvmConfigEntry,
        description: PikvmNumberDescription,
    ) -> None:
        """Initialize the number."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    def _limits(self) -> dict[str, Any]:
        if self.coordinator.data is None:
            return {}
        limits = self.coordinator.data.get("streamer", {}).get("limits", {})
        return limits.get(self.entity_description.param) or {}

    @property
    def native_min_value(self) -> float:
        """Return the minimum value."""
        value = self._limits().get("min")
        return self.entity_description.fallback_min if value is None else value

    @property
    def native_max_value(self) -> float:
        """Return the maximum value."""
        value = self._limits().get("max")
        return self.entity_description.fallback_max if value is None else value

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        if self.coordinator.data is None:
            return None
        params = self.coordinator.data.get("streamer", {}).get("params", {})
        return params.get(self.entity_description.param)

    async def async_set_native_value(self, value: float) -> None:
        """Set the streamer parameter."""
        param = self.entity_description.param
        try:
            await self.coordinator.client.set_streamer_params(**{param: int(value)})
        except (PikvmAuthError, PikvmConnectionError, PikvmApiError) as err:
            raise HomeAssistantError(f"Failed to set {param}: {err}") from err
        params = self.coordinator.data.get("streamer", {}).get("params", {})
        self.coordinator.push_partial("streamer", params={**params, param: int(value)})
