# CLAUDE.md — ha-pikvm-control

## Project Overview

Home Assistant custom integration for PiKVM (KVM over IP) devices. Real-time state via WebSocket, no polling.

GitHub: https://github.com/metril/ha-pikvm-control

## Architecture

```
HA Core (integration) --WebSocket--> PiKVM device (wss://.../api/ws?stream=0)
                      --HTTP-------> PiKVM device (initial state + actions)
```

- **WebSocket** for real-time state push (ATX, GPIO, HID, MSD, system info)
- **HTTP** for initial state fetch and action commands (power, GPIO, HID, MSD)
- **TOTP auth** appended to password on every request (rotates every 30s)
- `stream=0` parameter prevents PiKVM video streamer from starting on WS connect

## Key Files

| File | Purpose |
|------|---------|
| `custom_components/pikvm/api.py` | HTTP + WebSocket client, TOTP auth |
| `custom_components/pikvm/coordinator.py` | WebSocket-based coordinator, state merging |
| `custom_components/pikvm/entity.py` | Base entity + GPIO helpers (display names, KVM detection) |
| `custom_components/pikvm/select.py` | KVM Port, MSD Image, Resolution, Mouse/Keyboard Output selects |
| `custom_components/pikvm/number.py` | Desired FPS, JPEG Quality, H.264 Bitrate/GOP |
| `custom_components/pikvm/switch.py` | Jiggler, USB Connection, MSD Connected, GPIO switches |
| `custom_components/pikvm/button.py` | ATX Power Short/Long/Reset + GPIO pulse buttons |
| `custom_components/pikvm/camera.py` | Snapshot-based camera from /api/streamer/snapshot |

## Entities

**Buttons**: ATX Power Short, ATX Power Long, ATX Reset, HID Reset, Streamer Reset, GPIO pulse buttons
**Binary Sensors**: Power LED, HDD Activity, Undervoltage, Frequency Capped, Throttled, GPIO inputs, HDMI Signal, Keyboard Online, Mouse Online, WebSocket Connected
**Sensors**: Resolution, Captured FPS, kvmd Version, Hostname; CPU Temperature, CPU Usage, Memory Usage (diagnostic)
**Switches**: HID Jiggler, USB Connection (from `__v3_usb_breaker__` GPIO), MSD Connected, GPIO output switches
**Selects**: KVM Port (auto-detected from ezcoo GPIO pattern), MSD Image, Resolution (only if `features.resolution`), Mouse Output, Keyboard Output
**Numbers**: Desired FPS, JPEG Quality, H.264 Bitrate, H.264 GOP (last two disabled by default)
**Camera**: Screen (JPEG snapshots)
**Services**: `pikvm.send_shortcut`, `pikvm.type_text`

## Critical Design Decisions & Gotchas

### WebSocket event names do NOT have `_state` suffix
PiKVM sends `gpio`, `atx`, `hid`, `msd`, `info` — NOT `gpio_state`, `atx_state`, etc. The coordinator matches both formats for safety. **This was the root cause of all state update failures in early versions.**

### All state handlers must MERGE, never replace
WebSocket sends **partial updates** — only changed fields. Every `_process_*_event()` handler must merge into existing state, not replace the dict. Replacing causes unchanged fields to become `None` → sensors oscillate between value and "unknown".

### USB Connection is `__v3_usb_breaker__` GPIO, not `/api/hid/set_connected`
The "Connect main USB to server" feature in PiKVM web UI uses the `__v3_usb_breaker__` GPIO channel. The `/api/hid/set_connected` API is a no-op on most hardware (`connected` returns `null`). The integration detects `usb_breaker` in GPIO channel names and creates a dedicated switch.

### KVM port auto-detection
`detect_kvm_ports()` in `entity.py` identifies ezcoo-style KVM switches by finding paired `chN_led` (input) + `chN_button` (output, pulse-only) channels with the same driver. Creates a single `select` entity instead of individual buttons + sensors. Requires ≥2 matched ports.

### GPIO labels from `view.table`
Each row in `model.view.table` can have a label + multiple channels (input AND output). All channels in the same row share the label. Parser must assign label to ALL channels in the row, not just the last one.

### Internal GPIO channels
Channels prefixed with `__` (like `__v3_usb_breaker__`) are internal. Most are filtered from GPIO entity creation EXCEPT `usb_breaker` which gets its own dedicated switch.

### MSD workflow
Must disconnect before changing params. The MSD Image select handles: disconnect → set_params → reconnect automatically. MSD Connected switch is unavailable until an image is selected.

### HID Jiggler optimistic update
WebSocket may not push jiggler state changes. The switch does an optimistic update after successful API call — directly modifies coordinator data and calls `async_set_updated_data`.

### PiKVM API `ok` field
PiKVM returns HTTP 200 but `ok: false` in the JSON body on errors. The `_request()` method checks this field and raises `PikvmApiError` with the actual error message.

### TOTP parameter format
HID jiggler: `?jiggler=0` or `?jiggler=1` (not true/false)
HID connected: `?connected=0` or `?connected=1`
MSD connected: `?connected=0` or `?connected=1`

### Resolution select only when `features.resolution`
The streamer reports `features.resolution` only on USB-capture PiKVMs (V0/V1/V2). On V3/V4 resolution is EDID-bound (follows the host) and cannot be set, so no select is created.

### Streamer WS event
Event is `streamer` / `streamer_state`. `streamer: null` means capture stopped (resolution/FPS/HDMI signal become unknown).

### Copy-on-write handlers
Handlers are copy-on-write and return bool changed; publish only on change. Use `coordinator.push_partial` for optimistic updates.

### keep_video_active
Option `keep_video_active` = WebSocket opened with `stream=1` so kvmd keeps capture running. Costs PiKVM CPU.

### Releases
Releases are automatic: bump the manifest version, merge to main, `release.yml` tags.

## Coordinator State Shape

```python
{
    "atx": {"busy": bool, "enabled": bool, "leds": {"power": bool, "hdd": bool}},
    "system": {"cpu_temp": float, "cpu_percent": float, "mem_percent": float,
               "throttling": {"undervoltage": bool, "freq_capped": bool, "throttled": bool}},
    "hid": {"connected": bool | None, "jiggler": bool,
            "keyboard_online": bool, "mouse_online": bool,
            "keyboard_outputs": {"available": list, "active": str | None},
            "mouse_outputs": {"available": list, "active": str | None}},
    "msd": {"connected": bool, "image": str | None, "cdrom": bool, "rw": bool,
            "enabled": bool, "images": list[str]},
    "gpio": {"inputs": {channel: {"online": bool, "state": bool}},
             "outputs": {channel: {"busy": bool, "online": bool, "state": bool}}},
    "gpio_model": {"inputs": {channel: {...}}, "outputs": {channel: {...}}},
    "gpio_labels": {channel_name: display_label},
    "streamer": {"features": {}, "limits": {}, "params": {}, "online": bool,
                 "resolution": ..., "captured_fps": ..., "clients": ...},
    "info": {"kvmd_version", "hostname", "model", "base", "serial"},
    "ws_connected": bool,
}
```

## Git Conventions

- No AI attribution in commits
- GitHub user: metril
- Email: 1517921+metril@users.noreply.github.com
