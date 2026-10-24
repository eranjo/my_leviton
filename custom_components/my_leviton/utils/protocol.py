"""Leviton USB HID framing and CRC-8/MAXIM checksum."""

from __future__ import annotations

from typing import Iterable, List, Sequence

# Device type bytes observed on the wire / in status polling.
TYPE_RELAY = 0x26
TYPE_DIMMER = 0x1E
TYPE_DIMMER_ALT = 0x2E
DIMMER_TYPES = frozenset({TYPE_DIMMER, TYPE_DIMMER_ALT})

# Known primary switch args (captured from official app traffic).
ARG_ON = 0x00
ARG_OFF = 0x01

# Status reports use 0x00..0x64 for off..100%.
LEVEL_MIN = 0
LEVEL_MAX = 100

_FRAME_START = 0x7E
_FRAME_END = 0x7F
_TRAILER = 0x8F
_ESCAPE = 0x7C
_REPORT_ID = 0x02
_PREFIX = 0x01
_PAYLOAD_HEADER = (0x25, 0x0D, 0xBC, 0x00)
_CMD_SET = 0x05
# Captured on/off packets use fade=0x03 (~slow ramp). Absolute dimmer
# levels from the HA slider must use fade=0 so each step is immediate;
# otherwise mid-range slider moves feel like dead zones.
_FADE = 0x03
FADE_ON_OFF = _FADE
FADE_LEVEL = 0x00


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
) -> bytes:
    """Build a full USB HID report for a set/on/off/level command.

    Layout:
      02 | len | 01 7E | 25 0d bc 00 | type ch id_hi id_lo | 05 arg fade 00 00 00 | CS | 7F | 8F

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
        0x00,
        0x00,
        0x00,
    ]
    checksum = crc8_maxim(payload)
    stuffed = _stuff(payload + [checksum])
    body = [_PREFIX, _FRAME_START, *stuffed, _FRAME_END]
    length = len(body)
    return bytes([_REPORT_ID, length, *body, _TRAILER])


def brightness_to_level(brightness: int) -> int:
    """Map Home Assistant brightness (0-255) to Leviton level (0-100)."""
    if brightness <= 0:
        return LEVEL_MIN
    if brightness >= 255:
        return LEVEL_MAX
    return max(1, min(LEVEL_MAX, round(brightness * LEVEL_MAX / 255)))


def level_to_arg(level: int) -> int:
    """Map 0-100 level to the command argument byte for dimmers.

    Uses the same 0..100 scale as status reports. Level 0 sends ARG_OFF (0x01),
    which matches captured off packets; 1..100 are sent as absolute levels
    (100 = 0x64). Plain ON without a level still uses ARG_ON (0x00) separately.
    """
    level = max(LEVEL_MIN, min(LEVEL_MAX, int(level)))
    if level <= 0:
        return ARG_OFF
    return level


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
