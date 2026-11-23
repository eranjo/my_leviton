# utils/constants.py

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, List, Mapping

_LOGGER = logging.getLogger(__name__)

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

# Snapshot of the public placeholders. LIGHTS is mutated in place so modules
# that did ``from .constants import LIGHTS`` keep seeing the selected inventory.
_EXAMPLE_LIGHTS: List[dict[str, Any]] = [dict(light) for light in LIGHTS]
EXAMPLE_NAMES = frozenset(
    light["name"] for light in _EXAMPLE_LIGHTS if isinstance(light.get("name"), str)
)
# yaml | constants_local | private_unusable | examples | withheld_examples | restored
INVENTORY_SOURCE = "examples"


def _coerce_int(value: Any) -> int:
    """Parse an inventory int (decimal or 0x-prefixed text from YAML)."""
    if isinstance(value, bool) or value is None:
        raise TypeError(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value.strip(), 0)
    raise TypeError(value)


def address_unique_id(light: Mapping[str, Any]) -> str | None:
    """Entity unique_id from the bus address. The display name is not included.

    Format: ``my_leviton:{type:04x}:{channel:02x}:{device_id:04x}``.
    """
    if not isinstance(light, Mapping):
        return None
    try:
        type_id = _coerce_int(light.get("type"))
        channel = _coerce_int(light.get("channel"))
        device_id = _coerce_int(light.get("device_id"))
    except (TypeError, ValueError):
        return None
    return f"my_leviton:{type_id:04x}:{channel:02x}:{device_id:04x}"


def legacy_name_unique_id(name: str) -> str:
    """Old unique_id (``my_leviton::{name}``), used only to find rows to migrate."""
    return f"my_leviton::{name}"


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


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        found.append(path)
    return found


def config_yaml_candidates(config_dir: Path | None = None) -> list[Path]:
    """``/config/my_leviton_lights.yaml`` candidates, outside the integration folder.

    HACS replaces everything under ``custom_components/my_leviton``. The
    durable inventory is the HA config dir (parent of ``custom_components``).
    Both the unresolved and resolved ``__file__`` are checked so a symlink
    into a git checkout still sees ``/config``.
    """
    dirs: list[Path] = []
    if config_dir is not None:
        dirs.append(Path(config_dir))

    bases = [Path(__file__)]
    resolved = bases[0].resolve()
    if resolved != bases[0]:
        bases.append(resolved)
    for base in bases:
        parents = base.parents
        # utils → my_leviton → custom_components → config
        if len(parents) > 3:
            dirs.append(parents[3])
        for parent in parents:
            if (parent / "configuration.yaml").is_file() or (parent / ".storage").is_dir():
                dirs.append(parent)
                break
    if not dirs:
        dirs.append(Path(__file__).resolve().parents[3])
    return _dedupe_paths(directory / "my_leviton_lights.yaml" for directory in dirs)


def inventory_paths(config_dir: Path | None = None) -> tuple[Path, Path]:
    """Return (config_dir yaml, package yaml) candidate paths."""
    package_dir = Path(__file__).resolve().parents[1]
    config_yaml = config_yaml_candidates(config_dir)[0]
    return (config_yaml, package_dir / "my_leviton_lights.yaml")


def preferred_inventory_yaml(config_dir: Path | None = None) -> Path:
    """Config-dir yaml used for durable inventory writes.

    Never targets a file inside the integration package. An existing config
    yaml wins so discovery does not create a second copy.
    """
    candidates = config_yaml_candidates(config_dir)
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


def constants_local_path() -> Path:
    """Path to the gitignored private ``constants_local.py`` module."""
    unresolved = Path(__file__).parent / "constants_local.py"
    if unresolved.is_file():
        return unresolved
    return Path(__file__).resolve().parent / "constants_local.py"


def is_example_only(lights: List[dict[str, Any]] | None) -> bool:
    """True when every name is one of the public placeholder lights."""
    if not lights:
        return False
    names = [
        light.get("name")
        for light in lights
        if isinstance(light, dict) and isinstance(light.get("name"), str)
    ]
    return bool(names) and all(name in EXAMPLE_NAMES for name in names)


def _example_address_ids() -> frozenset[str]:
    return frozenset(
        uid for light in _EXAMPLE_LIGHTS if (uid := address_unique_id(light))
    )


def is_usable_private(lights: List[dict[str, Any]] | None) -> bool:
    """True when the list is not the public example table.

    A non-example display name counts, and so does a bus address that is not
    one of the placeholder addresses. Display names may match the examples.
    """
    if not isinstance(lights, list):
        return False
    example_addrs = _example_address_ids()
    for light in lights:
        if not isinstance(light, dict):
            continue
        name = light.get("name")
        if isinstance(name, str) and name not in EXAMPLE_NAMES:
            return True
        uid = address_unique_id(light)
        if uid and uid not in example_addrs:
            return True
    return False


