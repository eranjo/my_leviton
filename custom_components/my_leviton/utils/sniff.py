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
    TYPE_DIMMER,
    TYPE_DIMMER_ALT,
    TYPE_RELAY,
    build_set_command,
    build_set_level_command,
    level_to_dim_arg,
)

_LOGGER = logging.getLogger(__name__)

# Captured dimmer frames (physical dim):
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


# HID reads are 64 bytes; named status frames often span 2+ reports.
# Cap an open reassembled frame so a lost 0x7F cannot grow forever.
_MAX_REASSEMBLY_BYTES = 192
# Flush an open prefix that is long enough for type/ch/id/kind/level.
_MIN_PARTIAL_FRAME = 6
# VRUSB HID report id (same as protocol TX).
_HID_REPORT_ID = 0x02
# Load types whose status payloads may omit the leading 0x7E on the wire.
_LOAD_TYPES = frozenset({TYPE_RELAY, TYPE_DIMMER, TYPE_DIMMER_ALT})


def _strip_hid_padding(raw: Sequence[int]) -> List[int]:
    """Drop USB/HID 0xFF pad bytes (also appear between 0x7C and ASCII)."""
    return [b for b in raw if b != 0xFF]


def _unwrap_hid_report(raw: Sequence[int]) -> List[int]:
    """Strip HID report id/length prefix, then 0xFF padding.

    VRUSB interrupt reads are ``02 | len | payload…`` padded to 64 bytes.
    Using *len* avoids trailing garbage (spurious ``0x7F`` / next fragment).
    """
    data = list(raw)
    if len(data) >= 2 and data[0] == _HID_REPORT_ID:
        length = int(data[1]) & 0xFF
        end = 2 + length
        if end > len(data):
            end = len(data)
        data = data[2:end]
    return _strip_hid_padding(data)


def _status_header_at(data: Sequence[int], index: int) -> bool:
    """True when data[index:] looks like type/ch/id_hi/id_lo/kind(status|live)."""
    if index + 4 >= len(data):
        return False
    return (
        data[index] in _LOAD_TYPES
        and data[index + 4] in (_KIND_STATUS, _KIND_LIVE)
    )


def _unframed_status_offset(data: Sequence[int]) -> Optional[int]:
    """Return index of an unframed status header at the start of *data*, else None.

    Many named status HID reports omit ``0x7E`` and begin directly with
    ``type ch id_hi id_lo 04 LEVEL … 7c NAME``. Detecting that lets us start a
    new frame instead of gluing the payload onto a different channel's open
    prefix (cross-channel ``bus_name`` bug on multi-channel relays).
    """
    if _status_header_at(data, 0):
        return 0
    # Occasional leading NUL / noise before the address.
    if len(data) > 1 and data[0] == 0x00 and _status_header_at(data, 1):
        return 1
    return None


def _name_restart_offset(data: Sequence[int]) -> Optional[int]:
    """Index of a ``0x7C`` name-restart continuation, else None.

    Dump pattern: report 1 ends with ``7c`` + truncated ASCII; report 2 is
    ``[00…] 7c FULLNAME … 7f``. Appending blindly yields two ``7c`` runs and
    the first (truncated) name wins unless we replace from the prior ``7c``.
    """
    i = 0
    while i < len(data) and data[i] == 0x00:
        i += 1
    if i < len(data) and data[i] == 0x7C:
        return i
    return None


def _raw_ascii_after(frame: Sequence[int], start: int) -> str:
    """Printable ASCII from *start* until NUL / control / 0x7F (may be short)."""
    chars: List[str] = []
    for byte in frame[start:]:
        if byte in (0x00, 0x7F) or byte < 0x20:
            break
        if 0x20 <= byte <= 0x7E:
            chars.append(chr(byte))
        # else: skip 0x80–0xFF noise and continue joining ASCII
    return "".join(chars).strip()


def _ascii_run_after(frame: Sequence[int], start: int) -> Optional[str]:
    """Printable ASCII from *start* until NUL / control / 0x7F."""
    name = _raw_ascii_after(frame, start)
    return name if _is_clean_bus_name(name) else None


