"""Base entity for PiKVM Control."""

from __future__ import annotations

import re
from typing import Any

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


def detect_kvm_ports(
    gpio_model: dict[str, Any], gpio_labels: dict[str, str]
) -> list[dict[str, Any]]:
    """Detect ezcoo-style KVM port groups from GPIO model.

    Looks for paired input/output channels with matching chN_ prefix
    using the same driver, where outputs are pulse-only (switch=false).

    Returns list of port dicts:
        [{"label": "Helios (Desktop)", "led_channel": "ch0_led",
          "button_channel": "ch0_button", "pulse_delay": 0.1}, ...]
    Returns empty list if no KVM pattern detected.
    """
    inputs = gpio_model.get("inputs", {})
    outputs = gpio_model.get("outputs", {})

    # Group channels by chN_ prefix
    input_by_prefix: dict[str, str] = {}
    for name in inputs:
        match = re.match(r"^(ch\d+)_", name)
        if match:
            input_by_prefix[match.group(1)] = name

    output_by_prefix: dict[str, tuple[str, dict]] = {}
    for name, config in outputs.items():
        if name.startswith("__"):
            continue
        match = re.match(r"^(ch\d+)_", name)
        if match:
            output_by_prefix[match.group(1)] = (name, config)

    # Find paired channels
    common_prefixes = sorted(set(input_by_prefix) & set(output_by_prefix))
    if len(common_prefixes) < 2:
        return []

    # Verify all outputs are pulse-only (not switch)
    for prefix in common_prefixes:
        _, config = output_by_prefix[prefix]
        if config.get("switch", False):
            return []
        if not config.get("pulse"):
            return []

    # Build port list
    ports = []
    for prefix in common_prefixes:
        led_channel = input_by_prefix[prefix]
        button_channel, config = output_by_prefix[prefix]
        label = gpio_labels.get(led_channel) or gpio_labels.get(button_channel) or prefix
        pulse_delay = config.get("pulse", {}).get("delay", 0.1)

        ports.append({
            "label": label,
            "led_channel": led_channel,
            "button_channel": button_channel,
            "pulse_delay": pulse_delay,
        })

    return ports


def get_kvm_channel_names(kvm_ports: list[dict[str, Any]]) -> set[str]:
    """Get set of all channel names used by KVM ports."""
    names: set[str] = set()
    for port in kvm_ports:
        names.add(port["led_channel"])
        names.add(port["button_channel"])
    return names


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
