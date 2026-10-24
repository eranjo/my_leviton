"""Capture and probe USB traffic for reverse-engineering dimmer encoding."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple

import usb.core
import usb.util

from .constants import LIGHTS, PID, POLL_COMMAND, VID
from .locks import initialize_usb_lock
from .protocol import (
    ARG_OFF,
    ARG_ON,
    FADE_LEVEL,
    build_set_command,
)

_LOGGER = logging.getLogger(__name__)

# Captured Työhuone katto frames (physical dim):
#   Settled status:  7e type ch id_hi id_lo 04 LEVEL ...
#   Live while dim:  7e type ch id_hi id_lo 22 03 0d bb 03 LEVEL 00 ...
_KIND_STATUS = 0x04
_KIND_LIVE = 0x22
_STATUS_LEVEL_INDEX = 6
_LIVE_LEVEL_INDEX = 10


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


def _clamp_level(value: int) -> Optional[int]:
    """Accept Leviton percent levels 0..100; ignore truncated/garbage bytes."""
    if 0 <= value <= 100:
        return value
    return None


def _level_from_frame(frame: Sequence[int]) -> Optional[int]:
    """Extract 0-100 dimmer level from a status or live-dim frame.

    Also handles glued/command-echo frames seen during probe::
      7e type ch id 25 0d bc 00 04 LEVEL ...
    where a plain ``kind=frame[5]`` read would miss the ``04 LEVEL`` pair.
    """
    if len(frame) < 6:
        return None
    kind = frame[5]
    if kind == _KIND_STATUS and len(frame) > _STATUS_LEVEL_INDEX:
        return _clamp_level(frame[_STATUS_LEVEL_INDEX])
    if kind == _KIND_LIVE and len(frame) > _LIVE_LEVEL_INDEX:
        return _clamp_level(frame[_LIVE_LEVEL_INDEX])
    # Command header stuffed after the address, then a normal status.
    if (
        len(frame) >= 11
        and list(frame[5:9]) == [0x25, 0x0D, 0xBC, 0x00]
        and frame[9] == _KIND_STATUS
    ):
        return _clamp_level(frame[10])
    return None


def _kind_label(kind: Optional[int]) -> str:
    if kind == _KIND_STATUS:
        return "status"
    if kind == _KIND_LIVE:
        return "live"
    if kind is None:
        return "kind=?"
    if kind == 0x25:
        return "cmdhdr"
    return f"kind=0x{kind:02x}"


def _frame_summary(frame: Sequence[int]) -> str:
    if len(frame) < 5:
        return f"short frame={_hex(frame)}"
    kind = frame[5] if len(frame) > 5 else None
    level = _level_from_frame(frame)
    level_s = f"level={level}" if level is not None else "level=?"
    return (
        f"type=0x{frame[1]:02x} ch=0x{frame[2]:02x} "
        f"id=0x{frame[3]:02x}{frame[4]:02x} {_kind_label(kind)} "
        f"{level_s} frame={_hex(frame)}"
    )


@contextmanager
def _open_usb() -> Iterator[usb.core.Device]:
    """Hold the USB lock and yield a configured VRUSB device."""
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

            yield dev
        finally:
            try:
                usb.util.dispose_resources(dev)
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass


def _poll(dev: usb.core.Device) -> None:
    try:
        dev.write(0x01, bytes(POLL_COMMAND), timeout=1000)
    except usb.core.USBError:
        pass


def _read_raw(dev: usb.core.Device, timeout_ms: int = 400) -> Optional[List[int]]:
    try:
        return list(dev.read(0x81, 64, timeout=timeout_ms))
    except usb.core.USBError as err:
        if getattr(err, "errno", None) not in (110, 60, None) and (
            "timed out" not in str(err).lower()
        ):
            _LOGGER.debug("USB read error: %s", err)
        return None


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

    with _open_usb() as dev:
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
                _poll(dev)
                next_poll = now + poll_interval_sec

            raw = _read_raw(dev)
            if raw is None:
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
                kind = frame[5] if len(frame) > 5 else None
                level = _level_from_frame(frame)
                changed = level != last_level
                last_level = level
                marker = "LEVEL" if changed else "same"
                if level is not None:
                    emit(
                        f"{marker} {light_name}: level={level} "
                        f"(0x{level:02x}) {_kind_label(kind)} frame={_hex(frame)}"
                    )
                else:
                    emit(
                        f"{marker} {light_name}: level=? {_kind_label(kind)} "
                        f"frame={_hex(frame)}"
                    )

            if not matched and frames:
                # Help debug address layout if filter misses.
                for frame in frames:
                    if len(frame) < 5 or frame[1] not in (0x1E, 0x2E, 0x26):
                        continue
                    emit(f"other device {_frame_summary(frame)}")

        emit(f"LISTEN done hits={hits} last_level={last_level}")
        return lines


def dump_bus(
    duration_sec: float = 30.0,
    *,
    poll_interval_sec: float = 1.0,
    on_event: Optional[Callable[[str], None]] = None,
) -> List[str]:
    """Poll the stick and log every framed packet (no light filter).

    Use this to discover unknown opcodes / devices on the bus.
    """
    lines: List[str] = []

    def emit(msg: str) -> None:
        _LOGGER.warning("%s", msg)
        lines.append(msg)
        if on_event:
            on_event(msg)

    with _open_usb() as dev:
        emit(f"DUMP_BUS start for {duration_sec:.0f}s")
        deadline = time.monotonic() + duration_sec
        next_poll = 0.0
        last_raw: Optional[Tuple[int, ...]] = None
        hits = 0
        seen: Dict[str, int] = {}

        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_poll:
                _poll(dev)
                next_poll = now + poll_interval_sec

            raw = _read_raw(dev)
            if raw is None:
                continue

            raw_t = tuple(raw)
            if raw_t == last_raw:
                continue
            last_raw = raw_t

            frames = _extract_frames(raw)
            if not frames:
                emit(f"raw_no_frame {_hex(raw)}")
                continue

            for frame in frames:
                hits += 1
                summary = _frame_summary(frame)
                key = summary.split(" frame=")[0]
                seen[key] = seen.get(key, 0) + 1
                emit(f"BUS {summary}")

        emit(f"DUMP_BUS done frames={hits} unique_summaries={len(seen)}")
        for key, count in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0])):
            emit(f"DUMP_SUMMARY count={count} {key}")
        return lines


def _resolve_arg_level(
    level: int,
    arg: Optional[int],
    *,
    level_in_arg: bool,
) -> Tuple[int, int]:
    """Return (arg_byte, level_byte) for a probe SET packet."""
    level = max(0, min(100, int(level)))
    if level_in_arg:
        # Broken layout tried earlier: percent stuffed into arg.
        if arg is not None:
            return arg & 0xFF, 0
        if level <= 0:
            return ARG_OFF, 0
        return level & 0xFF, 0
    if arg is not None:
        return arg & 0xFF, level
    if level <= 0:
        return ARG_OFF, 0
    return ARG_ON, level


def _addr_key(frame: Sequence[int]) -> Optional[str]:
    if len(frame) < 5:
        return None
    return f"0x{frame[1]:02x}/0x{frame[2]:02x}/0x{frame[3]:02x}{frame[4]:02x}"


def _read_light_level(
    dev: usb.core.Device,
    light: Dict,
    *,
    settle_sec: float,
    wait_sec: float,
    poll_interval_sec: float,
) -> Tuple[Optional[int], List[str]]:
    """After a SET, drain status until ``light`` appears or ``wait_sec`` elapses.

    A full-network POLL streams many HID reports. Re-polling every few hundred
    ms restarts that stream and can starve later devices (e.g. 0x1052). Match
    ``dump_bus`` pacing: poll slowly, read continuously.
    """
    notes: List[str] = []
    if settle_sec > 0:
        time.sleep(settle_sec)

    last_level: Optional[int] = None
    frames_seen = 0
    matched_frames = 0
    seen: Dict[str, int] = {}
    # Poll immediately once, then leave a long gap so the stick can finish.
    next_poll = 0.0
    poll_gap = max(1.5, float(poll_interval_sec))
    deadline = time.monotonic() + max(0.5, wait_sec)

    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_poll:
            _poll(dev)
            next_poll = now + poll_gap

        raw = _read_raw(dev, timeout_ms=250)
        if raw is None:
            continue

        for frame in _extract_frames(raw):
            frames_seen += 1
            key = _addr_key(frame)
            if key:
                seen[key] = seen.get(key, 0) + 1
            if not _match_light(frame, light):
                continue
            matched_frames += 1
            level = _level_from_frame(frame)
            kind = frame[5] if len(frame) > 5 else None
            notes.append(
                f"rx {_kind_label(kind)} level={level} frame={_hex(frame)}"
            )
            if level is not None:
                last_level = level
                # Prefer a settled status (plain 0x04 or cmd-header+0x04).
                if kind == _KIND_STATUS or (
                    kind == 0x25 and level is not None
                ):
                    notes.append(
                        f"wait_ok frames={frames_seen} matched={matched_frames}"
                    )
                    return last_level, notes

    top = sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))[:12]
    seen_s = ",".join(f"{k}*{c}" for k, c in top) if top else "(none)"
    notes.append(
        f"wait_timeout frames={frames_seen} matched={matched_frames} "
        f"last_level={last_level} seen={seen_s}"
    )
    return last_level, notes


def probe_level(
    light_name: str,
    *,
    level: Optional[int] = None,
    sweep: bool = False,
    step: int = 5,
    arg: Optional[int] = None,
    fade: Optional[int] = None,
    level_in_arg: bool = False,
    settle_sec: float = 0.5,
    wait_sec: float = 20.0,
    reset_before: bool = True,
    on_event: Optional[Callable[[str], None]] = None,
) -> List[str]:
    """Send SET packets and log the status level the device reports back.

    Single probe: pass ``level`` (0-100).
    Sweep: ``sweep=True`` walks 0..100 by ``step`` (always includes 0 and 100).

    Encoding knobs for A/B tests:
      - default: ``05 ARG_ON fade LEVEL`` (level 0 → ARG_OFF)
      - ``level_in_arg=True``: put percent in the arg byte
      - ``arg`` / ``fade``: override those bytes explicitly

    ``wait_sec`` is how long to drain status for *this* light after each SET.
    Polls are intentionally slow (~2s) so the stick can finish streaming the
    full device list (fast re-poll was starving later addresses).

    ``reset_before`` (default True) sends OFF and waits for level 0 before each
    non-zero SET so a prior mid-level cannot contaminate the next sample.
    """
    light = lookup_light(light_name)
    if not light:
        raise ValueError(f"Unknown light: {light_name}")
    if not light.get("dimmer"):
        raise ValueError(f"Light {light_name!r} is not a dimmer")

    fade_byte = FADE_LEVEL if fade is None else (fade & 0xFF)
    step = max(1, min(100, int(step)))
    wait_sec = max(1.0, float(wait_sec))

    if sweep:
        levels = list(range(0, 101, step))
        if levels[-1] != 100:
            levels.append(100)
    elif level is None:
        raise ValueError("probe_level requires level= or sweep=true")
    else:
        levels = [max(0, min(100, int(level)))]

    lines: List[str] = []

    def emit(msg: str) -> None:
        _LOGGER.warning("%s", msg)
        lines.append(msg)
        if on_event:
            on_event(msg)

    def _write_packet(dev: usb.core.Device, packet: bytes, label: str) -> bool:
        try:
            written = dev.write(0x01, packet, timeout=1000)
            ok = written == len(packet)
        except usb.core.USBError as err:
            emit(f"PROBE_TX_FAIL {label} err={err}")
            return False
        if not ok:
            emit(f"PROBE_TX_FAIL {label} short_write")
            return False
        return True

    with _open_usb() as dev:
        emit(
            f"PROBE start light={light_name!r} "
            f"type=0x{light['type']:02x} ch=0x{light['channel']:02x} "
            f"id=0x{light['device_id']:04x} "
            f"levels={levels[0]}..{levels[-1]} n={len(levels)} "
            f"fade=0x{fade_byte:02x} level_in_arg={level_in_arg} "
            f"arg_override={None if arg is None else f'0x{arg:02x}'} "
            f"reset_before={reset_before} "
            f"settle={settle_sec}s wait={wait_sec}s poll_gap=2.0s"
        )
        emit(
            "PROBE_CSV sent_level,arg,fade,level_byte,reported,match,tx_hex"
        )

        for sent in levels:
            if reset_before and sent > 0:
                off_pkt = build_set_command(
                    light["type"],
                    light["channel"],
                    light["device_id"],
                    ARG_OFF,
                    fade=fade_byte,
                    level=0,
                )
                if _write_packet(dev, off_pkt, f"reset_off before={sent}"):
                    emit(f"PROBE_RESET before={sent} tx={_hex(off_pkt)}")
                    baselined, base_notes = _read_light_level(
                        dev,
                        light,
                        settle_sec=settle_sec,
                        wait_sec=wait_sec,
                        poll_interval_sec=2.0,
                    )
                    for note in base_notes[-3:]:
                        emit(f"PROBE_RESET_RX before={sent} {note}")
                    emit(
                        f"PROBE_RESET_RESULT before={sent} "
                        f"reported={baselined}"
                    )

            arg_b, level_b = _resolve_arg_level(
                sent, arg, level_in_arg=level_in_arg
            )
            packet = build_set_command(
                light["type"],
                light["channel"],
                light["device_id"],
                arg_b,
                fade=fade_byte,
                level=level_b,
            )
            if not _write_packet(dev, packet, f"sent={sent}"):
                continue

            emit(
                f"PROBE_TX sent={sent} arg=0x{arg_b:02x} fade=0x{fade_byte:02x} "
                f"level_byte=0x{level_b:02x} tx={_hex(packet)}"
            )

            reported, notes = _read_light_level(
                dev,
                light,
                settle_sec=settle_sec,
                wait_sec=wait_sec,
                poll_interval_sec=2.0,
            )
            for note in notes[-6:]:
                emit(f"PROBE_RX sent={sent} {note}")

            match = (
                "yes"
                if reported is not None and reported == sent
                else "no"
                if reported is not None
                else "none"
            )
            emit(
                f"PROBE_CSV {sent},{arg_b},{fade_byte},{level_b},"
                f"{'' if reported is None else reported},{match},"
                f"{_hex(packet)}"
            )
            emit(
                f"PROBE_RESULT sent={sent} reported={reported} match={match}"
            )

        emit("PROBE done")
        return lines
