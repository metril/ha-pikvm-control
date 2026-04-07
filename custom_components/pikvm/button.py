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
from .entity import PikvmEntity


@dataclass(frozen=True)
class PikvmButtonDescription(ButtonEntityDescription):
    """Describes a PiKVM button entity."""

    button_type: str = ""


BUTTONS: tuple[PikvmButtonDescription, ...] = (
    PikvmButtonDescription(
        key="atx_power",
        name="ATX Power",
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

    async_add_entities(
        PikvmButton(coordinator, entry, desc) for desc in BUTTONS
    )


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
