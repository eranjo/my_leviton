"""Hardware status → HA entity state helpers and USB status reads.

LevitonLight historically was optimistic-only (SET then assume). This module
provides short post-SET readback and a shared bus poll so HA tracks reported
levels (including wall-button changes between polls).
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

# Post-SET: wait long enough for OFF-echo then mid settle; don't early-return.
_READBACK_SETTLE_SEC = 0.35
_READBACK_WAIT_SEC = 2.5
_READBACK_POLL_GAP = 1.5

# Periodic bus poll: one POLL, drain a few seconds, map all addresses.
_BUS_POLL_WAIT_SEC = 3.5
_BUS_POLL_GAP = 1.5


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
) -> Dict[str, List[Optional[int]]]:
    """Open USB once, POLL, and collect status/live levels per light name."""
    by_name: Dict[str, List[Optional[int]]] = {}
    index = _index_lights()
    only_light = lookup_light(only_name) if only_name else None
    if only_name and not only_light:
        _LOGGER.error("Unknown light for status read: %s", only_name)
        return by_name

    with _open_usb() as dev:
        if settle_sec > 0:
            time.sleep(settle_sec)
        deadline = time.monotonic() + max(0.5, wait_sec)
        next_poll = 0.0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_poll:
                _poll(dev)
                next_poll = now + max(1.0, poll_gap)
            raw = _read_raw(dev, timeout_ms=250)
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
    return by_name


def read_light_level(
    light_name: str,
    *,
    wait_sec: float = _READBACK_WAIT_SEC,
    settle_sec: float = _READBACK_SETTLE_SEC,
) -> Optional[int]:
    """Read one light's settled reported level (holds USB lock via _open_usb)."""
    samples_map = _collect_levels(
        wait_sec,
        settle_sec=settle_sec,
        poll_gap=_READBACK_POLL_GAP,
        only_name=light_name,
    )
    return settle_reported_level(samples_map.get(light_name, []))


def poll_bus_levels(
    *,
    wait_sec: float = _BUS_POLL_WAIT_SEC,
    force: bool = False,
) -> Dict[str, int]:
    """Poll the bus once and return {light_name: settled_level}.

    Results are cached briefly so concurrent entity ``async_update`` calls share
    one USB session.
    """
    global _CACHE, _CACHE_MONO
    with _CACHE_LOCK:
        now = time.monotonic()
        if not force and _CACHE and (now - _CACHE_MONO) < _CACHE_TTL_SEC:
            return dict(_CACHE)

    # Single-flight: one USB poll at a time; waiters reuse the fresh cache.
    with _POLL_IN_FLIGHT:
        with _CACHE_LOCK:
            now = time.monotonic()
            if not force and _CACHE and (now - _CACHE_MONO) < _CACHE_TTL_SEC:
                return dict(_CACHE)
        try:
            samples_map = _collect_levels(wait_sec, settle_sec=0.0, poll_gap=_BUS_POLL_GAP)
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


def invalidate_status_cache() -> None:
    """Drop the shared poll cache (e.g. after a local SET + dedicated readback)."""
    global _CACHE, _CACHE_MONO
    with _CACHE_LOCK:
        _CACHE = {}
        _CACHE_MONO = 0.0


# Re-export for tests / callers that map HA brightness back to percent.
__all__ = [
    "brightness_to_level",
    "invalidate_status_cache",
    "level_to_brightness",
    "level_to_entity_state",
    "poll_bus_levels",
    "read_light_level",
    "settle_reported_level",
]
