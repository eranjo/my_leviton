"""Leviton USB HID framing and CRC-8/MAXIM checksum."""

from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Optional, Sequence

# Device type bytes observed on the wire / in status polling.
TYPE_RELAY = 0x26
TYPE_DIMMER = 0x1E
TYPE_DIMMER_ALT = 0x2E
DIMMER_TYPES = frozenset({TYPE_DIMMER, TYPE_DIMMER_ALT})

# Primary SET arg opcodes (VRUSB, confirmed on live bus):
#   0x00 = full ON (100%)
#   0x01 = OFF
#   0x0A = ON last level (matches wall-button tap ON)
# Absolute mid-levels use other arg values — see level_to_dim_arg().
ARG_ON = 0x00
ARG_OFF = 0x01
ARG_ON_LAST = 0x0A

# Status reports use 0x00..0x64 for off..100%.
LEVEL_MIN = 0
LEVEL_MAX = 100

# Named allowlists for LIGHTS[*].profile (anonymized; real homes use local
# constants_local / yaml with explicit ``levels``). USB dimmers often only
# accept a handful of absolute SET points — HA must snap, not emit mid junk.
DIMMER_PROFILES: dict[str, tuple[int, ...]] = {
    # Typical 0x1E ceiling dimmer: floor ~61, then 10% steps to full.
    "office_steps": (0, 61, 70, 80, 90, 100),
    # Typical 0x2E fan/dimmer: mid args stick near 83; usable USB set is coarse.
    "fan_steps": (0, 83, 90, 100),
}

_FRAME_START = 0x7E
_FRAME_END = 0x7F
_TRAILER = 0x8F
_ESCAPE = 0x7C
_REPORT_ID = 0x02
_PREFIX = 0x01
_PAYLOAD_HEADER = (0x25, 0x0D, 0xBC, 0x00)
_CMD_SET = 0x05
# Captured on/off packets use fade=0x03. fade=0x00 (1.1.0b4) made dimmers
# ignore many SET packets.
_FADE = 0x03
FADE_ON_OFF = _FADE
FADE_LEVEL = _FADE


def crc8_maxim(data: Iterable[int]) -> int:
    """CRC-8/MAXIM (DOW-CRC): poly 0x31 reflected, init 0, xorout 0."""
    crc = 0
    for byte in data:
        crc ^= byte & 0xFF
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0x8C
            else:
                crc >>= 1
    return crc


def _stuff(data: Sequence[int]) -> List[int]:
    """Escape 0x7E/0x7C/0x7F inside the framed payload so 0x7E stays unique."""
    out: List[int] = []
    for byte in data:
        if byte in (_FRAME_START, _ESCAPE, _FRAME_END):
            out.append(_ESCAPE)
            out.append(byte ^ 0x80)
        else:
            out.append(byte)
    return out


def build_set_command(
    device_type: int,
    channel: int,
    device_id: int,
    arg: int,
    *,
    fade: int = _FADE,
    level: int = 0,
) -> bytes:
    """Build a full USB HID report for a set/on/off/level command.

    Layout:
      02 | len | 01 7E | 25 0d bc 00 | type ch id_hi id_lo | 05 arg fade level 00 00 | CS | 7F | 8F

    On VRUSB dimmers the **arg** byte is the opcode / coarse level selector;
    putting HA percent into the level data byte with arg=ARG_ON (0x00) always
    drove the load to ~100% (1.1.0b7–b12). Absolute mid-levels use
    ``level_to_dim_arg`` instead.

    CS is CRC-8/MAXIM over the bytes after 0x7E and before CS (unstuffed).
    """
    id_hi = (device_id >> 8) & 0xFF
    id_lo = device_id & 0xFF
    payload = [
        *_PAYLOAD_HEADER,
        device_type & 0xFF,
        channel & 0xFF,
        id_hi,
        id_lo,
        _CMD_SET,
        arg & 0xFF,
        fade & 0xFF,
        level & 0xFF,
        0x00,
        0x00,
    ]
    checksum = crc8_maxim(payload)
    stuffed = _stuff(payload + [checksum])
    body = [_PREFIX, _FRAME_START, *stuffed, _FRAME_END]
    length = len(body)
    return bytes([_REPORT_ID, length, *body, _TRAILER])


def level_to_dim_arg(level: int) -> int:
    """Map Leviton percent 0-100 to a VRUSB SET arg byte.

    Live probes on a VRUSB dimmer showed:

    * ``0x00`` → full 100%
    * ``0x01`` → off
    * ``0x0A`` (and several nearby) → restore last level
    * ``0x0D..0x0F`` (13..15) → about 70 / 80 / 90%
      i.e. ``reported ≈ (arg - 6) * 10``
    * ``0x10`` → ~100% from OFF, but **ignored when already on**
      (70→96 and 80→96 stayed at the prior level). Use ``ARG_ON`` instead.
    * Mid args (e.g. ``0x07`` for ~6%) are also **ignored after full ON**
      (96→6 stayed at 100). Callers must OFF-then-SET — see
      ``needs_off_before_level``.

    Mid-levels encode as ``arg = 6 + round(level / 10)`` (7..15) with an
    empty level data byte. Levels that would round to step 10 (≥~95%) use
    ``ARG_ON`` so absolute jumps to near-full take effect while on.
    """
    level = max(LEVEL_MIN, min(LEVEL_MAX, int(level)))
    if level <= 0:
        return ARG_OFF
    if level >= LEVEL_MAX:
        return ARG_ON
    # Round half up so 25→3 (not banker's 2).
    step = int(level / 10.0 + 0.5)
    step = max(1, min(10, step))
    # step 10 → arg 0x10 is ignored on an already-on dimmer; force full ON.
    if step >= 10:
        return ARG_ON
    return 6 + step


