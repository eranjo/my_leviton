# My Leviton (USB) — Home Assistant Custom Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories)

**Category:** Integration (HACS)  
**Install via:** HACS (Custom Repository) or manual copy

This repo packages the `my_leviton` integration as a HACS-compatible repository.

## Features
- Local USB communication (requires `pyusb`)
- Implements `light` platform
- No cloud dependency

## Installation (HACS)
1. In Home Assistant, go to **HACS → Integrations → 3‑dot menu → Custom repositories**.
2. Add this repository URL and set **Category** to **Integration**.
3. Click **Add** and then **Install** the **My Leviton (USB)** integration.
4. Restart Home Assistant.
5. Go to **Settings → Devices & Services → Add Integration** and search for **My Leviton (USB)** (or add via YAML if your version prefers file‑based setup).

> If HACS doesn’t automatically discover it, verify the folder structure on your HA host is `config/custom_components/my_leviton/` with the files from this repo, and restart HA again.

## Manual Installation
1. Copy the `custom_components/my_leviton/` folder from this repository into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

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
      usb_utils.py
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

© 2025 Your Name. See [LICENSE](LICENSE).
