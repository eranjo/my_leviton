"""Hardware status → HA entity state helpers and USB status reads.

LevitonLight historically was optimistic-only (SET then assume). This module
provides short post-SET readback, a shared bus poll, and a lightweight
continuous listener so HA tracks reported levels (including wall-button
changes) without starving turn_on/off.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple

from .constants import LIGHTS
from .protocol import LEVEL_MAX, brightness_to_level
from .sniff import (
    _extract_frames,
    _level_from_frame,
    _match_light,
    _open_usb,
    _poll,
    _read_raw,
    lookup_light,
)

_LOGGER = logging.getLogger(__name__)

# Shared cache so N polling entities do not each open USB in the same window.
# Separate from USB_LOCK — never nest this with _open_usb (non-reentrant).
_CACHE: Dict[str, int] = {}
_CACHE_MONO: float = 0.0
_CACHE_LOCK = threading.Lock()
_POLL_IN_FLIGHT = threading.Lock()
_CACHE_TTL_SEC = 2.0

# Local SETs mark this so shared bus polls / listener yield (return cache /
# skip USB) instead of holding USB_LOCK and starving turn_on/off.
_CMD_MONO: float = 0.0
_CMD_LOCK = threading.Lock()
_CMD_QUIET_SEC = 1.25

# Post-SET readback (background only — service calls never await this).
# Wall-button LEDs flip immediately: the device announces quickly, so take the
# first matching frame. Long multi-second drains only starve USB commands.
_READBACK_SETTLE_SEC = 0.0
_READBACK_WAIT_SEC = 0.35
_READBACK_POLL_GAP = 0.35
# 0 = exit on first matching sample for only_name (wall LED proves it's enough).
_READBACK_AFTER_SAMPLE_SEC = 0.0

# Periodic / listener bus sample: brief drain only. Must stay shorter than a
# human toggle gap so SETs can grab USB_LOCK between bursts.
_BUS_POLL_WAIT_SEC = 0.6
_BUS_POLL_GAP = 0.5
# Continuous listener: short USB hold + idle gap → wall→HA under ~1s while
# leaving windows for commands (b18 priority). Wait must be long enough to
# catch this light in a full-network POLL stream (~25 devices); idle is the
# command-priority window between bursts.
_LISTEN_WAIT_SEC = 0.55
_LISTEN_POLL_GAP = 0.55
_LISTEN_IDLE_SEC = 0.12


def mark_command_activity() -> None:
    """Note a local SET so bus polls / listener defer USB access to commands."""
    global _CMD_MONO
    with _CMD_LOCK:
        _CMD_MONO = time.monotonic()


def command_activity_recent(*, within_sec: float = _CMD_QUIET_SEC) -> bool:
    """True if a local SET happened within ``within_sec``."""
    with _CMD_LOCK:
        if _CMD_MONO <= 0:
            return False
        return (time.monotonic() - _CMD_MONO) < within_sec


def level_to_brightness(level: int) -> int:
    """Map Leviton percent 0-100 to Home Assistant brightness 0-255."""
    level = max(0, min(LEVEL_MAX, int(level)))
    if level <= 0:
        return 0
    if level >= LEVEL_MAX:
        return 255
    return max(1, min(255, round(level * 255 / LEVEL_MAX)))


def level_to_entity_state(level: int, *, dimmer: bool) -> Tuple[bool, Optional[int]]:
    """Return (is_on, brightness_or_None) from a reported percent level."""
    level = max(0, min(LEVEL_MAX, int(level)))
    is_on = level > 0
    if not dimmer:
        return is_on, None
    return is_on, level_to_brightness(level)


def settle_reported_level(samples: Sequence[Optional[int]]) -> Optional[int]:
    """Choose a settled level from chronologically ordered samples.

    Returns the last non-None sample. That avoids OFF-echo false settles when
    callers collect for the full wait window (0 then mid → mid wins).
    """
    values = [int(s) for s in samples if s is not None]
    if not values:
        return None
    return values[-1]


def _addr_tuple(frame: Sequence[int]) -> Optional[Tuple[int, int, int]]:
    if len(frame) < 5:
        return None
    return (frame[1], frame[2], (frame[3] << 8) | frame[4])


def _index_lights() -> Dict[Tuple[int, int, int], Dict]:
    return {
        (L["type"], L["channel"], L["device_id"]): L for L in LIGHTS
    }


def _collect_levels(
    wait_sec: float,
    *,
    settle_sec: float = 0.0,
    poll_gap: float = _BUS_POLL_GAP,
    only_name: Optional[str] = None,
    abort_on_command: bool = False,
    exit_when_changed_from: Optional[Dict[str, int]] = None,
) -> Dict[str, List[Optional[int]]]:
    """Open USB once, POLL, and collect status/live levels per light name.

    Settle sleeps *before* taking the USB lock so commands are not blocked by
    idle wait. Single-light reads exit after the first matching frame once
    ``_READBACK_AFTER_SAMPLE_SEC`` has elapsed (0 = first frame wins).

    When ``abort_on_command`` is set, exit early if ``mark_command_activity``
    was called so SETs are not serialized behind a status drain.

    When ``exit_when_changed_from`` is set, return as soon as any known light
    reports a level different from that baseline (wall-button fast path).
    """
    by_name: Dict[str, List[Optional[int]]] = {}
    index = _index_lights()
    only_light = lookup_light(only_name) if only_name else None
    if only_name and not only_light:
        _LOGGER.error("Unknown light for status read: %s", only_name)
        return by_name

    # Do not hold USB_LOCK during settle — SET packets must stay responsive.
    if settle_sec > 0:
        time.sleep(settle_sec)

    if abort_on_command and command_activity_recent():
        return by_name

    first_sample_mono: Optional[float] = None
    with _open_usb() as dev:
        deadline = time.monotonic() + max(0.25, wait_sec)
        next_poll = 0.0
        while time.monotonic() < deadline:
            if abort_on_command and command_activity_recent():
                break
            now = time.monotonic()
            if (
                only_name
                and first_sample_mono is not None
                and (now - first_sample_mono) >= _READBACK_AFTER_SAMPLE_SEC
            ):
                break
            if now >= next_poll:
                _poll(dev)
                next_poll = now + max(0.4, poll_gap)
            raw = _read_raw(dev, timeout_ms=100)
            if raw is None:
                continue
            for frame in _extract_frames(raw):
                if only_light is not None and not _match_light(frame, only_light):
                    continue
                key = _addr_tuple(frame)
                if key is None:
                    continue
                light = index.get(key)
                if light is None:
                    continue
                level = _level_from_frame(frame)
                # Prefer status/live frames that decode; still record None skips
                # only when we got a numeric level.
                if level is None:
                    continue
                name = light["name"]
                by_name.setdefault(name, []).append(level)
                if only_name and name == only_name and first_sample_mono is None:
                    first_sample_mono = time.monotonic()
                    # First matching frame: wall LED proves announce is immediate.
                    if _READBACK_AFTER_SAMPLE_SEC <= 0:
                        return by_name
                if exit_when_changed_from:
                    prev = exit_when_changed_from.get(name)
                    if prev is not None and int(prev) != int(level):
                        return by_name
    return by_name


def read_light_level(
    light_name: str,
    *,
    wait_sec: float = _READBACK_WAIT_SEC,
    settle_sec: float = _READBACK_SETTLE_SEC,
) -> Optional[int]:
    """Read one light's reported level (first matching frame; short USB hold)."""
    samples_map = _collect_levels(
        wait_sec,
        settle_sec=settle_sec,
        poll_gap=_READBACK_POLL_GAP,
        only_name=light_name,
        abort_on_command=False,
    )
    return settle_reported_level(samples_map.get(light_name, []))