def _stub_after_7c(buf: Sequence[int]) -> str:
    """Truncated wire-name stub already buffered after the first ``0x7C``."""
    if 0x7C not in buf:
        return ""
    return _raw_ascii_after(buf, buf.index(0x7C) + 1)


def _name_restart_compatible(stub: str, restart: str) -> bool:
    """True when *restart* continues the same label as *stub* (not another device).

    Dump restarts replace a truncated prefix (``AA.BB.CC`` → ``AA.BB.CCCC``).
    An unrelated restart (``Xx`` open, ``AA.BB.CCCC`` arrives) must not merge.
    """
    if not restart:
        return False
    if not stub:
        return True
    return restart.startswith(stub) or stub.startswith(restart)


def _extract_frames(raw: Sequence[int]) -> List[List[int]]:
    """Split a byte buffer into complete 0x7E…0x7F frames.

    Incomplete trailing 0x7E prefixes are *not* emitted (the old 16-byte
    fallback truncated name tails mid-report). Use :class:`HidFrameBuffer`
    to reassemble across HID reads, or pass an already-joined buffer.
    """
    data = _strip_hid_padding(raw)
    frames, _carry = _drain_complete_frames(data)
    return frames


def _drain_complete_frames(data: List[int]) -> Tuple[List[List[int]], List[int]]:
    """Return (complete 0x7E…0x7F frames, leftover carry without a closing 0x7F)."""
    frames: List[List[int]] = []
    i = 0
    while i < len(data):
        if data[i] != 0x7E:
            i += 1
            continue
        try:
            end = data.index(0x7F, i + 1)
        except ValueError:
            return frames, data[i:]
        frames.append(data[i : end + 1])
        i = end + 1
    return frames, []


