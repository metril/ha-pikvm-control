"""WebSocket-based coordinator for PiKVM Control."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PikvmApiClient, PikvmApiError, PikvmAuthError, PikvmConnectionError
from .const import (
    CONF_KEEP_VIDEO_ACTIVE,
    CONF_WS_RECONNECT_DELAY,
    DEFAULT_KEEP_VIDEO_ACTIVE,
    DEFAULT_WS_RECONNECT_DELAY,
    DOMAIN,
    MAX_WS_RECONNECT_DELAY,
)

_LOGGER = logging.getLogger(__name__)

type PikvmConfigEntry = ConfigEntry[PikvmDataUpdateCoordinator]

WS_STABLE_SECONDS = 60  # connection lasting this long resets the backoff
WS_UNAVAILABLE_GRACE = 30  # entities stay available this long after WS loss


class PikvmDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that maintains a WebSocket connection to PiKVM.

    Receives real-time state updates via WebSocket events.
    No polling - state is pushed by PiKVM when it changes.

    State sections are copy-on-write: a handler never mutates a dict that has
    been published, it builds a new one and swaps it in only when it differs.
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
            # No update_interval - we use WebSocket push, not polling
        )
        self.client = client
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ws_task: asyncio.Task | None = None
        self._ws_connected_at: float | None = None
        self._ws_disconnected_since: float | None = None
        self._avail_unsub: Callable[[], None] | None = None
        self._initial_done = False
        self._state: dict[str, Any] = {
            "atx": {},
            "system": {},
            "hid": {},
            "msd": {},
            "gpio": {"inputs": {}, "outputs": {}},
            "gpio_model": {"inputs": {}, "outputs": {}},
            "gpio_labels": {},
            "streamer": {
                "features": {},
                "limits": {},
                "params": {},
                "online": False,
                "resolution": None,
                "captured_fps": None,
                "clients": None,
            },
            "info": {
                "kvmd_version": None,
                "hostname": None,
                "model": None,
                "base": None,
                "serial": None,
            },
            "ws_connected": False,
        }

    # --- lifecycle ---

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
        if self._avail_unsub:
            self._avail_unsub()
            self._avail_unsub = None
        task, self._ws_task = self._ws_task, None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        ws, self._ws = self._ws, None
        if ws and not ws.closed:
            await ws.close()

    @property
    def ws_available(self) -> bool:
        """Return True unless the WebSocket has been down past the grace period."""
        if self._state["ws_connected"] or self._ws_disconnected_since is None:
            return True
        return time.monotonic() - self._ws_disconnected_since < WS_UNAVAILABLE_GRACE

    # --- publishing helpers ---

    def _publish(self) -> None:
        """Publish a shallow snapshot of the state."""
        self.async_set_updated_data({**self._state})

    def _set_section(self, section: str, new: Any) -> bool:
        """Swap in a new section value if it differs. Return True if changed."""
        if new == self._state.get(section):
            return False
        self._state[section] = new
        return True

    def push_partial(self, section: str, **fields: Any) -> None:
        """Copy-on-write merge of fields into a section; publish if changed."""
        new = {**self._state.get(section, {}), **fields}
        if self._set_section(section, new):
            self._publish()

    # --- HTTP fetch ---

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch initial state via HTTP (state is then pushed via WebSocket)."""
        try:
            await self._fetch_http_state()
        except PikvmAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (PikvmConnectionError, PikvmApiError, TimeoutError) as err:
            raise UpdateFailed(str(err)) from err
        self._initial_done = True
        return {**self._state}

    async def _fetch_http_state(self) -> None:
        """Fetch full state over HTTP and merge it into the state."""
        atx, info, hid, msd, gpio = await asyncio.gather(
            self.client.get_atx_state(),
            self.client.get_system_info(),
            self.client.get_hid_state(),
            self.client.get_msd_state(),
            self.client.get_gpio_state(),
        )
        self._process_atx_event(atx)
        self._process_info(info)
        self._process_hid_event(hid)
        self._process_msd_event(msd)
        self._process_gpio_full(gpio)

        try:
            streamer = await self.client.get_streamer_state()
        except (PikvmApiError, PikvmConnectionError, PikvmAuthError) as err:
            _LOGGER.debug("PiKVM streamer state unavailable: %s", err)
        else:
            self._process_streamer_event(streamer)

    # --- WebSocket loop ---

    async def _ws_loop(self) -> None:
        """Maintain WebSocket connection with automatic reconnect."""
        delay: float | None = None
        while True:
            self._ws_connected_at = None
            try:
                await self._ws_connect_and_listen()
                err: Exception = ConnectionError("WebSocket closed by server")
            except PikvmAuthError as auth_err:
                _LOGGER.error("PiKVM WebSocket auth failed: %s", auth_err)
                self._mark_ws_down()
                if self.last_update_success:
                    self.async_set_update_error(auth_err)
                self.config_entry.async_start_reauth(self.hass)
                return  # Stop reconnecting on auth failure
            except Exception as ex:  # noqa: BLE001 - keep the loop alive on any error
                err = ex

            self._mark_ws_down()
            base = self.config_entry.options.get(
                CONF_WS_RECONNECT_DELAY, DEFAULT_WS_RECONNECT_DELAY
            )
            lasted = (
                time.monotonic() - self._ws_connected_at
                if self._ws_connected_at is not None
                else 0
            )
            if delay is None or lasted >= WS_STABLE_SECONDS:
                delay = base
            _LOGGER.warning(
                "PiKVM WebSocket disconnected: %s. Reconnecting in %ds", err, delay
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_WS_RECONNECT_DELAY)

    def _mark_ws_down(self) -> None:
        """Record WS disconnect, publish, and schedule an availability re-check."""
        if self._ws_disconnected_since is None:
            self._ws_disconnected_since = time.monotonic()
        changed = self._set_section("ws_connected", False)
        if changed:
            self._publish()
        if self._avail_unsub is None:
            self._avail_unsub = async_call_later(
                self.hass, WS_UNAVAILABLE_GRACE + 1, self._recheck_availability
            )

    @callback
    def _recheck_availability(self, _now: Any = None) -> None:
        """Re-publish so entities re-evaluate ws_available after the grace period."""
        self._avail_unsub = None
        if not self._state["ws_connected"]:
            self._publish()

    async def _ws_connect_and_listen(self) -> None:
        """Connect to WebSocket and process events."""
        stream = bool(
            self.config_entry.options.get(
                CONF_KEEP_VIDEO_ACTIVE, DEFAULT_KEEP_VIDEO_ACTIVE
            )
        )
        self._ws = await self.client.connect_ws(stream=stream)
        _LOGGER.info("PiKVM WebSocket connected")
        self._ws_connected_at = time.monotonic()
        self._ws_disconnected_since = None
        if self._avail_unsub:
            self._avail_unsub()
            self._avail_unsub = None
        self._state["ws_connected"] = True
        try:
            await self._fetch_http_state()
        except PikvmAuthError:
            raise
        except Exception as err:  # noqa: BLE001 - WS still provides state
            _LOGGER.warning("PiKVM state refresh after reconnect failed: %s", err)
        self._publish()

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
        """Process a WebSocket message and publish if anything changed."""
        event_type = data.get("event_type", "")
        event = data.get("event") or {}
        _LOGGER.debug("PiKVM WS event: %s", event_type)

        changed = False
        if event_type in ("atx_state", "atx"):
            changed = self._process_atx_event(event)
        elif event_type in ("info_hw_state", "info"):
            if any(k in event for k in ("hw", "system", "meta")):
                changed = self._process_info(event)
            else:
                changed = self._process_info({"hw": event})
        elif event_type == "info_system_state":
            changed = self._process_info({"system": event})
        elif event_type == "info_meta_state":
            changed = self._process_info({"meta": event})
        elif event_type in ("hid_state", "hid"):
            changed = self._process_hid_event(event)
        elif event_type in ("msd_state", "msd"):
            changed = self._process_msd_event(event)
        elif event_type in ("gpio_state", "gpio"):
            changed = self._process_gpio_state_event(event)
        elif event_type in ("gpio_model_state", "gpio_model"):
            changed = self._process_gpio_model_event(event)
        elif event_type in ("streamer", "streamer_state"):
            changed = self._process_streamer_event(event)
        elif event_type == "loop":
            _LOGGER.debug("PiKVM WebSocket initial state bundle complete")
            changed = True

        if changed:
            self._publish()

    def _process_atx_event(self, event: dict[str, Any]) -> bool:
        """Process ATX state (merges partial updates)."""
        new = {**self._state.get("atx", {})}
        for key in ("busy", "enabled"):
            if key in event:
                new[key] = event[key]
        if "leds" in event:
            leds = {**new.get("leds", {})}
            for key in ("power", "hdd"):
                if key in event["leds"]:
                    leds[key] = event["leds"][key]
            new["leds"] = leds
        return self._set_section("atx", new)

    def _process_info(self, info: dict[str, Any]) -> bool:
        """Process an /api/info-shaped dict with optional hw/system/meta."""
        changed = False
        if "hw" in info:
            changed |= self._process_hw_event({"hw": info["hw"] or {}})
        fields: dict[str, Any] = {}
        if "system" in info:
            kvmd = ((info["system"] or {}).get("kvmd")) or {}
            if kvmd.get("version") is not None:
                fields["kvmd_version"] = kvmd["version"]
        if "meta" in info:
            host = (((info["meta"] or {}).get("server")) or {}).get("host")
            if host is not None:
                fields["hostname"] = host
        changed |= self._merge_info(fields)
        return changed

    def _merge_info(self, fields: dict[str, Any]) -> bool:
        """Merge non-None fields into the info section; sync device registry."""
        old = self._state["info"]
        new = {**old, **{k: v for k, v in fields.items() if v is not None}}
        if not self._set_section("info", new):
            return False
        if self._initial_done and any(
            new[k] != old.get(k) for k in ("kvmd_version", "model", "base")
        ):
            self._update_device_registry(new)
        return True

    def _update_device_registry(self, info: dict[str, Any]) -> None:
        """Push new version/model to the device registry."""
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(
            identifiers={(DOMAIN, self.config_entry.entry_id)}
        )
        if device is None:
            return
        kwargs: dict[str, Any] = {}
        if info.get("kvmd_version"):
            kwargs["sw_version"] = info["kvmd_version"]
        if model := info.get("model") or info.get("base"):
            kwargs["model"] = model
        if kwargs:
            registry.async_update_device(device.id, **kwargs)

    def _process_hw_event(self, event: dict[str, Any]) -> bool:
        """Process hardware info state (merges partial updates)."""
        hw = event.get("hw", event)
        health = hw.get("health") or {}
        new = {**self._state.get("system", {})}

        temp = health.get("temp") or {}
        if "cpu" in temp:
            new["cpu_temp"] = temp["cpu"]
        cpu = health.get("cpu") or {}
        if "percent" in cpu:
            new["cpu_percent"] = cpu["percent"]
        mem = health.get("mem") or {}
        if "percent" in mem:
            new["mem_percent"] = mem["percent"]

        parsed = (health.get("throttling") or {}).get("parsed_flags") or {}
        if parsed:
            throttling = {**new.get("throttling", {})}
            for key in ("undervoltage", "freq_capped", "throttled"):
                if key in parsed:
                    throttling[key] = parsed[key].get("now", False)
            new["throttling"] = throttling

        changed = self._set_section("system", new)

        platform = hw.get("platform") or {}
        if platform:
            changed |= self._merge_info(
                {
                    "model": platform.get("model"),
                    "base": platform.get("base"),
                    "serial": platform.get("serial"),
                }
            )
        return changed

    def _process_hid_event(self, event: dict[str, Any]) -> bool:
        """Process HID state (merges partial WebSocket updates)."""
        new = {**self._state.get("hid", {"connected": False, "jiggler": False})}

        if "connected" in event:
            new["connected"] = event["connected"]

        if "jiggler" in event:
            jiggler = event["jiggler"]
            if isinstance(jiggler, dict):
                new["jiggler"] = jiggler.get("enabled", new.get("jiggler", False))
            else:
                new["jiggler"] = bool(jiggler)

        for dev in ("keyboard", "mouse"):
            sub = event.get(dev)
            if not isinstance(sub, dict):
                continue
            if "online" in sub:
                new[f"{dev}_online"] = sub["online"]
            outputs = sub.get("outputs")
            if isinstance(outputs, dict):
                prev = new.get(f"{dev}_outputs") or {}
                new[f"{dev}_outputs"] = {
                    "available": list(
                        outputs.get("available", prev.get("available", []))
                    ),
                    "active": outputs.get("active", prev.get("active")),
                }

        return self._set_section("hid", new)

    def _process_msd_event(self, event: dict[str, Any]) -> bool:
        """Process MSD state (merges partial updates)."""
        new = {**self._state.get("msd", {})}

        drive = event.get("drive") or {}
        for key in ("connected", "image", "cdrom", "rw"):
            if key in drive:
                new[key] = drive[key]
        if "enabled" in event:
            new["enabled"] = event["enabled"]
        storage = event.get("storage") or {}
        if "images" in storage:
            new["images"] = list(storage["images"].keys())

        return self._set_section("msd", new)

    def _process_streamer_event(self, event: dict[str, Any]) -> bool:
        """Process streamer state (partial merge; `streamer: null` = offline)."""
        new = {**self._state["streamer"]}
        for key in ("features", "limits", "params"):
            if isinstance(event.get(key), dict):
                new[key] = {**new.get(key, {}), **event[key]}

        if "streamer" in event:
            ss = event["streamer"]
            if ss is None:
                new.update(
                    online=False, resolution=None, captured_fps=None, clients=None
                )
            else:
                source = ss.get("source") or {}
                new["online"] = bool(source.get("online"))
                res = source.get("resolution")
                if isinstance(res, dict) and "width" in res and "height" in res:
                    new["resolution"] = f"{res['width']}x{res['height']}"
                new["captured_fps"] = source.get("captured_fps")
                new["clients"] = (ss.get("stream") or {}).get("clients")

        return self._set_section("streamer", new)

    def _process_gpio_full(self, event: dict[str, Any]) -> bool:
        """Process full GPIO response (from HTTP GET /api/gpio)."""
        model = event.get("model") or {}
        scheme = model.get("scheme") or {}
        state = event.get("state") or {}

        changed = self._set_section(
            "gpio_model",
            {
                "inputs": dict(scheme.get("inputs", {})),
                "outputs": dict(scheme.get("outputs", {})),
            },
        )
        changed |= self._set_section(
            "gpio",
            {
                "inputs": dict(state.get("inputs", {})),
                "outputs": dict(state.get("outputs", {})),
            },
        )

        # Parse view.table for human-readable channel labels
        # Each row can have a label + multiple channels (input and output)
        # All channels in the same row share the same label
        labels: dict[str, str] = {}
        for row in (model.get("view") or {}).get("table", []):
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
        changed |= self._set_section("gpio_labels", labels)
        return changed

    def _merge_channels(
        self, section: str, update: dict[str, Any], deep: bool
    ) -> bool:
        """Copy-on-write merge of inputs/outputs channel dicts into a section."""
        old = self._state[section]
        new = {**old}
        for kind in ("inputs", "outputs"):
            if kind not in update:
                continue
            channels = {**old.get(kind, {})}
            for name, val in update[kind].items():
                if deep and isinstance(val, dict):
                    channels[name] = {**channels.get(name, {}), **val}
                else:
                    channels[name] = val
            new[kind] = channels
        return self._set_section(section, new)

    def _process_gpio_state_event(self, event: dict[str, Any]) -> bool:
        """Process GPIO state update from WebSocket."""
        if not event.get("inputs") and not event.get("outputs"):
            _LOGGER.debug("GPIO state event with unexpected structure: %s", event)
        return self._merge_channels("gpio", event, deep=True)

    def _process_gpio_model_event(self, event: dict[str, Any]) -> bool:
        """Process GPIO model update from WebSocket."""
        return self._merge_channels("gpio_model", event.get("scheme", event), deep=False)
