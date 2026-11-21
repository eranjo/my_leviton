# utils/constants.py

from __future__ import annotations

from pathlib import Path
from typing import Any, List

# USB Vendor ID and Product ID (Microchip-based Leviton VRUSB)
VID = 0x04D8
PID = 0xFE93

# Ask the stick for status of all devices (from original polling code).
POLL_COMMAND = [
    0x02,
    0x0F,
    0x01,
    0x7E,
    0x25,
    0x0D,
    0xBC,
    0x00,
    0xFF,
    0xFF,
    0xFF,
    0xFF,
    0x02,
    0x01,
    0x00,
    0xFE,
    0x7F,
    0x45,
]

# Lights are addressed by (type, channel, device_id). Packets are built at
# runtime with CRC-8/MAXIM — see utils/protocol.py.
# type 0x26 = relay/switch, 0x1E/0x2E = dimmer (level 0-100).
#
# PUBLIC EXAMPLE inventory only. Replace via:
#   1) utils/constants_local.py  (gitignored) defining LIGHTS, or
#   2) /config/my_leviton_lights.yaml  (survives HACS updates)
# Do not commit real home device names or IDs to the public repository.

LIGHTS: List[dict[str, Any]] = [
    {
        "name": "Living Room Wall",
        "type": 0x26,
        "channel": 0x03,
        "device_id": 0xA101,
        "dimmer": False,
    },
    {
        "name": "Living Room Ceiling",
        "type": 0x26,
        "channel": 0x43,
        "device_id": 0xA101,
        "dimmer": False,
    },
    {
        "name": "Office Ceiling",
        "type": 0x1E,
        "channel": 0x03,
        "device_id": 0xA201,
        "dimmer": True,
    },
    {
        "name": "Hallway Fan",
        "type": 0x2E,
        "channel": 0x03,
        "device_id": 0xA301,
        "dimmer": True,
    },
    {
        "name": "Kitchen Switch",
        "type": 0x26,
        "channel": 0x03,
        "device_id": 0xA401,
        "dimmer": False,
    },
    {
        "name": "Entry Switch",
        "type": 0x26,
        "channel": 0x43,
        "device_id": 0xA401,
        "dimmer": False,
    },
]


def _load_lights_yaml(path: Path) -> List[dict[str, Any]] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        import yaml  # Home Assistant always provides PyYAML
    except ImportError:
        return None
    try:
        data = yaml.safe_load(text)
    except Exception:
        return None
    if isinstance(data, list) and data:
        return data
    if isinstance(data, dict):
        lights = data.get("lights")
        if isinstance(lights, list) and lights:
            return lights
    return None


def _apply_local_lights() -> None:
    """Prefer private inventory over the public example table."""
    global LIGHTS
    try:
        from . import constants_local as _local

        local = getattr(_local, "LIGHTS", None)
        if isinstance(local, list) and local:
            LIGHTS = local
            return
    except ImportError:
        pass

    # YAML overrides (prefer /config so HACS package replaces do not wipe it).
    # parents: utils → my_leviton → custom_components → config
    package_dir = Path(__file__).resolve().parents[1]
    config_dir = Path(__file__).resolve().parents[3]
    for cfg in (
        config_dir / "my_leviton_lights.yaml",
        package_dir / "my_leviton_lights.yaml",
    ):
        loaded = _load_lights_yaml(cfg)
        if loaded:
            LIGHTS = loaded
            return


_apply_local_lights()