def needs_off_before_level(previous_level: int | None, target_level: int) -> bool:
    """Return True if an absolute SET must be preceded by OFF.

    Basic jump scenarios (live dimmer):

    * full/near-full (``ARG_ON``) → mid level: mid arg is ignored while on
      (96→6 stayed at 100 with bare ``arg=0x07``).
    * mid → full: ``ARG_ON`` works without reset.
    * anything → 0 / 100: OFF / ON opcodes work without reset.
    """
    target = max(LEVEL_MIN, min(LEVEL_MAX, int(target_level)))
    if previous_level is None:
        return False
    if not (LEVEL_MIN < target < 95):
        return False
    return level_to_dim_arg(int(previous_level)) == ARG_ON


def build_set_level_command(
    device_type: int,
    channel: int,
    device_id: int,
    level: int,
    *,
    fade: int = FADE_LEVEL,
) -> bytes:
    """Absolute dimmer level 0-100 via VRUSB arg encoding (level data byte 0)."""
    level = max(LEVEL_MIN, min(LEVEL_MAX, int(level)))
    arg = level_to_dim_arg(level)
    return build_set_command(
        device_type, channel, device_id, arg, fade=fade, level=0
    )


def brightness_to_level(brightness: int) -> int:
    """Map Home Assistant brightness (0-255) to Leviton level (0-100)."""
    if brightness <= 0:
        return LEVEL_MIN
    if brightness >= 255:
        return LEVEL_MAX
    return max(1, min(LEVEL_MAX, round(brightness * LEVEL_MAX / 255)))


def level_to_brightness(level: int) -> int:
    """Map Leviton percent 0-100 to Home Assistant brightness 0-255."""
    level = max(LEVEL_MIN, min(LEVEL_MAX, int(level)))
    if level <= 0:
        return 0
    if level >= LEVEL_MAX:
        return 255
    return max(1, min(255, round(level * 255 / LEVEL_MAX)))


def resolve_profile_levels(
    light: Mapping[str, Any] | None,
) -> Optional[tuple[int, ...]]:
    """Return an allowlist from ``levels`` / ``profile`` on a LIGHTS entry."""
    if not light:
        return None
    raw = light.get("levels")
    if raw is not None:
        try:
            vals = tuple(sorted({max(LEVEL_MIN, min(LEVEL_MAX, int(x))) for x in raw}))
        except (TypeError, ValueError):
            return None
        return vals or None
    profile = light.get("profile")
    if isinstance(profile, str) and profile in DIMMER_PROFILES:
        return DIMMER_PROFILES[profile]
    return None


def snap_level(
    level: int,
    *,
    levels: Sequence[int] | None = None,
    min_level: int | None = None,
) -> int:
    """Snap a percent level to an allowlist and/or floor.

    * ``levels``: nearest member (0 stays 0; positive requests never snap to 0).
    * ``min_level``: when no allowlist, clamp positive levels up to the floor.
    """
    level = max(LEVEL_MIN, min(LEVEL_MAX, int(level)))
    if level <= 0:
        return LEVEL_MIN

    if levels:
        allow = sorted({max(LEVEL_MIN, min(LEVEL_MAX, int(x))) for x in levels})
        positive = [x for x in allow if x > 0]
        if positive:
            # Tie-break toward the higher step so 85→90 not 80 on {80,90}.
            return min(positive, key=lambda x: (abs(x - level), -x))

    if min_level is not None:
        floor = max(LEVEL_MIN, min(LEVEL_MAX, int(min_level)))
        if floor > 0:
            return max(floor, level)
    return level


def snap_level_for_light(light: Mapping[str, Any] | None, level: int) -> int:
    """Snap using a LIGHTS entry's ``levels`` / ``profile`` / ``min_level``."""
    allow = resolve_profile_levels(light)
    min_level = None
    if light is not None and light.get("min_level") is not None:
        try:
            min_level = int(light["min_level"])
        except (TypeError, ValueError):
            min_level = None
    return snap_level(level, levels=allow, min_level=min_level)


def is_dimmer_type(device_type: int) -> bool:
    return device_type in DIMMER_TYPES


def parse_address_from_packet(packet: Sequence[int]) -> dict:
    """Extract type/channel/device_id from a captured 21-byte command packet."""
    if len(packet) < 14:
        raise ValueError("packet too short to contain an address")
    return {
        "type": packet[8],
        "channel": packet[9],
        "device_id": (packet[10] << 8) | packet[11],
    }