def row_is_private(light: Mapping[str, Any]) -> bool:
    """True for one inventory row that must not be replaced by an example."""
    if not isinstance(light, Mapping):
        return False
    name = light.get("name")
    if not isinstance(name, str) or not name:
        return False
    if name not in EXAMPLE_NAMES:
        return True
    uid = address_unique_id(light)
    return bool(uid) and uid not in _example_address_ids()


def using_private_inventory() -> bool:
    """True when the active LIGHTS list came from a private source."""
    return INVENTORY_SOURCE in {"yaml", "constants_local", "restored"} and is_usable_private(
        LIGHTS
    )


def withhold_example_inventory() -> None:
    """Drop placeholder rows so they are not commanded or registered.

    Used when Home Assistant already has my_leviton lights but the private
    address list is missing. Applying the example table would attach those
    lights to placeholder bus addresses, or cause HA to disable them.
    """
    global INVENTORY_SOURCE
    if using_private_inventory():
        return
    _set_lights([])
    INVENTORY_SOURCE = "withheld_examples"


def constants_local_active() -> bool:
    """True when ``constants_local.LIGHTS`` is a usable private inventory."""
    exists, local = _read_constants_local()
    return exists and is_usable_private(local)


def _set_lights(lights: List[dict[str, Any]]) -> None:
    """Replace LIGHTS contents without rebinding the list other modules hold."""
    copied = [dict(light) for light in lights if isinstance(light, dict)]
    LIGHTS.clear()
    LIGHTS.extend(copied)


def _read_constants_local() -> tuple[bool, List[dict[str, Any]] | None]:
    """Return (file_exists, LIGHTS or None if the file failed to import)."""
    path = constants_local_path()
    if not path.is_file():
        return False, None
    import importlib
    import sys

    module_name = __package__ + ".constants_local" if __package__ else None
    already = bool(module_name and module_name in sys.modules)
    try:
        from . import constants_local as local_mod

        if already:
            importlib.reload(local_mod)
    except Exception:
        _LOGGER.exception(
            "constants_local.py is present but failed to load; "
            "not using the public example table"
        )
        return True, None
    local = getattr(local_mod, "LIGHTS", None)
    if isinstance(local, list):
        return True, [dict(light) for light in local if isinstance(light, dict)]
    return True, []


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
    if not is_usable_private(lights):
        _LOGGER.error(
            "Refusing to write the public example table over constants_local.py"
        )
        return
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


def config_yaml_is_protected(path: Path) -> bool:
    """True when ``path`` already holds a non-example inventory. Do not overwrite."""
    if not path.is_file():
        return False
    return is_usable_private(_load_lights_yaml(path))


def write_private_yaml(path: Path, lights: List[dict[str, Any]]) -> bool:
    """Write a private inventory yaml. Refuses example names and protected files."""
    if not is_usable_private(lights):
        _LOGGER.error(
            "Refusing to write the public example table into private inventory"
        )
        return False
    if config_yaml_is_protected(path):
        _LOGGER.info("Private inventory yaml already exists; not overwriting")
        return False
    from .discovery import write_inventory_yaml

    write_inventory_yaml(path, lights)
    _LOGGER.warning(
        "Wrote private Leviton inventory yaml (%s lights) outside the integration folder",
        len(lights),
    )
    return True


def persist_inventory(
    lights: List[dict[str, Any]], config_dir: Path | None = None
) -> list[Path]:
    """Write an explicit discovery update to the config-dir yaml.

    Not called on startup or update. Refuses the public example table, and
    does not rewrite ``constants_local.py`` (HACS would delete that copy).
    """
    private = [dict(light) for light in lights if row_is_private(light)]
    if not is_usable_private(private) and not any(
        path.is_file() for path in config_yaml_candidates(config_dir)
    ):
        _LOGGER.error(
            "Refusing to persist the public example table over private inventory"
        )
        return []
    from .discovery import write_inventory_yaml

    yaml_path = preferred_inventory_yaml(config_dir)
    existing = _load_lights_yaml(yaml_path) if yaml_path.is_file() else None
    existing_private = [dict(light) for light in (existing or []) if row_is_private(light)]
    merged = _merge_private_lists(existing_private, private)
    if not is_usable_private(merged):
        _LOGGER.error(
            "Refusing to persist the public example table over private inventory"
        )
        return []
    # Existing private rows stay. Discovery may append names or fill blank fields.
    if existing_private and merged == existing_private:
        return []
    write_inventory_yaml(yaml_path, merged)
    return [yaml_path]


