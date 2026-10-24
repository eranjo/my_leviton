# My Leviton (USB) — Home Assistant Custom Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories)

**Category:** Integration (HACS)  
**Install via:** HACS (Custom Repository) or manual copy

This repo packages the `my_leviton` integration as a HACS-compatible repository.

## Beta (1.1.0b8)
**Reverse-engineering actions** — Developer Tools → Actions → paste YAML → Run:

### Single level probe (start here)
```yaml
action: my_leviton.probe_level
data:
  light: Työhuone katto
  level: 50
```

### Level sweep (sent vs reported table)
```yaml
action: my_leviton.probe_level
data:
  light: Työhuone katto
  sweep: true
  step: 5
```

### Dump whole bus
```yaml
action: my_leviton.dump_bus
data:
  duration: 30
```

### Listen while using the wall paddle
```yaml
action: my_leviton.listen_dimmer
data:
  light: Työhuone katto
  duration: 90
```

| Action | Output file |
|---|---|
| `my_leviton.probe_level` | `/config/my_leviton_probe.log` (`PROBE_CSV` / `PROBE_RESULT`) |
| `my_leviton.dump_bus` | `/config/my_leviton_bus_dump.log` |
| `my_leviton.listen_dimmer` | `/config/my_leviton_capture.log` |

Optional A/B on `probe_level`: `fade`, `arg`, `level_in_arg: true`. Paste the log back before any encoding change.

Each dimmer still has a **dim start** number entity (0–100%) for hardware low-end trim.

## Still to do
- Implement automatic discovery of devices on the bus network
- Optional auto-probe for dim start
- Fix absolute level encoding once probe tables confirm the wire layout

## Features
- Local USB communication (requires `pyusb`)
- Implements `light` platform (on/off relays + brightness dimmers)
- CRC-8/MAXIM checksum when building USB packets
- No cloud dependency
- USB capture / dump / probe services for protocol reverse-engineering

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
