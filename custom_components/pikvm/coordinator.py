"""WebSocket-based coordinator for PiKVM Control."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PikvmApiClient, PikvmApiError, PikvmAuthError, PikvmConnectionError
from .const import (
    CONF_WS_RECONNECT_DELAY,
    DEFAULT_WS_RECONNECT_DELAY,
    MAX_WS_RECONNECT_DELAY,
)

_LOGGER = logging.getLogger(__name__)

type PikvmConfigEntry = ConfigEntry[PikvmDataUpdateCoordinator]


class PikvmDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that maintains a WebSocket connection to PiKVM.

    Receives real-time state updates via WebSocket events.
    No polling — state is pushed by PiKVM when it changes.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: PikvmConfigEntry,
        client: PikvmApiClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"PiKVM {entry.title}",
            # No update_interval — we use WebSocket push, not polling
        )
        self.client = client
        self._ws_failures = 0
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ws_task: asyncio.Task | None = None
        self._state: dict[str, Any] = {
            "atx": {},
            "system": {},
            "hid": {},
            "msd": {},
            "gpio": {"inputs": {}, "outputs": {}},
            "gpio_model": {"inputs": {}, "outputs": {}},
            "gpio_labels": {},
            "info": {},
        }

    async def async_start(self) -> None:
        """Start the WebSocket connection."""
        self._ws_task = self.config_entry.async_create_background_task(
            self.hass,
            self._ws_loop(),
            name=f"pikvm_ws_{self.config_entry.entry_id}",
            eager_start=True,
        )

    async def async_stop(self) -> None:
        """Stop the WebSocket connection."""
        task, self._ws_task = self._ws_task, None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        ws, self._ws = self._ws, None
        if ws and not ws.closed:
            await ws.close()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch initial state via HTTP (state is then pushed via WebSocket)."""
        try:
            atx, hw_info, hid, msd, gpio, info = await asyncio.gather(
                self.client.get_atx_state(),
                self.client.get_system_info(),
                self.client.get_hid_state(),
                self.client.get_msd_state(),
                self.client.get_gpio_state(),
                self.client.get_info(),
            )
        except PikvmAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (PikvmConnectionError, PikvmApiError, TimeoutError) as err:
            raise UpdateFailed(str(err)) from err

        self._process_atx_event(atx)
        self._process_hw_event(hw_info)
        self._process_hid_event(hid)
        self._process_msd_event(msd)
        self._process_gpio_full(gpio)
        self._process_info(info)

        return dict(self._state)

    def _process_info(self, info: dict[str, Any]) -> None:
        """Extract version/model/hostname from the /api/info result."""
        system = info.get("system") or {}
        platform = (info.get("hw") or {}).get("platform") or {}
        server = (info.get("meta") or {}).get("server") or {}
        kvmd = system.get("kvmd") or {}
        self._state["info"] = {
            "version": kvmd.get("version"),
            "model": platform.get("model") or platform.get("type"),
            "platform": platform.get("base") or platform.get("type"),
            "hostname": server.get("host"),
        }

    async def _ws_loop(self) -> None:
        """Maintain WebSocket connection with automatic reconnect."""
        while True:
            try:
                await self._ws_connect_and_listen()
                err: Exception = ConnectionError("WebSocket closed by server")
            except PikvmAuthError as auth_err:
                _LOGGER.error("PiKVM WebSocket auth failed: %s", auth_err)
                if self.last_update_success:
                    self.async_set_update_error(auth_err)
                self.config_entry.async_start_reauth(self.hass)
                return  # Stop reconnecting on auth failure
            except Exception as ex:  # noqa: BLE001 - keep the loop alive on any error
                err = ex

            base = self.config_entry.options.get(
                CONF_WS_RECONNECT_DELAY, DEFAULT_WS_RECONNECT_DELAY
            )
            delay = min(base * 2**self._ws_failures, MAX_WS_RECONNECT_DELAY)
            self._ws_failures += 1
            _LOGGER.warning(
                "PiKVM WebSocket disconnected: %s. Reconnecting in %ds", err, delay
            )
            if self.last_update_success:
                self.async_set_update_error(err)
            await asyncio.sleep(delay)

    async def _ws_connect_and_listen(self) -> None:
        """Connect to WebSocket and process events."""
        self._ws = await self.client.connect_ws()
        self._ws_failures = 0
        _LOGGER.info("PiKVM WebSocket connected")

        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._process_ws_message(msg.json())
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.error("PiKVM WebSocket error: %s", self._ws.exception())
                    break
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    break
        finally:
            if self._ws and not self._ws.closed:
                await self._ws.close()
            self._ws = None

    @callback
    def _process_ws_message(self, data: dict[str, Any]) -> None:
        """Process a WebSocket message and update state."""
        event_type = data.get("event_type", "")
        event = data.get("event", {})
        _LOGGER.debug("PiKVM WS event: %s", event_type)

        updated = False

        if event_type in ("atx_state", "atx"):
            self._process_atx_event(event)
            updated = True
        elif event_type in ("info_hw_state", "info"):
            self._process_hw_event({"hw": event} if "hw" not in event else event)
            updated = True
        elif event_type in ("hid_state", "hid"):
            self._process_hid_event(event)
            updated = True
        elif event_type in ("msd_state", "msd"):
            self._process_msd_event(event)
            updated = True
        elif event_type in ("gpio_state", "gpio"):
            self._process_gpio_state_event(event)
            updated = True
        elif event_type in ("gpio_model_state", "gpio_model"):
            self._process_gpio_model_event(event)
            updated = True
        elif event_type == "loop":
            _LOGGER.debug("PiKVM WebSocket initial state bundle complete")
            self.async_set_updated_data(dict(self._state))

        if updated:
            self.async_set_updated_data(dict(self._state))

    def _process_atx_event(self, event: dict[str, Any]) -> None:
        """Process ATX state (merges partial updates)."""
        current = self._state.get("atx", {})

        if "busy" in event:
            current["busy"] = event["busy"]
        if "enabled" in event:
            current["enabled"] = event["enabled"]
        if "leds" in event:
            current_leds = current.get("leds", {})
            leds = event["leds"]
            if "power" in leds:
                current_leds["power"] = leds["power"]
            if "hdd" in leds:
                current_leds["hdd"] = leds["hdd"]
            current["leds"] = current_leds

        self._state["atx"] = current

    def _process_hw_event(self, event: dict[str, Any]) -> None:
        """Process hardware info state (merges partial updates)."""
        hw = event.get("hw", event)
        health = hw.get("health", {})
        current = self._state.get("system", {})

        temp = health.get("temp", {})
        if "cpu" in temp:
            current["cpu_temp"] = temp["cpu"]

        cpu = health.get("cpu", {})
        if "percent" in cpu:
            current["cpu_percent"] = cpu["percent"]

        mem = health.get("mem", {})
        if "percent" in mem:
            current["mem_percent"] = mem["percent"]

        throttling = health.get("throttling", {})
        parsed = throttling.get("parsed_flags", {})
        if parsed:
            current_throttling = current.get("throttling", {})
            if "undervoltage" in parsed:
                current_throttling["undervoltage"] = parsed["undervoltage"].get(
                    "now", False
                )
            if "freq_capped" in parsed:
                current_throttling["freq_capped"] = parsed["freq_capped"].get(
                    "now", False
                )
            if "throttled" in parsed:
                current_throttling["throttled"] = parsed["throttled"].get("now", False)
            current["throttling"] = current_throttling

        self._state["system"] = current

    def _process_hid_event(self, event: dict[str, Any]) -> None:
        """Process HID state (merges partial WebSocket updates)."""
        current = self._state.get("hid", {"connected": False, "jiggler": False})

        if "connected" in event:
            current["connected"] = event["connected"]

        if "jiggler" in event:
            jiggler = event["jiggler"]
            if isinstance(jiggler, dict):
                current["jiggler"] = jiggler.get(
                    "enabled", current.get("jiggler", False)
                )
            else:
                current["jiggler"] = bool(jiggler)

        self._state["hid"] = current

    def _process_msd_event(self, event: dict[str, Any]) -> None:
        """Process MSD state (merges partial updates)."""
        current = self._state.get("msd", {})

        drive = event.get("drive", {})
        if "connected" in drive:
            current["connected"] = drive["connected"]
        if "image" in drive:
            current["image"] = drive["image"]
        if "cdrom" in drive:
            current["cdrom"] = drive["cdrom"]
        if "rw" in drive:
            current["rw"] = drive["rw"]

        if "enabled" in event:
            current["enabled"] = event["enabled"]

        storage = event.get("storage", {})
        if "images" in storage:
            current["images"] = list(storage["images"].keys())

        self._state["msd"] = current

    def _process_gpio_full(self, event: dict[str, Any]) -> None:
        """Process full GPIO response (from HTTP GET /api/gpio)."""
        model = event.get("model", {})
        scheme = model.get("scheme", {})
        state = event.get("state", {})

        self._state["gpio_model"] = {
            "inputs": scheme.get("inputs", {}),
            "outputs": scheme.get("outputs", {}),
        }
        self._state["gpio"] = {
            "inputs": state.get("inputs", {}),
            "outputs": state.get("outputs", {}),
        }

        # Parse view.table for human-readable channel labels
        # Each row can have a label + multiple channels (input and output)
        # All channels in the same row share the same label
        labels: dict[str, str] = {}
        table = model.get("view", {}).get("table", [])
        for row in table:
            if not isinstance(row, list):
                continue
            label_text = None
            channel_names: list[str] = []
            for cell in row:
                if not isinstance(cell, dict):
                    continue
                if cell.get("type") == "label" and "text" in cell:
                    label_text = cell["text"]
                elif cell.get("type") in ("input", "output") and "channel" in cell:
                    channel_names.append(cell["channel"])
            if label_text:
                for ch in channel_names:
                    labels[ch] = label_text
        self._state["gpio_labels"] = labels

    def _process_gpio_state_event(self, event: dict[str, Any]) -> None:
        """Process GPIO state update from WebSocket."""
        if "inputs" in event:
            self._state["gpio"]["inputs"].update(event["inputs"])
            _LOGGER.debug("GPIO inputs updated: %s", event["inputs"])
        if "outputs" in event:
            self._state["gpio"]["outputs"].update(event["outputs"])
            _LOGGER.debug("GPIO outputs updated: %s", event["outputs"])
        if not event.get("inputs") and not event.get("outputs"):
            _LOGGER.debug("GPIO state event with unexpected structure: %s", event)

    def _process_gpio_model_event(self, event: dict[str, Any]) -> None:
        """Process GPIO model update from WebSocket."""
        scheme = event.get("scheme", event)
        if "inputs" in scheme:
            self._state["gpio_model"]["inputs"].update(scheme["inputs"])
        if "outputs" in scheme:
            self._state["gpio_model"]["outputs"].update(scheme["outputs"])
