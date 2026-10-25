"""Select platform for PiKVM Control — KVM port selection."""

from __future__ import annotations

import logging
import sys
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PikvmApiError, PikvmAuthError, PikvmConnectionError
from .coordinator import PikvmConfigEntry, PikvmDataUpdateCoordinator
from .entity import PikvmEntity, detect_kvm_ports

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PikvmConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PiKVM select entities."""
    coordinator = entry.runtime_data

    if coordinator.data is None:
        return

    gpio_model = coordinator.data.get("gpio_model", {})
    gpio_labels = coordinator.data.get("gpio_labels", {})
    kvm_ports = detect_kvm_ports(gpio_model, gpio_labels)

    entities: list[SelectEntity] = []

    if kvm_ports:
        entities.append(PikvmKvmPortSelect(coordinator, entry, kvm_ports))

    # MSD image select (always add if MSD is available)
    msd = coordinator.data.get("msd", {})
    if msd.get("enabled", True):
        entities.append(PikvmMsdImageSelect(coordinator, entry))

    streamer = coordinator.data.get("streamer", {})
    if streamer.get("features", {}).get("resolution"):
        entities.append(PikvmResolutionSelect(coordinator, entry))

    hid = coordinator.data.get("hid", {})
    if hid.get("mouse_outputs", {}).get("available"):
        entities.append(PikvmMouseOutputSelect(coordinator, entry))
    if hid.get("keyboard_outputs", {}).get("available"):
        entities.append(PikvmKeyboardOutputSelect(coordinator, entry))

    async_add_entities(entities)


class PikvmKvmPortSelect(PikvmEntity, SelectEntity):
    """Select entity for KVM port switching."""

    _attr_translation_key = "kvm_port"
    _attr_icon = "mdi:monitor-multiple"

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: PikvmConfigEntry,
        ports: list[dict[str, Any]],
    ) -> None:
        """Initialize the KVM port select."""
        super().__init__(coordinator, entry)
        self._ports = ports
        self._attr_unique_id = f"{entry.entry_id}_kvm_port"
        self._attr_options = [p["label"] for p in ports]

    @property
    def current_option(self) -> str | None:
        """Return the currently active KVM port."""
        if self.coordinator.data is None:
            return None

        inputs = self.coordinator.data.get("gpio", {}).get("inputs", {})
        for port in self._ports:
            channel_state = inputs.get(port["led_channel"], {})
            if channel_state.get("state"):
                return port["label"]

        return None

    async def async_select_option(self, option: str) -> None:
        """Switch to the selected KVM port."""
        for port in self._ports:
            if port["label"] == option:
                try:
                    await self.coordinator.client.gpio_pulse(
                        port["button_channel"], port["pulse_delay"]
                    )
                except (PikvmAuthError, PikvmConnectionError, PikvmApiError) as err:
                    raise HomeAssistantError(
                        f"Failed to switch KVM to {option}: {err}"
                    ) from err
                return

        raise HomeAssistantError(f"Unknown KVM port: {option}")


class PikvmMsdImageSelect(PikvmEntity, SelectEntity):
    """Select entity for choosing which ISO image to mount via MSD."""

    _attr_translation_key = "msd_image"
    _attr_icon = "mdi:disc"

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: PikvmConfigEntry,
    ) -> None:
        """Initialize the MSD image select."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_msd_image"

    @property
    def options(self) -> list[str]:
        """Return available ISO images."""
        if self.coordinator.data is None:
            return []
        return self.coordinator.data.get("msd", {}).get("images", [])

    @property
    def current_option(self) -> str | None:
        """Return the currently selected image."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("msd", {}).get("image")

    async def async_select_option(self, option: str) -> None:
        """Select an image: disconnect → set params → reconnect."""
        msd = self.coordinator.data.get("msd", {}) if self.coordinator.data else {}
        was_connected = msd.get("connected", False)

        client = self.coordinator.client
        try:
            # Must disconnect before changing params
            if was_connected:
                await client.set_msd_connected(False)

            # Set the image (keep current cdrom/rw settings)
            await client.set_msd_params(
                image=option,
                cdrom=msd.get("cdrom", True),
                rw=msd.get("rw", False),
            )
        except (PikvmAuthError, PikvmConnectionError, PikvmApiError) as err:
            raise HomeAssistantError(
                f"Failed to select MSD image '{option}': {err}"
            ) from err
        finally:
            # Reconnect if it was connected before
            if was_connected:
                try:
                    await client.set_msd_connected(True)
                except (PikvmAuthError, PikvmConnectionError, PikvmApiError) as err:
                    if sys.exc_info()[1] is None:
                        raise HomeAssistantError(
                            f"Failed to reconnect MSD after selecting '{option}': {err}"
                        ) from err
                    # Keep the original error as the reported cause
                    _LOGGER.warning(
                        "Failed to reconnect MSD after selecting '%s': %s", option, err
                    )


class PikvmResolutionSelect(PikvmEntity, SelectEntity):
    """Select entity for the streamer capture resolution."""

    _attr_translation_key = "resolution"
    _attr_icon = "mdi:monitor-screenshot"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: PikvmConfigEntry,
    ) -> None:
        """Initialize the resolution select."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_resolution_select"

    @property
    def options(self) -> list[str]:
        """Return available resolutions."""
        if self.coordinator.data is None:
            return []
        limits = self.coordinator.data.get("streamer", {}).get("limits", {})
        return list(limits.get("available_resolutions", []))

    @property
    def current_option(self) -> str | None:
        """Return the current resolution."""
        if self.coordinator.data is None:
            return None
        params = self.coordinator.data.get("streamer", {}).get("params", {})
        return params.get("resolution")

    async def async_select_option(self, option: str) -> None:
        """Set the streamer resolution."""
        try:
            await self.coordinator.client.set_streamer_params(resolution=option)
        except (PikvmAuthError, PikvmConnectionError, PikvmApiError) as err:
            raise HomeAssistantError(
                f"Failed to set resolution '{option}': {err}"
            ) from err
        params = self.coordinator.data.get("streamer", {}).get("params", {})
        self.coordinator.push_partial(
            "streamer", params={**params, "resolution": option}
        )