class HidFrameBuffer:
    """Reassemble Leviton bus frames that span multiple 64-byte HID reads.

    Status replies with wire names (``7c…ASCII…7f``) frequently put the
    ``0x7E`` header in one report and the name / ``0x7F`` in the next.
    Feeding each USB read here concatenates until complete frames emerge.

    Critical: some reports carry a full ``type/ch/id/…/7c/NAME`` payload
    *without* a leading ``0x7E``. Those must start a new frame — never append
    onto a pending prefix for a different channel (same ``device_id``).

    Also critical: after a channel switch *drops* a truncated ``7c`` stub, a
    later name-restart report for the dropped address must not glue onto the
    newly opened header (a name for one channel glued onto a different
    module).

    Orphan bare ``7c NAME`` restarts must never glue onto a nameless open
    header either (a dimmer inherited another keypad's wire name).
    Same-device names arrive as mid-payload bytes before ``7c``, or as
    unframed ``type/ch/id/…/7c/NAME`` reports — not as ambiguous restarts.
    Type ``0x1E``/``0x2E`` dimmers almost never carry wire names in dumps;
    reject any ``7c`` that only appears via continuation glue onto them.
    """

    def __init__(self) -> None:
        self._buf: List[int] = []
        # Stub dropped on channel switch — used to recognize orphaned restarts.
        self._dropped_stub: str = ""

    @property
    def pending(self) -> bool:
        """True when an open 0x7E… frame is waiting for 0x7F."""
        return bool(self._buf)

    @staticmethod
    def _open_is_dimmer(buf: Sequence[int]) -> bool:
        return len(buf) >= 2 and buf[0] == 0x7E and buf[1] in (
            TYPE_DIMMER,
            TYPE_DIMMER_ALT,
        )

    def feed(self, raw: Sequence[int]) -> List[List[int]]:
        """Append one HID read; return any newly completed frames."""
        chunk = _unwrap_hid_report(raw)
        if not chunk:
            return []

        out: List[List[int]] = []
        unframed_at = _unframed_status_offset(chunk)
        name_at = _name_restart_offset(chunk) if unframed_at is None else None
        if unframed_at is not None:
            # New status address in this report (often missing 0x7E on wire).
            # Never glue onto a prior open prefix — that bound e.g.
            # one channel's wire name onto a different channel.
            if (
                self._buf
                and len(self._buf) >= _MIN_PARTIAL_FRAME
                and self._buf[0] == 0x7E
                and 0x7C not in self._buf
            ):
                # Level-only prior header: emit for addr/level, then switch.
                out.append(list(self._buf))
            elif self._buf and 0x7C in self._buf:
                # Prior open ``|partial`` name is dropped (avoids truncated
                # stubs). Remember the stub so a later name-restart for
                # that address is not glued onto the new channel's header.
                stub = _stub_after_7c(self._buf)
                if stub:
                    self._dropped_stub = stub
            self._buf = [0x7E, *chunk[unframed_at:]]
        elif self._buf and name_at is not None:
            restart = _raw_ascii_after(chunk, name_at + 1)
            if 0x7C in self._buf:
                stub = _stub_after_7c(self._buf)
                if _name_restart_compatible(stub, restart):
                    # Same-channel name restart: replace truncated ``7c`` tail.
                    # Dimmer prefixes still reject — dumps show they lack names.
                    if self._open_is_dimmer(self._buf):
                        self._dropped_stub = ""
                    else:
                        first_7c = self._buf.index(0x7C)
                        self._buf = self._buf[:first_7c] + list(chunk[name_at:])
                        self._dropped_stub = ""
                elif self._dropped_stub and _name_restart_compatible(
                    self._dropped_stub, restart
                ):
                    # Restart belongs to the channel we just dropped — ignore.
                    self._dropped_stub = ""
                # else: unrelated restart while a stub is open — ignore chunk.
            elif self._dropped_stub and _name_restart_compatible(
                self._dropped_stub, restart
            ):
                # Open header has no name yet; restart is for the dropped
                # channel (an open header must not inherit another channel's name).
                self._dropped_stub = ""
            else:
                # Bare ``7c NAME`` on a nameless open header is ambiguous —
                # prefer reject over wrong attribution (dimmer ← another keypad).
                # Real same-device names use mid-payload or unframed headers.
                pass
        elif self._buf:
            # True continuation (name tail / mid-payload) of the open frame.
            # Dimmer dumps rarely/never carry wire names; a ``7c`` that only
            # appears in a continuation is cross-frame glue — drop the chunk.
            if (
                0x7C not in self._buf
                and 0x7C in chunk
                and self._open_is_dimmer(self._buf)
            ):
                return out
            self._buf.extend(chunk)
        elif 0x7E in chunk:
            self._buf = chunk[chunk.index(0x7E) :]
        else:
            # Orphan name tail / padding without an open header — drop.
            return out

        if len(self._buf) > _MAX_REASSEMBLY_BYTES:
            # Lost end marker — keep a trailing window that may still close.
            self._buf = self._buf[-_MAX_REASSEMBLY_BYTES :]
            if 0x7E in self._buf:
                self._buf = self._buf[self._buf.index(0x7E) :]
            else:
                self._buf = []
                return out

        frames, self._buf = _drain_complete_frames(self._buf)
        out.extend(frames)
        return out

    def flush(self) -> List[List[int]]:
        """Emit a trailing open 0x7E prefix (addr/level) and clear the buffer."""
        frames, carry = _drain_complete_frames(self._buf)
        self._buf = []
        self._dropped_stub = ""
        if len(carry) >= _MIN_PARTIAL_FRAME and carry[0] == 0x7E:
            frames.append(carry)
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


def _is_clean_bus_name(name: Optional[str]) -> bool:
    """True when *name* is usable wire text (printable ASCII with an alnum).

    Rejects empty strings, punctuation-only junk (``§``, ``|``, ``...``),
    and any codepoint outside 0x20–0x7E (Latin-1 mojibake from high bus bytes).
    """
    if not name:
        return False
    if any(ord(c) < 0x20 or ord(c) > 0x7E for c in name):
        return False
    return any(c.isalnum() for c in name)


