"""Capture USB status reports while a physical dimmer is adjusted."""
from __future__ import annotations

import logging
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import usb.core
import usb.util

from .constants import LIGHTS, PID, POLL_COMMAND, VID
from .locks import initialize_usb_lock

_LOGGER = logging.getLogger(__name__)

# Status frame after 0x7E (from historical polling parser):
#   [0]=7E [1]=type [2]=channel [3]=id_hi [4]=id_lo [5]=? [6]=level
_STATUS_LEVEL_INDEX = 6


def lookup_light(name: str) -> Optional[Dict]:
    for light in LIGHTS:
        if light.get("name") == name:
            return light
    return None


def _hex(data: Sequence[int]) -> str:
    return " ".join(f"{b:02x}" for b in data)


def _extract_frames(raw: Sequence[int]) -> List[List[int]]:
    """Split a USB read into 0x7E-framed payloads (best-effort)."""
    data = [b for b in raw if b != 0xFF]
    frames: List[List[int]] = []
    i = 0
    while i < len(data):
        if data[i] != 0x7E:
            i += 1
            continue
        # Prefer explicit end marker; otherwise take a short window.
        try:
            end = data.index(0x7F, i + 1)
            frames.append(data[i : end + 1])
            i = end + 1
        except ValueError:
            frames.append(data[i : i + 16])
            break
    return frames


def _match_light(frame: Sequence[int], light: Dict) -> bool:
    if len(frame) < 5:
        return False
    # frame[0] is 0x7E; address follows like command packets.
    return (
        frame[1] == light["type"]
        and frame[2] == light["channel"]
        and frame[3] == ((light["device_id"] >> 8) & 0xFF)
        and frame[4] == (light["device_id"] & 0xFF)
    )


def _level_from_frame(frame: Sequence[int]) -> Optional[int]:
    if len(frame) <= _STATUS_LEVEL_INDEX:
        return None
    return frame[_STATUS_LEVEL_INDEX]


def listen_dimmer(
    light_name: str,
    duration_sec: float = 90.0,
    *,
    poll_interval_sec: float = 1.0,
    on_event: Optional[Callable[[str], None]] = None,
) -> List[str]:
    """Listen for USB traffic matching one light while the user dims it.

    Returns a list of human-readable event lines. Also logs them.
    Holds the USB device exclusively for the duration.
    """
    light = lookup_light(light_name)
    if not light:
        raise ValueError(f"Unknown light: {light_name}")

    lines: List[str] = []

    def emit(msg: str) -> None:
        _LOGGER.warning("%s", msg)
        lines.append(msg)
        if on_event:
            on_event(msg)

    lock = initialize_usb_lock()
    with lock:
        dev = usb.core.find(idVendor=VID, idProduct=PID)
        if dev is None:
            raise RuntimeError(f"USB device {VID:04x}:{PID:04x} not found")

        try:
            try:
                if dev.is_kernel_driver_active(0):
                    dev.detach_kernel_driver(0)
            except (NotImplementedError, usb.core.USBError):
                pass

            try:
                dev.set_configuration()
            except usb.core.USBError:
                pass

            emit(
                f"LISTEN start light={light_name!r} "
                f"type=0x{light['type']:02x} ch=0x{light['channel']:02x} "
                f"id=0x{light['device_id']:04x} for {duration_sec:.0f}s — "
                "use the physical dimmer now"
            )

            deadline = time.monotonic() + duration_sec
            next_poll = 0.0
            last_level: Optional[int] = None
            last_raw: Optional[Tuple[int, ...]] = None
            hits = 0

            while time.monotonic() < deadline:
                now = time.monotonic()
                if now >= next_poll:
                    try:
                        dev.write(0x01, bytes(POLL_COMMAND), timeout=1000)
                    except usb.core.USBError as err:
                        emit(f"poll write failed: {err}")
                    next_poll = now + poll_interval_sec

                try:
                    raw = list(dev.read(0x81, 64, timeout=400))
                except usb.core.USBError as err:
                    # 110 = timeout on Linux; ignore.
                    if getattr(err, "errno", None) not in (110, 60, None) and (
                        "timed out" not in str(err).lower()
                    ):
                        emit(f"read error: {err}")
                    continue

                raw_t = tuple(raw)
                if raw_t == last_raw:
                    continue
                last_raw = raw_t

                frames = _extract_frames(raw)
                matched = False
                for frame in frames:
                    if not _match_light(frame, light):
                        continue
                    matched = True
                    hits += 1
                    level = _level_from_frame(frame)
                    changed = level != last_level
                    last_level = level
                    marker = "LEVEL" if changed else "same"
                    if level is not None:
                        emit(
                            f"{marker} {light_name}: level={level} "
                            f"(0x{level:02x}) frame={_hex(frame)}"
                        )
                    else:
                        emit(
                            f"{marker} {light_name}: level=? frame={_hex(frame)}"
                        )

                if not matched and frames:
                    # Help debug address layout if filter misses.
                    for frame in frames:
                        if len(frame) < 5 or frame[1] not in (0x1E, 0x2E, 0x26):
                            continue
                        if len(frame) > 6:
                            emit(
                                f"other device frame type=0x{frame[1]:02x} "
                                f"ch=0x{frame[2]:02x} "
                                f"id=0x{frame[3]:02x}{frame[4]:02x} "
                                f"b5=0x{frame[5]:02x} b6=0x{frame[6]:02x} "
                                f"raw={_hex(frame)}"
                            )
                        else:
                            emit(f"other short frame={_hex(frame)}")

            emit(f"LISTEN done hits={hits} last_level={last_level}")
            return lines
        finally:
            try:
                usb.util.dispose_resources(dev)
            except Exception:
                pass