def poll_bus_levels(
    *,
    wait_sec: float = _BUS_POLL_WAIT_SEC,
    force: bool = False,
) -> Dict[str, int]:
    """Poll the bus once and return {light_name: settled_level}.

    Results are cached briefly so concurrent entity ``async_update`` calls share
    one USB session. After a local SET, returns cache without opening USB so
    turn_on/off are not serialized behind a status drain.
    """
    global _CACHE, _CACHE_MONO
    with _CACHE_LOCK:
        now = time.monotonic()
        if not force and _CACHE and (now - _CACHE_MONO) < _CACHE_TTL_SEC:
            return dict(_CACHE)

    # Yield to commands: wall LED proves SET already took effect.
    if not force and command_activity_recent():
        with _CACHE_LOCK:
            return dict(_CACHE)

    # Single-flight: one USB poll at a time; waiters reuse the fresh cache.
    with _POLL_IN_FLIGHT:
        with _CACHE_LOCK:
            now = time.monotonic()
            if not force and _CACHE and (now - _CACHE_MONO) < _CACHE_TTL_SEC:
                return dict(_CACHE)
        if not force and command_activity_recent():
            with _CACHE_LOCK:
                return dict(_CACHE)
        try:
            samples_map = _collect_levels(
                wait_sec,
                settle_sec=0.0,
                poll_gap=_BUS_POLL_GAP,
                abort_on_command=True,
            )
        except Exception as err:  # noqa: BLE001 — keep entities available
            _LOGGER.warning("Bus status poll failed: %s", err)
            with _CACHE_LOCK:
                return dict(_CACHE)
        settled: Dict[str, int] = {}
        for name, samples in samples_map.items():
            level = settle_reported_level(samples)
            if level is not None:
                settled[name] = level
        with _CACHE_LOCK:
            if settled:
                _CACHE = settled
                _CACHE_MONO = time.monotonic()
            return dict(_CACHE)


