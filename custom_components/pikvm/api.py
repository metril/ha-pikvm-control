"""PiKVM API client for HTTP and WebSocket communication."""

from __future__ import annotations

import logging
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


class PikvmApiClient:
    """Client for PiKVM HTTP API and WebSocket."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        username: str,
        password: str,
        totp_secret: str,
        verify_ssl: bool = False,
    ) -> None:
        """Initialize the PiKVM API client."""
        self._session = session
        self._url = url.rstrip("/")
        self._username = username
        self._password = password
        self._totp_secret = totp_secret
        self._verify_ssl = verify_ssl
        self._totp = pyotp.TOTP(totp_secret)

    def _auth(self) -> aiohttp.BasicAuth:
        """Build BasicAuth with current TOTP code appended to password."""
        full_password = f"{self._password}{self._totp.now()}"
        return aiohttp.BasicAuth(self._username, full_password)

    def _auth_headers(self) -> dict[str, str]:
        """Build X-KVMD auth headers with current TOTP."""
        full_password = f"{self._password}{self._totp.now()}"
        return {
            "X-KVMD-User": self._username,
            "X-KVMD-Passwd": full_password,
        }

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make an authenticated HTTP request to PiKVM."""
        url = f"{self._url}{path}"
        _LOGGER.debug("PiKVM API: %s %s", method, path)
        try:
            async with self._session.request(
                method,
                url,
                auth=self._auth(),
                ssl=self._verify_ssl if not self._verify_ssl else None,
                timeout=aiohttp.ClientTimeout(total=10),
                **kwargs,
            ) as resp:
                _LOGGER.debug("PiKVM API response: %s %s -> HTTP %d", method, path, resp.status)
                if resp.status in (401, 403):
                    raise PikvmAuthError(
                        f"Authentication failed (HTTP {resp.status})"
                    )
                if resp.status != 200:
                    text = await resp.text()
                    raise PikvmApiError(
                        f"API error: HTTP {resp.status}: {text}"
                    )
                data = await resp.json()
                # Check PiKVM's ok field — API returns 200 but ok:false on errors
                if not data.get("ok", True):
                    error_msg = data.get("result", {}).get("error_msg", "Unknown error")
                    error_type = data.get("result", {}).get("error", "")
                    _LOGGER.error("PiKVM API error on %s: %s (%s)", path, error_msg, error_type)
                    raise PikvmApiError(f"PiKVM error: {error_msg}")
                return data
        except (PikvmAuthError, PikvmApiError, PikvmConnectionError):
            raise
        except aiohttp.ClientConnectorError as err:
            raise PikvmConnectionError(
                f"Failed to connect to PiKVM: {err}"
            ) from err
        except aiohttp.ClientError as err:
            raise PikvmConnectionError(
                f"Connection error: {err}"
            ) from err

    async def _request_raw(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> bytes:
        """Make an authenticated HTTP request returning raw bytes."""
        url = f"{self._url}{path}"
        try:
            async with self._session.request(
                method,
                url,
                auth=self._auth(),
                ssl=self._verify_ssl if not self._verify_ssl else None,
                timeout=aiohttp.ClientTimeout(total=10),
                **kwargs,
            ) as resp:
                if resp.status in (401, 403):
                    raise PikvmAuthError(
                        f"Authentication failed (HTTP {resp.status})"
                    )
                if resp.status != 200:
                    raise PikvmApiError(
                        f"API error: HTTP {resp.status}"
                    )
                return await resp.read()
        except aiohttp.ClientConnectorError as err:
            raise PikvmConnectionError(
                f"Failed to connect to PiKVM: {err}"
            ) from err
        except aiohttp.ClientError as err:
            raise PikvmConnectionError(
                f"Connection error: {err}"
            ) from err

    # --- Connection test ---

    async def test_connection(self) -> None:
        """Test the connection to PiKVM. Raises on failure."""
        await self._request("GET", "/api/info")

    # --- Pollable state (used by coordinator for initial state) ---

    async def get_atx_state(self) -> dict[str, Any]:
        """Get ATX power state."""
        data = await self._request("GET", "/api/atx")
        return data.get("result", {})

    async def get_system_info(self) -> dict[str, Any]:
        """Get system hardware info (CPU, memory, throttling)."""
        data = await self._request("GET", "/api/info?fields=hw")
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
        await self._request("POST", f"/api/atx/click?button={button}")

    async def atx_power(self, action: str) -> None:
        """Control ATX power (on, off, off_hard, reset_hard)."""
        await self._request("POST", f"/api/atx/power?action={action}")

    # --- HID actions ---

    async def set_hid_jiggler(self, enabled: bool) -> None:
        """Enable or disable HID jiggler."""
        value = "1" if enabled else "0"
        await self._request("POST", f"/api/hid/set_params?jiggler={value}")

    async def set_hid_connected(self, connected: bool) -> None:
        """Connect or disconnect HID."""
        value = "1" if connected else "0"
        await self._request("POST", f"/api/hid/set_connected?connected={value}")

    async def reset_hid(self) -> None:
        """Reset HID to default state."""
        await self._request("POST", "/api/hid/reset")

    async def send_shortcut(self, keys: str) -> None:
        """Send a keyboard shortcut (comma-separated key names)."""
        await self._request("POST", f"/api/hid/events/send_shortcut?keys={keys}")

    async def type_text(self, text: str, keymap: str = "en") -> None:
        """Type text on the remote system."""
        await self._request(
            "POST",
            f"/api/hid/print?keymap={keymap}",
            data=text,
        )

    # --- MSD actions ---

    async def set_msd_connected(self, connected: bool) -> None:
        """Connect or disconnect MSD."""
        value = "1" if connected else "0"
        await self._request("POST", f"/api/msd/set_connected?connected={value}")

    async def set_msd_params(
        self, image: str, cdrom: bool = True, rw: bool = False
    ) -> None:
        """Set MSD parameters (image, cdrom mode, rw mode).

        MSD must be disconnected before calling this.
        """
        cdrom_val = "1" if cdrom else "0"
        rw_val = "1" if rw else "0"
        await self._request(
            "POST",
            f"/api/msd/set_params?image={image}&cdrom={cdrom_val}&rw={rw_val}",
        )

    # --- GPIO actions ---

    async def gpio_switch(self, channel: str, state: bool) -> None:
        """Set a GPIO output channel state."""
        value = "1" if state else "0"
        await self._request(
            "POST", f"/api/gpio/switch?channel={channel}&state={value}"
        )

    async def gpio_pulse(self, channel: str, delay: float = 0) -> None:
        """Pulse a GPIO output channel."""
        await self._request(
            "POST", f"/api/gpio/pulse?channel={channel}&delay={delay}"
        )

    # --- Snapshot ---

    async def get_snapshot(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes:
        """Fetch a JPEG snapshot from the video streamer."""
        params = "?allow_offline=1"
        if width:
            params += f"&preview=1&preview_max_width={width}"
        if height:
            params += f"&preview_max_height={height}"
        return await self._request_raw("GET", f"/api/streamer/snapshot{params}")

    # --- WebSocket ---

    async def connect_ws(self) -> aiohttp.ClientWebSocketResponse:
        """Establish WebSocket connection for real-time state updates.

        Returns the WebSocket connection. The caller is responsible for
        reading events and closing the connection.
        """
        ws_url = self._url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/api/ws?stream=0"

        try:
            ws = await self._session.ws_connect(
                ws_url,
                headers=self._auth_headers(),
                ssl=self._verify_ssl if not self._verify_ssl else None,
                heartbeat=30,
            )
            return ws
        except aiohttp.WSServerHandshakeError as err:
            if err.status in (401, 403):
                raise PikvmAuthError(
                    f"WebSocket authentication failed (HTTP {err.status})"
                ) from err
            raise PikvmConnectionError(
                f"WebSocket handshake failed: {err}"
            ) from err
        except aiohttp.ClientError as err:
            raise PikvmConnectionError(
                f"WebSocket connection failed: {err}"
            ) from err
