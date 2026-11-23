"""USB bus discovery + inventory correlation (by device address).

Matching strategy:

1. Index local inventory (constants_local / yaml) by (type, channel, device_id).
2. Index HA entity registry rows for current names and entity_ids.
   Legacy rows use ``my_leviton::{name}``. Current rows use the bus address
   ``my_leviton:{type:04x}:{channel:02x}:{device_id:04x}``.
3. Scan the bus for live addresses + last level (+ optional bus-defined name).
4. Correlate bus → inventory by address first; fall back to registry name only
   when the inventory entry's name matches a registry unique_id suffix.
5. Preserve existing HA-facing / inventory names for matched addresses. New
   unmatched addresses prefer a bus-defined name from status frames
   (``… 7c ASCII… 7f``); otherwise a stable placeholder (never renames an
   existing entity).

Also classifies device kind (relay / dimmer / fan_dimmer) and suggests a
dimmer profile from the type byte when the inventory has no ``levels`` yet.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from .protocol import (
    DIMMER_PROFILES,
    DIMMER_TYPES,
    TYPE_DIMMER,
    TYPE_DIMMER_ALT,
    TYPE_RELAY,
    is_dimmer_type,
)

_LOGGER = logging.getLogger(__name__)

# my_leviton:{type:04x}:{channel:02x}:{device_id:04x}
_ADDRESS_UID_RE = re.compile(r"^my_leviton:[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{4}$")

# Wire types we treat as Leviton load-control devices.
_KNOWN_TYPES = frozenset({TYPE_RELAY, TYPE_DIMMER, TYPE_DIMMER_ALT})

AddrKey = tuple[int, int, int]  # type, channel, device_id


# One-shot / invalid-level addresses are treated as bus noise for *new* rows.
_MIN_NEW_FRAME_COUNT = 2


@dataclass(frozen=True)
class BusDevice:
    """One address observed on the USB status bus."""

    type: int
    channel: int
    device_id: int
    last_level: Optional[int] = None
    frame_count: int = 0
    last_seen_mono: float = 0.0
    bus_name: Optional[str] = None  # wire ASCII after 0x7C when present

    @property
    def addr(self) -> AddrKey:
        return (self.type, self.channel, self.device_id)

    @property
    def kind(self) -> str:
        return classify_kind(self.type)

    @property
    def dimmer(self) -> bool:
        return is_dimmer_type(self.type)

    @property
    def suggested_profile(self) -> Optional[str]:
        return suggest_profile(self.type)


@dataclass
class RegistryLight:
    """Minimal HA entity-registry view for a my_leviton light."""

    entity_id: str
    unique_id: str
    name: str  # inventory / unique_id suffix (match key)
    friendly_name: str = ""  # current HA display name (override or original)
    disabled: bool = False


@dataclass
class MatchedDevice:
    """Bus device correlated with inventory / registry (or marked new)."""

    type: int
    channel: int
    device_id: int
    name: str
    kind: str
    dimmer: bool
    entity_id: Optional[str] = None
    unique_id: Optional[str] = None
    match_source: str = "new"  # inventory_addr | registry_name | new
    is_new: bool = True
    last_level: Optional[int] = None
    frame_count: int = 0
    suggested_profile: Optional[str] = None
    inventory_levels: Optional[list[int]] = None
    notes: list[str] = field(default_factory=list)
    bus_name: Optional[str] = None  # wire name from status 7c…7f when seen
    friendly_name: Optional[str] = None  # current HA friendly name when known

    @property
    def addr(self) -> AddrKey:
        return (self.type, self.channel, self.device_id)

    def form_key(self) -> str:
        """Stable schema key for config-flow fields (type_ch_id hex)."""
        return f"{self.type:02x}_{self.channel:02x}_{self.device_id:04x}"

    def display_name(self) -> str:
        """Best UI label: HA friendly → inventory → wire → placeholder."""
        for candidate in (self.friendly_name, self.name, self.bus_name):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return placeholder_name(
            BusDevice(self.type, self.channel, self.device_id)
        )

    def to_light_dict(self, *, name: Optional[str] = None) -> dict[str, Any]:
        """LIGHTS-entry shape; keeps existing allowlists when known."""
        entry: dict[str, Any] = {
            "name": name if name is not None else self.name,
            "type": self.type,
            "channel": self.channel,
            "device_id": self.device_id,
            "dimmer": self.dimmer,
        }
        if self.inventory_levels is not None:
            entry["levels"] = list(self.inventory_levels)
        elif self.suggested_profile and self.dimmer:
            entry["profile"] = self.suggested_profile
        return entry

    def as_public_dict(self) -> dict[str, Any]:
        """JSON-safe summary (may contain real names — keep volume-local)."""
        d = asdict(self)
        return d


def classify_kind(device_type: int) -> str:
    """Human-readable device class from the bus type byte."""
    if device_type == TYPE_RELAY:
        return "relay"
    if device_type == TYPE_DIMMER:
        return "dimmer"
    if device_type == TYPE_DIMMER_ALT:
        return "fan_dimmer"
    return f"unknown_0x{device_type:02x}"


def suggest_profile(device_type: int) -> Optional[str]:
    """Default named allowlist for a type byte (anonymized public profiles)."""
    if device_type == TYPE_DIMMER:
        return "office_steps" if "office_steps" in DIMMER_PROFILES else None
    if device_type == TYPE_DIMMER_ALT:
        return "fan_steps" if "fan_steps" in DIMMER_PROFILES else None
    return None


def addr_key(light: Mapping[str, Any]) -> Optional[AddrKey]:
    """Extract (type, channel, device_id) from a LIGHTS-like mapping."""
    try:
        return (int(light["type"]), int(light["channel"]), int(light["device_id"]))
    except (KeyError, TypeError, ValueError):
        return None


def index_inventory_by_addr(
    lights: Sequence[Mapping[str, Any]],
) -> dict[AddrKey, dict[str, Any]]:
    out: dict[AddrKey, dict[str, Any]] = {}
    for light in lights:
        key = addr_key(light)
        if key is None:
            continue
        # First wins — do not overwrite (stable against accidental dupes).
        out.setdefault(key, dict(light))
    return out


def index_inventory_by_name(
    lights: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for light in lights:
        name = light.get("name")
        if isinstance(name, str) and name and name not in out:
            out[name] = dict(light)
    return out


def parse_registry_lights(
    entries: Iterable[Mapping[str, Any]],
) -> list[RegistryLight]:
    """Parse entity-registry rows for ``platform==my_leviton`` lights."""
    found: list[RegistryLight] = []
    for entry in entries:
        if entry.get("domain") not in (None, "light"):
            # Some dumps omit domain; prefer entity_id prefix then.
            pass
        entity_id = str(entry.get("entity_id") or "")
        platform = entry.get("platform")
        unique_id = str(entry.get("unique_id") or "")
        if platform and platform != "my_leviton":
            continue
        legacy = unique_id.startswith("my_leviton::")
        addressed = _ADDRESS_UID_RE.match(unique_id) is not None
        if not legacy and not addressed:
            continue
        if entity_id and not entity_id.startswith("light."):
            continue
        # Legacy unique_ids embed the inventory name. Address unique_ids do not.
        # Prefer original_name so a registry display-name override stays display-only.
        if legacy:
            inventory_name = unique_id.split("::", 1)[1]
        else:
            inventory_name = ""
        original = entry.get("original_name")
        if isinstance(original, str) and original.strip():
            inventory_name = original.strip()
        if not inventory_name and not addressed:
            continue
        override = entry.get("name")
        if isinstance(override, str) and override.strip():
            friendly = override.strip()
        elif isinstance(original, str) and original.strip():
            friendly = original.strip()
        else:
            friendly = inventory_name
        found.append(
            RegistryLight(
                entity_id=entity_id or "light.unknown",
                unique_id=unique_id,
                name=inventory_name,
                friendly_name=friendly,
                disabled=entry.get("disabled_by") is not None,
            )
        )
    return found


def index_registry_by_name(
    registry: Sequence[RegistryLight],
) -> dict[str, RegistryLight]:
    return {r.name: r for r in registry if r.name}


def placeholder_name(device: BusDevice) -> str:
    """Stable name for a newly discovered address (no rename of existing)."""
    kind = classify_kind(device.type).replace("_", " ").title()
    return f"{kind} {device.device_id:04X} ch{device.channel:02X}"


def _is_credible_new(device: BusDevice) -> bool:
    """Drop one-shot / nameless-noise addresses that lack a valid level."""
    return (
        device.last_level is not None
        and 0 <= int(device.last_level) <= 100
        and device.frame_count >= _MIN_NEW_FRAME_COUNT
    )


def correlate_devices(
    bus_devices: Sequence[BusDevice],
    inventory: Sequence[Mapping[str, Any]],
    registry: Sequence[RegistryLight] | None = None,
) -> list[MatchedDevice]:
    """Match bus addresses to inventory / HA registry without renaming.

    Priority per bus device:
    1. Inventory entry with the same (type, channel, device_id) → keep its name.
    2. Else if inventory has no addr match but registry has a name that still
       appears in inventory by name with a *different* addr — do not steal;
       treat as new (address is authoritative).
    3. New address → bus_name when present, else placeholder (user can rename
       later in inventory). Requires a valid level and ≥2 frames.
    """
    from .constants import address_unique_id
    from .sniff import _is_clean_bus_name

    by_addr = index_inventory_by_addr(inventory)
    reg_by_name = index_registry_by_name(registry or [])
    reg_by_uid = {item.unique_id: item for item in (registry or []) if item.unique_id}
    matched: list[MatchedDevice] = []

    for bus in bus_devices:
        inv = by_addr.get(bus.addr)
        notes: list[str] = []
        if inv is not None:
            name = str(inv.get("name") or placeholder_name(bus))
            uid = address_unique_id(inv)
            reg = (reg_by_uid.get(uid) if uid else None) or reg_by_name.get(name)
            levels = inv.get("levels")
            level_list = None
            if isinstance(levels, (list, tuple)):
                try:
                    level_list = [int(x) for x in levels]
                except (TypeError, ValueError):
                    level_list = None
            # Prefer inventory dimmer flag; fall back to type byte.
            dimmer = bool(inv.get("dimmer", bus.dimmer))
            if bool(inv.get("dimmer")) != bus.dimmer:
                notes.append(
                    f"inventory dimmer={inv.get('dimmer')} bus_kind={bus.kind}"
                )
            profile = inv.get("profile")
            suggested = (
                profile
                if isinstance(profile, str)
                else (None if level_list else bus.suggested_profile)
            )
            matched.append(
                MatchedDevice(
                    type=bus.type,
                    channel=bus.channel,
                    device_id=bus.device_id,
                    name=name,
                    kind=bus.kind,
                    dimmer=dimmer,
                    entity_id=reg.entity_id if reg else None,
                    unique_id=uid or (reg.unique_id if reg else None),
                    match_source="inventory_addr",
                    is_new=False,
                    last_level=bus.last_level,
                    frame_count=bus.frame_count,
                    suggested_profile=suggested if isinstance(suggested, str) else None,
                    inventory_levels=level_list,
                    notes=notes,
                    bus_name=bus.bus_name,
                    friendly_name=(reg.friendly_name if reg else name),
                )
            )
            continue

        # Unmatched address — require a credible sighting to kill ghost addrs.
        if not _is_credible_new(bus):
            continue

        # bus_name is already sanitized at extract time; re-check for safety.
        if _is_clean_bus_name(bus.bus_name):
            name = bus.bus_name
            notes.append("no inventory address match; using bus-defined name")
        else:
            name = placeholder_name(bus)
            notes.append("no inventory address match; placeholder name assigned")
        matched.append(
            MatchedDevice(
                type=bus.type,
                channel=bus.channel,
                device_id=bus.device_id,
                name=name,
                kind=bus.kind,
                dimmer=bus.dimmer,
                entity_id=None,
                unique_id=address_unique_id(
                    {
                        "type": bus.type,
                        "channel": bus.channel,
                        "device_id": bus.device_id,
                    }
                ),
                match_source="new",
                is_new=True,
                last_level=bus.last_level,
                frame_count=bus.frame_count,
                suggested_profile=bus.suggested_profile,
                inventory_levels=None,
                notes=notes,
                bus_name=bus.bus_name,
                friendly_name=None,
            )
        )

    # Inventory-only rows not seen on this scan (still keep — never drop).
    seen_addrs = {m.addr for m in matched}
    for key, inv in by_addr.items():
        if key in seen_addrs:
            continue
        name = str(inv.get("name") or f"Device {key[2]:04X}")
        uid = address_unique_id(inv)
        reg = (reg_by_uid.get(uid) if uid else None) or reg_by_name.get(name)
        levels = inv.get("levels")
        level_list = None
        if isinstance(levels, (list, tuple)):
            try:
                level_list = [int(x) for x in levels]
            except (TypeError, ValueError):
                level_list = None
        matched.append(
            MatchedDevice(
                type=key[0],
                channel=key[1],
                device_id=key[2],
                name=name,
                kind=classify_kind(key[0]),
                dimmer=bool(inv.get("dimmer", is_dimmer_type(key[0]))),
                entity_id=reg.entity_id if reg else None,
                unique_id=uid or (reg.unique_id if reg else None),
                match_source="inventory_offline",
                is_new=False,
                last_level=None,
                frame_count=0,
                suggested_profile=inv.get("profile")
                if isinstance(inv.get("profile"), str)
                else suggest_profile(key[0]),
                inventory_levels=level_list,
                notes=["not seen on this bus scan; kept from inventory"],
                friendly_name=(reg.friendly_name if reg else name),
            )
        )

    # New addresses first so UI / summaries that iterate in order surface them.
    matched.sort(key=lambda m: (not m.is_new, m.name.lower(), m.device_id))
    return matched


def merge_inventory_additive(
    existing: Sequence[Mapping[str, Any]],
    matched: Sequence[MatchedDevice],
    *,
    include_new: bool = True,
    selected_keys: Optional[Iterable[str]] = None,
    name_overrides: Optional[Mapping[str, str]] = None,
) -> list[dict[str, Any]]:
    """Build an updated LIGHTS list without renaming or removing existing.

    * Existing entries matched by address keep their full dict (name, levels…).
    * New matched devices are appended when ``include_new`` (optionally
      filtered by ``selected_keys`` form keys and renamed via overrides).
    * Inventory entries not on the bus are preserved as-is.
    * Matched renames are *not* applied here — use entity-registry overrides
      so ``unique_id`` / entity_id stay stable.
    """
    by_addr = index_inventory_by_addr(existing)
    # Preserve original order for existing rows.
    out: list[dict[str, Any]] = [dict(x) for x in existing]
    known = set(by_addr.keys())
    selected = (
        None if selected_keys is None else {str(k) for k in selected_keys}
    )
    overrides = {
        str(k): str(v).strip()
        for k, v in (name_overrides or {}).items()
        if isinstance(v, str) and str(v).strip()
    }

    for m in matched:
        if m.addr in known:
            continue
        if not include_new or not m.is_new:
            continue
        key = m.form_key()
        if selected is not None and key not in selected:
            continue
        chosen = overrides.get(key) or m.name
        out.append(m.to_light_dict(name=chosen))
        known.add(m.addr)
    return out


def resolve_new_name_overrides(
    matched: Sequence[MatchedDevice],
    user_input: Mapping[str, Any],
    *,
    prefix: str = "name_",
) -> dict[str, str]:
    """Extract non-empty friendly-name fields for new devices from form input.

    Accepts both the human-readable ``Name — …`` schema keys and legacy
    ``name_<type_ch_id>`` keys.
    """
    out: dict[str, str] = {}
    for m in matched:
        if not m.is_new:
            continue
        key = m.form_key()
        for candidate in (
            new_device_name_field_key(m),
            f"{prefix}{key}",
        ):
            raw = user_input.get(candidate)
            if isinstance(raw, str) and raw.strip():
                out[key] = raw.strip()
                break
    return out


def matched_rename_field_key(m: MatchedDevice) -> str:
    """Config-flow schema key for a matched device's friendly-name text field.

    Human-readable so HA shows a sensible label without a per-device
    translation entry. ``resolve_matched_renames`` also accepts legacy
    ``rename_<addr>`` keys.
    """
    label = (m.friendly_name or m.name or m.form_key()).strip()
    return f"Name — {label}"


def resolve_matched_renames(
    matched: Sequence[MatchedDevice],
    selected_keys: Iterable[str],
    user_input: Mapping[str, Any],
    *,
    prefix: str = "rename_",
) -> list[tuple[MatchedDevice, str]]:
    """Return (device, new_friendly_name) for matched devices the user renamed.

    Skips empty / unchanged values. Does not touch inventory unique_id names.
    Accepts human-readable ``Name — …`` schema keys and legacy ``rename_<addr>``.
    """
    by_key = {m.form_key(): m for m in matched if not m.is_new}
    changes: list[tuple[MatchedDevice, str]] = []
    for key in selected_keys:
        m = by_key.get(str(key))
        if m is None or not m.entity_id:
            continue
        raw = None
        for candidate in (matched_rename_field_key(m), f"{prefix}{key}"):
            if candidate in user_input:
                raw = user_input[candidate]
                break
        if not isinstance(raw, str):
            continue
        new_name = raw.strip()
        if not new_name:
            continue
        current = (m.friendly_name or m.name or "").strip()
        if new_name == current:
            continue
        changes.append((m, new_name))
    return changes


def new_device_name_field_key(m: MatchedDevice) -> str:
    """Config-flow schema key for a new device's friendly-name text field.

    Uses a human-readable key so HA's form label is sensible even when there
    is no per-address entry in ``translations/en.json`` (dynamic keys cannot
    be pre-declared). ``resolve_new_name_overrides`` accepts this key and the
    legacy ``name_<addr>`` form.
    """
    return f"Name — {m.display_name()}"


def new_device_option_label(m: MatchedDevice) -> str:
    """Multi-select label for a new bus address."""
    wire = (m.bus_name or "").strip() or m.name
    return (
        f"{wire} — {m.kind} "
        f"ch=0x{m.channel:02x} id=0x{m.device_id:04x}"
    )


def matched_device_option_label(m: MatchedDevice) -> str:
    """Multi-select label for a matched inventory device."""
    ha = (m.friendly_name or m.name or "").strip()
    wire = (m.bus_name or "").strip()
    base = f"{ha} · wire: {wire}" if wire and wire != ha else ha
    return f"{base} (ch=0x{m.channel:02x} id=0x{m.device_id:04x})"


def inventory_yaml_text(lights: Sequence[Mapping[str, Any]]) -> str:
    """Serialize LIGHTS to the private yaml shape (volume-local only)."""
    try:
        import yaml
    except ImportError:
        # Minimal fallback — enough for round-trip of simple types.
        lines = ["# Private my_leviton inventory — DO NOT commit.", "lights:"]
        for light in lights:
            lines.append(f'  - name: "{light.get("name")}"')
            for key in ("type", "channel", "device_id", "dimmer", "profile", "min_level"):
                if key in light:
                    val = light[key]
                    if isinstance(val, bool):
                        lines.append(f"    {key}: {'true' if val else 'false'}")
                    elif isinstance(val, str):
                        lines.append(f'    {key}: "{val}"')
                    else:
                        lines.append(f"    {key}: {val}")
            if "levels" in light and light["levels"] is not None:
                levels = ", ".join(str(int(x)) for x in light["levels"])
                lines.append(f"    levels: [{levels}]")
        return "\n".join(lines) + "\n"

    payload = {
        "lights": [dict(light) for light in lights],
    }
    header = (
        "# Private my_leviton inventory — DO NOT commit. "
        "Survives if kept beside the package / under /config.\n"
    )
    return header + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def write_inventory_yaml(path: Path, lights: Sequence[Mapping[str, Any]]) -> None:
    """Write inventory yaml (caller must pass merged additive list)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(inventory_yaml_text(lights), encoding="utf-8")


