"""PiKVM API client for HTTP and WebSocket communication."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import time
from typing import Any

import aiohttp
import pyotp

_LOGGER = logging.getLogger(__name__)


class PikvmAuthError(Exception):
    """Raised when PiKVM authentication fails."""


class PikvmConnectionError(Exception):
    """Raised when PiKVM connection fails."""


class PikvmApiError(Exception):
    """Raised when PiKVM returns a non-OK response."""


def normalize_totp_secret(secret: str | None) -> str:
    """Strip whitespace and upper-case a TOTP secret."""
    return "".join((secret or "").split()).upper()


def validate_totp_secret(secret: str) -> bool:
    """Return True if the secret is empty or valid base32."""
    normalized = normalize_totp_secret(secret)
    if not normalized:
        return True
    padded = normalized + "=" * (-len(normalized) % 8)
    try:
        base64.b32decode(padded)
    except (binascii.Error, ValueError):
        return False
    return True


class PikvmApiClient:
    """Client for PiKVM HTTP API and WebSocket."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        username: str,
        password: str,
        totp_secret: str | None = None,
        verify_ssl: bool = False,
        http_timeout: int = 10,
    ) -> None:
        """Initialize the PiKVM API client."""
        self._session = session
        self._url = url.rstrip("/")
        self._username = username
        self._password = password
        self._http_timeout = http_timeout
        secret = normalize_totp_secret(totp_secret)
        self._totp: pyotp.TOTP | None = pyotp.TOTP(secret) if secret else None

    def _full_password(self) -> str:
        """Return password with current TOTP code appended (if configured)."""
        if self._totp is None:
            return self._password
        return f"{self._password}{self._totp.now()}"

    def _auth(self) -> aiohttp.BasicAuth:
        """Build BasicAuth with current TOTP code appended to password."""
        return aiohttp.BasicAuth(self._username, self._full_password())

    def _auth_headers(self) -> dict[str, str]:
        """Build X-KVMD auth headers with current TOTP."""
        return {
            "X-KVMD-User": self._username,
            "X-KVMD-Passwd": self._full_password(),
        }

    async def _wait_fresh_code(self) -> None:
        """Wait for the next TOTP window if the current one is about to end."""
        if self._totp is None:
            return
        interval = self._totp.interval
        remaining = interval - time.time() % interval
        if remaining < 2:
            await asyncio.sleep(remaining + 0.1)

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make an authenticated HTTP request to PiKVM."""
        url = f"{self._url}{path}"
        _LOGGER.debug("PiKVM API: %s %s", method, path)
        for attempt in (0, 1):
            try:
                async with self._session.request(
                    method,
                    url,
                    auth=self._auth(),
                    timeout=aiohttp.ClientTimeout(total=self._http_timeout),
                    **kwargs,
                ) as resp:
                    _LOGGER.debug(
                        "PiKVM API response: %s %s -> HTTP %d",
                        method,
                        path,
                        resp.status,
                    )
                    if resp.status in (401, 403):
                        if attempt == 0 and self._totp is not None:
                            await self._wait_fresh_code()
                            continue
                        raise PikvmAuthError(
                            f"Authentication failed (HTTP {resp.status})"
                        )
                    if resp.status != 200:
                        text = await resp.text()
                        raise PikvmApiError(f"API error: HTTP {resp.status}: {text}")
                    data = await resp.json()
                    # PiKVM may return 200 with ok:false on errors
                    if not data.get("ok", True):
                        result = data.get("result", {})
                        error_msg = result.get("error_msg", "Unknown error")
                        _LOGGER.error(
                            "PiKVM API error on %s: %s (%s)",
                            path,
                            error_msg,
                            result.get("error", ""),
                        )
                        raise PikvmApiError(f"PiKVM error: {error_msg}")
                    return data
            except (PikvmAuthError, PikvmApiError):
                raise
            except TimeoutError as err:
                raise PikvmConnectionError(
                    f"Timeout connecting to PiKVM: {err}"
                ) from err
            except aiohttp.ClientError as err:
                raise PikvmConnectionError(f"Connection error: {err}") from err
        raise PikvmAuthError("Authentication failed")  # pragma: no cover

    async def _request_raw(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> bytes:
        """Make an authenticated HTTP request returning raw bytes."""
        url = f"{self._url}{path}"
        for attempt in (0, 1):
            try:
                async with self._session.request(
                    method,
                    url,
                    auth=self._auth(),
                    timeout=aiohttp.ClientTimeout(total=self._http_timeout),
                    **kwargs,
                ) as resp:
                    if resp.status in (401, 403):
                        if attempt == 0 and self._totp is not None:
                            await self._wait_fresh_code()
                            continue
                        raise PikvmAuthError(
                            f"Authentication failed (HTTP {resp.status})"
                        )
                    if resp.status != 200:
                        raise PikvmApiError(f"API error: HTTP {resp.status}")
                    return await resp.read()
            except (PikvmAuthError, PikvmApiError):
                raise
            except TimeoutError as err:
                raise PikvmConnectionError(
                    f"Timeout connecting to PiKVM: {err}"
                ) from err
            except aiohttp.ClientError as err:
                raise PikvmConnectionError(f"Connection error: {err}") from err
        raise PikvmAuthError("Authentication failed")  # pragma: no cover

    # --- Connection test ---

    async def test_connection(self) -> None:
        """Test the connection to PiKVM. Raises on failure."""
        await self._request("GET", "/api/info")

    # --- Pollable state (used by coordinator for initial state) ---

    async def get_info(self) -> dict[str, Any]:
        """Get full PiKVM info."""
        data = await self._request("GET", "/api/info")
        return data.get("result", {})

    async def get_atx_state(self) -> dict[str, Any]:
        """Get ATX power state."""
        data = await self._request("GET", "/api/atx")
        return data.get("result", {})

    async def get_system_info(self) -> dict[str, Any]:
        """Get system hardware info (CPU, memory, throttling)."""
        data = await self._request("GET", "/api/info", params={"fields": "hw"})
        return data.get("result", {})

    async def get_hid_state(self) -> dict[str, Any]:
        """Get HID device state."""
        data = await self._request("GET", "/api/hid")
        return data.get("result", {})

    async def get_msd_state(self) -> dict[str, Any]:
        """Get MSD state."""
        data = await self._request("GET", "/api/msd")
        return data.get("result", {})

    async def get_gpio_state(self) -> dict[str, Any]:
        """Get GPIO state and model."""
        data = await self._request("GET", "/api/gpio")
        return data.get("result", {})

    # --- ATX actions ---

    async def atx_click(self, button: str) -> None:
        """Simulate ATX button press (power, power_long, reset)."""
        await self._request("POST", "/api/atx/click", params={"button": button})

    async def atx_power(self, action: str) -> None:
        """Control ATX power (on, off, off_hard, reset_hard)."""
        await self._request("POST", "/api/atx/power", params={"action": action})

    # --- HID actions ---

    async def set_hid_jiggler(self, enabled: bool) -> None:
        """Enable or disable HID jiggler."""
        await self._request(
            "POST", "/api/hid/set_params", params={"jiggler": "1" if enabled else "0"}
        )

    async def set_hid_connected(self, connected: bool) -> None:
        """Connect or disconnect HID."""
        await self._request(
            "POST",
            "/api/hid/set_connected",
            params={"connected": "1" if connected else "0"},
        )

    async def reset_hid(self) -> None:
        """Reset HID to default state."""
        await self._request("POST", "/api/hid/reset")

    async def send_shortcut(self, keys: str) -> None:
        """Send a keyboard shortcut (comma-separated key names)."""
        await self._request(
            "POST", "/api/hid/events/send_shortcut", params={"keys": keys}
        )

    async def type_text(self, text: str, keymap: str = "en") -> None:
        """Type text on the remote system."""
        await self._request(
            "POST", "/api/hid/print", params={"keymap": keymap}, data=text
        )

    # --- MSD actions ---

    async def set_msd_connected(self, connected: bool) -> None:
        """Connect or disconnect MSD."""
        await self._request(
            "POST",
            "/api/msd/set_connected",
            params={"connected": "1" if connected else "0"},
        )

    async def set_msd_params(
        self, image: str, cdrom: bool = True, rw: bool = False
    ) -> None:
        """Set MSD parameters (image, cdrom mode, rw mode).

        MSD must be disconnected before calling this.
        """
        await self._request(
            "POST",
            "/api/msd/set_params",
            params={
                "image": image,
                "cdrom": "1" if cdrom else "0",
                "rw": "1" if rw else "0",
            },
        )

    # --- GPIO actions ---

    async def gpio_switch(self, channel: str, state: bool) -> None:
        """Set a GPIO output channel state."""
        await self._request(
            "POST",
            "/api/gpio/switch",
            params={"channel": channel, "state": "1" if state else "0"},
        )

    async def gpio_pulse(self, channel: str, delay: float = 0) -> None:
        """Pulse a GPIO output channel."""
        await self._request(
            "POST",
            "/api/gpio/pulse",
            params={"channel": channel, "delay": str(delay)},
        )

    # --- Snapshot ---

    async def get_snapshot(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes:
        """Fetch a JPEG snapshot from the video streamer."""
        params: dict[str, str] = {"allow_offline": "1"}
        if width:
            params["preview"] = "1"
            params["preview_max_width"] = str(width)
        if height:
            params["preview_max_height"] = str(height)
        return await self._request_raw("GET", "/api/streamer/snapshot", params=params)

    # --- WebSocket ---

    async def connect_ws(self) -> aiohttp.ClientWebSocketResponse:
        """Establish WebSocket connection for real-time state updates.

        Returns the WebSocket connection. The caller is responsible for
        reading events and closing the connection.
        """
        ws_url = self._url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/api/ws"

        for attempt in (0, 1):
            try:
                return await self._session.ws_connect(
                    ws_url,
                    params={"stream": "0"},
                    headers=self._auth_headers(),
                    heartbeat=30,
                )
            except aiohttp.WSServerHandshakeError as err:
                if err.status in (401, 403):
                    if attempt == 0 and self._totp is not None:
                        await self._wait_fresh_code()
                        continue
                    raise PikvmAuthError(
                        f"WebSocket authentication failed (HTTP {err.status})"
                    ) from err
                raise PikvmConnectionError(
                    f"WebSocket handshake failed: {err}"
                ) from err
            except TimeoutError as err:
                raise PikvmConnectionError(
                    f"WebSocket connection timed out: {err}"
                ) from err
            except aiohttp.ClientError as err:
                raise PikvmConnectionError(
                    f"WebSocket connection failed: {err}"
                ) from err
        raise PikvmAuthError("WebSocket authentication failed")  # pragma: no cover
