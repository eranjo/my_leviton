# My Leviton (USB) — Home Assistant Custom Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories)

**Category:** Integration (HACS)  
**Install via:** HACS (Custom Repository) or manual copy

This repo packages the `my_leviton` integration as a HACS-compatible repository.

## Required hardware

This integration requires a **116A00-1 Omni-Bus Network USB Programming Interface** (OB161, HAI / Leviton). The code opens that stick as USB vendor id `04d8`, product id `fe93`. Other USB adapters are not supported.

## Stable 1.1.0

First stable of the 1.1 line (after `1.1.0b*`). Includes:

- Correct dimmer level encoding and relay ON behavior
- Fast HA-app toggles (optimistic SET + background readback)
- Wall-button / bus state sync via continuous short USB listen bursts
- Shared post-SET status readback so HA matches reported levels
- Per-dimmer **dim start** number entity (0–100%) for hardware low-end trim

### Reverse-engineering actions (optional)

Developer Tools → Actions → paste YAML → Run:

```yaml
action: my_leviton.probe_level
data:
  light: Office Ceiling
  level: 50
  wait: 20
```

```yaml
action: my_leviton.dump_bus
data:
  duration: 30
```

```yaml
action: my_leviton.listen_dimmer
data:
  light: Office Ceiling
  duration: 90
```

| Action | Output file |
|---|---|
| `my_leviton.probe_level` | `/config/my_leviton_probe.log` |
| `my_leviton.dump_bus` | `/config/my_leviton_bus_dump.log` |
| `my_leviton.listen_dimmer` | `/config/my_leviton_capture.log` |

## Still to do
- Optional auto-probe for dim start

## Features
- Local USB communication (requires `pyusb`)
- Implements `light` platform (on/off relays + brightness dimmers)
- Scans the 116A00-1 bus and matches addresses to the local inventory while keeping existing Home Assistant names
- CRC-8/MAXIM checksum when building USB packets
- No cloud dependency
- Fast toggles, wall-listener state sync, and USB capture / dump / probe services
## Installation

[![Open HACS repository](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=eranjo&repository=my_leviton)

### Manually
Copy `custom_components/my_leviton/` to `/config/custom_components/my_leviton` in Home Assistant.

## Configuration

The public repo ships **example** light names only (`Office Ceiling`, `Living Room Wall`, …). Edit your private inventory via either:

1. `custom_components/my_leviton/utils/constants_local.py` defining `LIGHTS` (gitignored), or
2. `/config/my_leviton_lights.yaml` (preferred; survives HACS updates) or `custom_components/my_leviton/my_leviton_lights.yaml` with a top-level `lights:` list.

Do not commit real home device names or bus IDs to GitHub.

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
