# PiKVM Power Control for Home Assistant

A Home Assistant custom integration to control ATX power via a [PiKVM](https://pikvm.org/) device.

## Features

- **ATX Power Button** — Send power button presses to your PiKVM-connected server
- **TOTP Authentication** — Supports PiKVM's two-factor authentication
- **Config Flow** — Full UI-based setup, no YAML needed
- **Reauth & Reconfigure** — Update credentials or connection details without removing the integration

## Installation

### HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Search for "PiKVM Power Control" and install
3. Restart Home Assistant
4. Go to **Settings > Devices & Services > Add Integration** and search for "PiKVM Power Control"

### Manual

1. Copy `custom_components/pikvm_power/` to your Home Assistant `config/custom_components/` directory
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

| Entity | Type | Description |
|--------|------|-------------|
| ATX Power | Button | Sends an ATX power button press to the PiKVM device |

## Requirements

- A PiKVM device accessible over the network
- PiKVM credentials with TOTP secret