def _merge_private_lists(
    existing: List[dict[str, Any]],
    incoming: List[dict[str, Any]],
) -> List[dict[str, Any]]:
    """Keep existing private rows, fill blank fields, append new names."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def _take(light: Mapping[str, Any], *, fill_only: bool) -> None:
        name = light.get("name")
        if not row_is_private(light):
            return
        if name not in merged:
            merged[name] = dict(light)
            order.append(name)
            return
        if not fill_only:
            return
        current = merged[name]
        for key, value in light.items():
            if key == "name":
                continue
            if key not in current or current[key] in (None, ""):
                current[key] = value

    for light in existing:
        _take(light, fill_only=False)
    for light in incoming:
        _take(light, fill_only=True)
    return [merged[name] for name in order]


def _load_config_yaml(
    config_dir: Path | None,
) -> tuple[bool, List[dict[str, Any]] | None]:
    """Return (any candidate file exists, first usable list or last parsed list)."""
    exists = False
    parsed: List[dict[str, Any]] | None = None
    failed = False
    for path in config_yaml_candidates(config_dir):
        if not path.is_file():
            continue
        exists = True
        loaded = _load_lights_yaml(path)
        if loaded is None:
            failed = True
            continue
        parsed = loaded
        if is_usable_private(loaded):
            return True, loaded
    if exists and parsed is None and failed:
        return True, None
    return exists, parsed


def choose_inventory(
    config_yaml_lights: List[dict[str, Any]] | None,
    config_yaml_exists: bool,
    local_lights: List[dict[str, Any]] | None,
    local_exists: bool,
) -> tuple[str, List[dict[str, Any]]]:
    """Pick inventory without letting the example table replace a private source.

    Order: existing ``/config/my_leviton_lights.yaml``, then ``constants_local.py``,
    then the public examples. A private file that fails to parse does not fall
    through to the examples. An example-only yaml is not treated as private.
    """
    if config_yaml_exists and is_usable_private(config_yaml_lights):
        return "yaml", list(config_yaml_lights or [])
    if local_exists and is_usable_private(local_lights):
        return "constants_local", list(local_lights or [])
    private_file = (config_yaml_exists and config_yaml_lights is None) or (
        local_exists and local_lights is None
    )
    if private_file:
        return "private_unusable", []
    return "examples", [dict(light) for light in _EXAMPLE_LIGHTS]


def _row_is_dimmer(capabilities: Any, supported: Any) -> bool:
    """Dimmer if the registry color mode is brightness, else the legacy feature bit."""
    modes: set[str] = set()
    if isinstance(capabilities, Mapping):
        raw = capabilities.get("supported_color_modes") or []
        if isinstance(raw, (list, tuple, set)):
            modes = {str(mode) for mode in raw}
    if "brightness" in modes:
        return True
    try:
        features = int(supported or 0)
    except (TypeError, ValueError):
        features = 0
    return bool(features & 1)


def lights_from_registry_rows(
    entries: Iterable[Mapping[str, Any] | Any],
) -> List[dict[str, Any]]:
    """Names copied from legacy ``my_leviton::{name}`` registry rows.

    Bus addresses are not stored on the entity, so these rows cannot become
    unique_ids and are not written on startup. Nothing is deleted.
    """
    rows: List[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if isinstance(entry, Mapping):
            domain = entry.get("domain")
            platform = entry.get("platform")
            unique_id = entry.get("unique_id") or ""
            supported = entry.get("supported_features") or 0
            capabilities = entry.get("capabilities")
        else:
            domain = getattr(entry, "domain", None)
            platform = getattr(entry, "platform", None)
            unique_id = getattr(entry, "unique_id", None) or ""
            supported = getattr(entry, "supported_features", 0) or 0
            capabilities = getattr(entry, "capabilities", None)
        if domain != "light" or platform != "my_leviton":
            continue
        unique_id = str(unique_id)
        if not unique_id.startswith("my_leviton::"):
            continue
        name = unique_id.split("::", 1)[1]
        if not name or name in EXAMPLE_NAMES or name in seen:
            continue
        seen.add(name)
        rows.append({"name": name, "dimmer": _row_is_dimmer(capabilities, supported)})
    return rows


def _apply_local_lights(config_dir: Path | None = None) -> str:
    """Select inventory. Private sources win; examples never replace them."""
    global INVENTORY_SOURCE
    yaml_exists, yaml_lights = _load_config_yaml(config_dir)
    local_exists, local_lights = _read_constants_local()
    source, chosen = choose_inventory(
        yaml_lights, yaml_exists, local_lights, local_exists
    )
    _set_lights(chosen)
    INVENTORY_SOURCE = source
    _LOGGER.info(
        "Leviton inventory source=%s count=%s", source, len(LIGHTS)
    )
    return source


def reload_lights(config_dir: Path | None = None) -> List[dict[str, Any]]:
    """Re-read config yaml / constants_local into ``LIGHTS``."""
    _apply_local_lights(config_dir)
    return LIGHTS


_apply_local_lights()
