# PiKVM Control for Home Assistant

A comprehensive Home Assistant custom integration for [PiKVM](https://pikvm.org/) devices. Real-time state updates via WebSocket.

## Features

- **ATX Power Control** — Power, power long (force shutdown), and reset buttons
- **System Health Monitoring** — CPU temperature, CPU usage, memory usage
- **Throttling Detection** — Undervoltage, frequency capping, and thermal throttling binary sensors
- **HID Control** — Jiggler toggle, HID connect/disconnect switch
- **Mass Storage Device** — MSD connect/disconnect switch
- **GPIO** — Auto-discovered GPIO channels as switches (outputs) and binary sensors (inputs)
- **Keyboard Services** — Send keyboard shortcuts and type text via HA services
- **Real-Time Updates** — WebSocket push for instant state changes (no polling delay)
- **Config Flow** — Full UI-based setup with reauth and reconfigure support
- **TOTP Authentication** — Supports PiKVM's two-factor authentication

## Installation

### HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Search for "PiKVM Control" and install
3. Restart Home Assistant
4. Go to **Settings > Devices & Services > Add Integration** and search for "PiKVM Control"

### Manual

1. Copy `custom_components/pikvm/` to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant
3. Add the integration via the UI

## Configuration

During setup you'll be asked for:

| Field | Description |
|-------|-------------|
| **URL** | Base URL of your PiKVM device (e.g., `https://pikvm.local`) |
| **Username** | PiKVM username (default: `admin`) |
| **Password** | PiKVM password |
| **TOTP Secret** | The TOTP secret key for two-factor authentication |
| **Verify SSL** | Whether to verify SSL certificates (default: off) |

## Entities

### Buttons
| Entity | Description |
|--------|-------------|
| ATX Power | Short power button press |
| ATX Power Long | Long power button press (force shutdown) |
| ATX Reset | Reset button press |

### Binary Sensors
| Entity | Description |
|--------|-------------|
| Power LED | Server power state (from ATX power LED) |
| HDD Activity | HDD activity LED state |
| Undervoltage | PiKVM undervoltage detected (diagnostic) |
| Frequency Capped | CPU frequency capping active (diagnostic) |
| Throttled | CPU thermal throttling active (diagnostic) |
| GPIO [name] | Auto-discovered GPIO input channels |

### Sensors
| Entity | Description |
|--------|-------------|
| CPU Temperature | PiKVM CPU temperature |
| CPU Usage | PiKVM CPU usage percentage |
| Memory Usage | PiKVM memory usage percentage |

### Switches
| Entity | Description |
|--------|-------------|
| HID Jiggler | Toggle mouse jiggler (prevents remote system sleep) |
| HID Connected | Connect/disconnect HID (keyboard/mouse) from remote system |
| MSD Connected | Connect/disconnect virtual USB drive |
| GPIO [name] | Auto-discovered GPIO output channels with switch capability |

## Services

### `pikvm.send_shortcut`
Send a keyboard shortcut to the remote system.

| Field | Description |
|-------|-------------|
| device_id | Target PiKVM device |
| keys | Comma-separated key names (e.g., `ControlLeft,AltLeft,Delete`) |

### `pikvm.type_text`
Type a text string on the remote system.

| Field | Description |
|-------|-------------|
| device_id | Target PiKVM device |
| text | The text to type |

## Requirements

- A PiKVM device accessible over the network
- PiKVM credentials with TOTP secret