def _bus_name_from_frame(frame: Sequence[int]) -> Optional[str]:
    """Extract a bus-defined device name from ``0x7C``-delimited ASCII runs.

    Some kind=0x04 status replies carry ``… 7c ASCII… 7f`` (0x7C is '|').
    HID padding 0xFF is usually stripped by ``_extract_frames`` /
    :class:`HidFrameBuffer` first. Callers keep the longest *clean* name
    seen per address. Returns None when no usable printable run is present.

    Leviton wire labels are printable ASCII (0x20–0x7E) only. High bytes
    (0x80–0xFF) are skipped so a single corrupt mid-name byte does not
    insert Latin-1 mojibake (ò, Â, §) or truncate the rest of the label.
    NUL / controls / 0x7F stop each run.

    When reassembly briefly leaves two ``7c`` runs (truncated prefix + full
    restart), the longest clean run wins — not the first.

    Names bind only to *this* frame's ``type/ch/id`` header. If a second
    status address appears before any ``0x7C`` (HID glue of two channels),
    the name is rejected so it cannot attach to the wrong channel.
    """
    if len(frame) < 6 or frame[0] != 0x7E:
        return None
    best: Optional[str] = None
    for idx, byte in enumerate(frame):
        if byte != 0x7C:
            continue
        # After type/ch/id/kind/level (index 6+): reject a second load-status
        # header sneaking in before this name delimiter.
        if any(_status_header_at(frame, j) for j in range(6, idx)):
            return None
        name = _ascii_run_after(frame, idx + 1)
        if name is not None and (best is None or len(name) > len(best)):
            best = name
    return best


def _prefer_longer_name(
    current: Optional[str], candidate: Optional[str]
) -> Optional[str]:
    """Prefer a clean ASCII bus name; among related clean names keep the longest.

    A longer garbled / punctuation-only string must not displace a shorter
    clean label (and junk candidates are ignored entirely).

    Unrelated clean labels (no shared prefix) must not replace each other —
    defense in depth against cross-channel glue (one clean label must not
    lose to a longer unrelated label from another address).
    """
    cur_ok = _is_clean_bus_name(current)
    cand_ok = _is_clean_bus_name(candidate)
    if not cand_ok:
        return current if cur_ok else None
    if not cur_ok:
        return candidate
    assert current is not None and candidate is not None
    related = candidate.startswith(current) or current.startswith(candidate)
    if not related:
        return current
    if len(candidate) > len(current):
        return candidate
    return current


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
        hid_buf = HidFrameBuffer()

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

            frames = hid_buf.feed(raw)
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
        hid_buf = HidFrameBuffer()

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

            frames = hid_buf.feed(raw)
            if not frames:
                # Still useful when debugging orphans / mid-name chunks.
                if 0x7E not in raw and hid_buf.pending:
                    emit(f"raw_continuation {_hex(raw)}")
                elif 0x7E not in raw:
                    emit(f"raw_no_frame {_hex(raw)}")
                continue

            for frame in frames:
                hits += 1
                summary = _frame_summary(frame)
                key = summary.split(" frame=")[0]
                seen[key] = seen.get(key, 0) + 1
                bus_name = _bus_name_from_frame(frame)
                if bus_name:
                    emit(f"BUS {summary} name={bus_name!r}")
                else:
                    emit(f"BUS {summary}")

        for frame in hid_buf.flush():
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
    ms restarts that stream and can starve later devices. Match
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
    hid_buf = HidFrameBuffer()

    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_poll:
            _poll(dev)
            next_poll = now + poll_gap

        raw = _read_raw(dev, timeout_ms=250)
        if raw is None:
            continue

        for frame in hid_buf.feed(raw):
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

    for frame in hid_buf.flush():
        frames_seen += 1
        key = _addr_key(frame)
        if key:
            seen[key] = seen.get(key, 0) + 1
        if not _match_light(frame, light):
            continue
        matched_frames += 1
        level = _level_from_frame(frame)
        if level is not None:
            last_level = level

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

            # Default path uses the shared production encoder (level_to_dim_arg).
            # Explicit arg / level_in_arg keep the old A/B layouts for RE.
            if arg is None and not level_in_arg:
                packet = build_set_level_command(
                    light["type"],
                    light["channel"],
                    light["device_id"],
                    sent,
                    fade=fade_byte,
                )
                arg_b = level_to_dim_arg(sent)
                level_b = 0
            else:
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
