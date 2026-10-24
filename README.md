# My Leviton (USB) — Home Assistant Custom Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories)

**Category:** Integration (HACS)  
**Install via:** HACS (Custom Repository) or manual copy

This repo packages the `my_leviton` integration as a HACS-compatible repository.

## Beta (1.1.0b6)
Each dimmer gets a **dim start** number entity (0–100%). Set it to the first hardware level where the lamp lights (Leviton low-end trim). The light brightness slider then maps onto that range → 100%.

Example: `number.tyohuone_katto_dim_start` = `60` → HA 1% sends device level 60, HA 100% sends 100.

## Still to do
- Implement automatic discovery of devices on the bus network
- Optional auto-probe for dim start
- Additional tasks to be determined...

## Features
- Local USB communication (requires `pyusb`)
- Implements `light` platform (on/off relays + brightness dimmers)
- CRC-8/MAXIM checksum when building USB packets
- No cloud dependency

## Installation

[![Open HACS repository](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=eranjo&repository=my_leviton)

### Manually
Copy `custom_components/my_leviton/` to `/config/custom_components/my_leviton` in Home Assistant.

## Configuration

[![Add Integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=my_leviton)
## Installation (HACS)
1. In Home Assistant, go to **HACS → Integrations → 3‑dot menu → Custom repositories**.
2. Add this repository URL and set **Category** to **Integration**.
3. Click **Add** and then **Install** the **My Leviton (USB)** integration.
4. Restart Home Assistant.
5. Go to **Settings → Devices & Services → Add Integration** and search for **My Leviton (USB)** (or add via YAML if your version prefers file‑based setup).

> If HACS doesn’t automatically discover it, verify the folder structure on your HA host is `config/custom_components/my_leviton/` with the files from this repo, and restart HA again.

## Directory Structure
```
custom_components/
  my_leviton/
    __init__.py
    light.py
    manifest.json
    utils/
      __init__.py
      commands.py
      constants.py
      locks.py
      protocol.py
      usb_utils.py
tests/
  test_protocol.py
hacs.json
README.md
LICENSE
```

## Requirements
- Home Assistant Core 2023.0+ (recommended)
- Python package: `pyusb` (installed automatically via `manifest.json` requirements)

## Notes
- Ensure the HA user has permission to access the USB device (e.g., udev rules on Linux).
- If you move between ports, you may need to re‑identify the device.

---

© 2025 eranjo. See [LICENSE](LICENSE).