def discover_bus_devices(
    duration_sec: float = 8.0,
    *,
    poll_interval_sec: float = 1.0,
) -> list[BusDevice]:
    """POLL the VRUSB stick and collect unique device addresses + last level.

    Only status (0x04) / live-dim (0x22) frames for known load types
    (relay 0x26, dimmer 0x1E / 0x2E) are counted — command echoes and
    unrelated type bytes are ignored.

    When a status frame carries a bus-defined name (``7c``…ASCII…``7f``),
    the longest *clean* printable ASCII run seen for that address is kept as
    ``bus_name`` (high-byte junk / punctuation-only labels are ignored).
    Correlate afterwards with ``correlate_devices`` (inventory / HA names win
    for matched addresses; ``bus_name`` is used for new ones).

    Requires USB access (run in an executor from HA).
    """
    # Local imports keep unit tests importable without a live USB stick.
    from .sniff import (
        HidFrameBuffer,
        _KIND_LIVE,
        _KIND_STATUS,
        _bus_name_from_frame,
        _level_from_frame,
        _open_usb,
        _poll,
        _prefer_longer_name,
        _read_raw,
    )

    seen: dict[AddrKey, BusDevice] = {}
    deadline = time.monotonic() + max(0.5, float(duration_sec))
    next_poll = 0.0
    last_raw: Optional[tuple[int, ...]] = None
    hid_buf = HidFrameBuffer()

    def _ingest(frame: Sequence[int], now: float) -> None:
        if len(frame) < 6:
            return
        dtype, channel = int(frame[1]), int(frame[2])
        if dtype not in _KNOWN_TYPES:
            return
        kind = int(frame[5])
        # Accept plain status/live, or glued frames that still decode a level.
        level = _level_from_frame(frame)
        if kind not in (_KIND_STATUS, _KIND_LIVE) and level is None:
            return
        device_id = (int(frame[3]) << 8) | int(frame[4])
        key: AddrKey = (dtype, channel, device_id)
        bus_name = _bus_name_from_frame(frame)
        prev = seen.get(key)
        if prev is None:
            seen[key] = BusDevice(
                type=dtype,
                channel=channel,
                device_id=device_id,
                last_level=level,
                frame_count=1,
                last_seen_mono=now,
                bus_name=bus_name,
            )
        else:
            seen[key] = BusDevice(
                type=dtype,
                channel=channel,
                device_id=device_id,
                last_level=level if level is not None else prev.last_level,
                frame_count=prev.frame_count + 1,
                last_seen_mono=now,
                bus_name=_prefer_longer_name(prev.bus_name, bus_name),
            )

    with _open_usb() as dev:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_poll:
                _poll(dev)
                next_poll = now + max(0.4, float(poll_interval_sec))
            raw = _read_raw(dev)
            if raw is None:
                continue
            raw_t = tuple(raw)
            if raw_t == last_raw:
                continue
            last_raw = raw_t
            for frame in hid_buf.feed(raw):
                _ingest(frame, now)
        # Trailing open prefix still carries addr/level (name may be absent).
        for frame in hid_buf.flush():
            _ingest(frame, time.monotonic())

    devices = sorted(seen.values(), key=lambda d: (d.type, d.channel, d.device_id))
    _LOGGER.info(
        "Bus discovery found %s device address(es) in %.1fs",
        len(devices),
        float(duration_sec),
    )
    return devices


def discovery_summary(matched: Sequence[MatchedDevice]) -> dict[str, Any]:
    """Counts for config-flow UI (no need to list every real name publicly)."""
    return {
        "total": len(matched),
        "matched": sum(1 for m in matched if not m.is_new),
        "new": sum(1 for m in matched if m.is_new),
        "online": sum(1 for m in matched if m.frame_count > 0),
        "offline_inventory": sum(
            1 for m in matched if m.match_source == "inventory_offline"
        ),
        "dimmers": sum(1 for m in matched if m.dimmer),
        "relays": sum(1 for m in matched if m.kind == "relay"),
    }