class _PikvmHidOutputSelect(PikvmEntity, SelectEntity):
    """Base select for HID keyboard/mouse output."""

    _attr_entity_category = EntityCategory.CONFIG
    _outputs_key = ""
    _client_arg = ""

    def __init__(
        self,
        coordinator: PikvmDataUpdateCoordinator,
        entry: PikvmConfigEntry,
    ) -> None:
        """Initialize the HID output select."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_{self._client_arg}_output"

    def _outputs(self) -> dict[str, Any]:
        if self.coordinator.data is None:
            return {}
        return self.coordinator.data.get("hid", {}).get(self._outputs_key, {})

    @property
    def options(self) -> list[str]:
        """Return available outputs."""
        return list(self._outputs().get("available", []))

    @property
    def current_option(self) -> str | None:
        """Return the active output."""
        return self._outputs().get("active")

    async def async_select_option(self, option: str) -> None:
        """Set the HID output."""
        try:
            await self.coordinator.client.set_hid_output(**{self._client_arg: option})
        except (PikvmAuthError, PikvmConnectionError, PikvmApiError) as err:
            raise HomeAssistantError(
                f"Failed to set {self._client_arg} output '{option}': {err}"
            ) from err
        self.coordinator.push_partial(
            "hid", **{self._outputs_key: {**self._outputs(), "active": option}}
        )


class PikvmMouseOutputSelect(_PikvmHidOutputSelect):
    """Select entity for the HID mouse output."""

    _attr_translation_key = "mouse_output"
    _attr_icon = "mdi:mouse"
    _outputs_key = "mouse_outputs"
    _client_arg = "mouse"


class PikvmKeyboardOutputSelect(_PikvmHidOutputSelect):
    """Select entity for the HID keyboard output."""

    _attr_translation_key = "keyboard_output"
    _attr_icon = "mdi:keyboard"
    _outputs_key = "keyboard_outputs"
    _client_arg = "keyboard"