def listen_bus_once(
    *,
    wait_sec: float = _LISTEN_WAIT_SEC,
) -> Dict[str, int]:
    """One short listener burst; always samples USB unless a SET is in flight.

    Used by the shared continuous listener. Aborts mid-drain if
    ``mark_command_activity`` fires so HA toggles stay fast. Returns early
    when a level differs from the cache (wall-button → HA without waiting out
    the full burst).
    """
    global _CACHE, _CACHE_MONO
    if command_activity_recent():
        with _CACHE_LOCK:
            return dict(_CACHE)

    with _POLL_IN_FLIGHT:
        if command_activity_recent():
            with _CACHE_LOCK:
                return dict(_CACHE)
        with _CACHE_LOCK:
            baseline = dict(_CACHE)
        try:
            samples_map = _collect_levels(
                wait_sec,
                settle_sec=0.0,
                poll_gap=_LISTEN_POLL_GAP,
                abort_on_command=True,
                exit_when_changed_from=baseline or None,
            )
        except Exception as err:  # noqa: BLE001 — keep entities available
            _LOGGER.debug("Bus status listen failed: %s", err)
            with _CACHE_LOCK:
                return dict(_CACHE)
        settled: Dict[str, int] = {}
        for name, samples in samples_map.items():
            level = settle_reported_level(samples)
            if level is not None:
                settled[name] = level
        with _CACHE_LOCK:
            if settled:
                # Merge so lights that did not report this burst keep last known.
                _CACHE.update(settled)
                _CACHE_MONO = time.monotonic()
            return dict(_CACHE)


def invalidate_status_cache() -> None:
    """Drop the shared poll cache (e.g. after a local SET + dedicated readback)."""
    global _CACHE, _CACHE_MONO
    with _CACHE_LOCK:
        _CACHE = {}
        _CACHE_MONO = 0.0


def listen_idle_sec() -> float:
    """Idle gap between listener bursts (command priority window)."""
    return _LISTEN_IDLE_SEC


# Re-export for tests / callers that map HA brightness back to percent.
__all__ = [
    "brightness_to_level",
    "command_activity_recent",
    "invalidate_status_cache",
    "level_to_brightness",
    "level_to_entity_state",
    "listen_bus_once",
    "listen_idle_sec",
    "mark_command_activity",
    "poll_bus_levels",
    "read_light_level",
    "settle_reported_level",
]
