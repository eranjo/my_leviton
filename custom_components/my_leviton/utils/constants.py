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
        # Optional: levels | min_level | profile (see protocol.DIMMER_PROFILES).
        "profile": "office_steps",
    },
    {
        "name": "Hallway Fan",
        "type": 0x2E,
        "channel": 0x03,
        "device_id": 0xA301,
        "dimmer": True,
        "profile": "fan_steps",
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


def inventory_paths() -> tuple[Path, Path]:
    """Return (config_dir yaml, package yaml) candidate paths."""
    package_dir = Path(__file__).resolve().parents[1]
    config_dir = Path(__file__).resolve().parents[3]
    return (
        config_dir / "my_leviton_lights.yaml",
        package_dir / "my_leviton_lights.yaml",
    )


def preferred_inventory_yaml() -> Path:
    """Path used when discovery writes additive inventory updates.

    Prefer an already-present yaml (config first). If neither exists but
    ``constants_local`` is in use, still target config-dir yaml so HACS
    package replaces do not wipe discoveries (local py stays authoritative
    until the operator migrates).
    """
    config_yaml, package_yaml = inventory_paths()
    if config_yaml.is_file():
        return config_yaml
    if package_yaml.is_file():
        return package_yaml
    return config_yaml


def constants_local_path() -> Path:
    """Path to the gitignored private ``constants_local.py`` module."""
    return Path(__file__).resolve().parent / "constants_local.py"


def constants_local_active() -> bool:
    """True when ``constants_local.LIGHTS`` is the live inventory source."""
    try:
        from . import constants_local as _local

        local = getattr(_local, "LIGHTS", None)
        return isinstance(local, list) and bool(local)
    except ImportError:
        return False


def _format_light_py(light: dict[str, Any]) -> str:
    """Serialize one LIGHTS row as a Python dict literal."""
    parts: list[str] = []
    name = light.get("name", "")
    parts.append(f'"name": {name!r}')
    for key in ("type", "channel", "device_id", "dimmer", "min_level"):
        if key not in light:
            continue
        val = light[key]
        if isinstance(val, bool):
            parts.append(f'"{key}": {val!s}')
        else:
            parts.append(f'"{key}": {int(val) if isinstance(val, int) else val!r}')
    if "profile" in light and isinstance(light["profile"], str):
        parts.append(f'"profile": {light["profile"]!r}')
    if "levels" in light and light["levels"] is not None:
        levels = ", ".join(str(int(x)) for x in light["levels"])
        parts.append(f'"levels": [{levels}]')
    return "{" + ", ".join(parts) + "}"


def write_constants_local(path: Path, lights: List[dict[str, Any]]) -> None:
    """Rewrite private constants_local.py LIGHTS (volume-local; gitignored)."""
    rows = ",\n    ".join(_format_light_py(dict(x)) for x in lights)
    text = (
        "# Private home inventory — DO NOT commit or publish.\n"
        "# Loaded automatically by utils/constants.py when present.\n"
        "from __future__ import annotations\n"
        "\n"
        f"LIGHTS = [\n    {rows},\n]\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def persist_inventory(lights: List[dict[str, Any]]) -> list[Path]:
    """Write inventory to the live source(s) and return paths written.

    When ``constants_local`` is active it is rewritten (otherwise reload would
    ignore yaml). Always also write preferred yaml as a backup / HACS-safe copy.
    """
    from .discovery import write_inventory_yaml

    written: list[Path] = []
    yaml_path = preferred_inventory_yaml()
    write_inventory_yaml(yaml_path, lights)
    written.append(yaml_path)
    if constants_local_active() or constants_local_path().is_file():
        local_path = constants_local_path()
        write_constants_local(local_path, lights)
        written.append(local_path)
    return written


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
    for cfg in inventory_paths():
        loaded = _load_lights_yaml(cfg)
        if loaded:
            LIGHTS = loaded
            return


def reload_lights() -> List[dict[str, Any]]:
    """Re-read constants_local / yaml into ``LIGHTS`` (after discovery writes)."""
    import importlib

    try:
        from . import constants_local as _local

        importlib.reload(_local)
    except ImportError:
        pass
    _apply_local_lights()
    return LIGHTS


_apply_local_lights()
