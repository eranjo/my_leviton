import logging
import time
from typing import Optional, Dict

from .usb_utils import send_bytes_once
from .constants import VID, PID, LIGHTS
from .locks import initialize_usb_lock
from .protocol import (
    ARG_OFF,
    ARG_ON,
    ARG_ON_LAST,
    FADE_LEVEL,
    FADE_ON_OFF,
    build_set_command,
    build_set_level_command,
    brightness_to_level,
    needs_off_before_level,
)
from .status import mark_command_activity

_LOGGER = logging.getLogger(__name__)
_USB_LOCK = initialize_usb_lock()

# Brief settle after forced OFF so a following mid-level SET is accepted
# (96→6 stayed at 100 without this; see needs_off_before_level).
_OFF_BEFORE_SET_SEC = 0.15


def lookup_light(name: str) -> Optional[Dict]:
    for light in LIGHTS:
        if light.get("name") == name:
            return light
    return None


def _send_arg(light: Dict, arg: int, *, fade: int = FADE_ON_OFF) -> bool:
    data = build_set_command(
        light["type"],
        light["channel"],
        light["device_id"],
        arg,
        fade=fade,
    )
    mark_command_activity()
    with _USB_LOCK:
        ok = send_bytes_once(VID, PID, data)
        if not ok:
            _LOGGER.error(
                "Failed to send arg=0x%02x fade=0x%02x to light '%s'",
                arg,
                fade,
                light.get("name"),
            )
        return ok


def send_light_command(light_name: str, command_key: str) -> bool:
    """Send 'on' or 'off' for a named light."""
    light = lookup_light(light_name)
    if not light:
        _LOGGER.error("Unknown light: %s", light_name)
        return False

    if command_key == "on":
        # Dimmers: 0x0A restores last level (wall-button tap ON).
        # Relays (0x26): 0x0A is ignored — must use ARG_ON (0x00). b16
        # used ON_LAST for every light; post-SET readback then saw level=0
        # and rolled the optimistic HA state back to OFF (Entry Switch repro).
        if light.get("dimmer"):
            arg = ARG_ON_LAST
        else:
            arg = ARG_ON
    elif command_key == "off":
        arg = ARG_OFF
    else:
        _LOGGER.error("Light '%s' unsupported command '%s'", light_name, command_key)
        return False

    return _send_arg(light, arg, fade=FADE_ON_OFF)


def send_light_level(
    light_name: str,
    level: int,
    *,
    previous_level: Optional[int] = None,
) -> bool:
    """Send absolute dimmer level 0-100 for a named light.

    Uses VRUSB arg encoding (``level_to_dim_arg``). When leaving full/near-full
    for a mid level, sends OFF first — bare mid args are ignored after ARG_ON.
    """
    light = lookup_light(light_name)
    if not light:
        _LOGGER.error("Unknown light: %s", light_name)
        return False
    if not light.get("dimmer"):
        _LOGGER.error("Light '%s' is not a dimmer", light_name)
        return False

    target = max(0, min(100, int(level)))
    if needs_off_before_level(previous_level, target):
        off_pkt = build_set_command(
            light["type"],
            light["channel"],
            light["device_id"],
            ARG_OFF,
            fade=FADE_LEVEL,
            level=0,
        )
        mark_command_activity()
        with _USB_LOCK:
            ok = send_bytes_once(VID, PID, off_pkt)
        if not ok:
            _LOGGER.error(
                "Failed OFF-before-SET for light '%s' (prev=%s target=%s)",
                light.get("name"),
                previous_level,
                target,
            )
            return False
        time.sleep(_OFF_BEFORE_SET_SEC)

    data = build_set_level_command(
        light["type"],
        light["channel"],
        light["device_id"],
        target,
        fade=FADE_LEVEL,
    )
    mark_command_activity()
    with _USB_LOCK:
        ok = send_bytes_once(VID, PID, data)
        if not ok:
            _LOGGER.error(
                "Failed to send level=%s to light '%s'", target, light.get("name")
            )
        return ok


def send_light_brightness(
    light_name: str,
    brightness: int,
    *,
    previous_brightness: Optional[int] = None,
) -> bool:
    """Send Home Assistant brightness 0-255 for a dimmer."""
    prev_level = (
        None
        if previous_brightness is None
        else brightness_to_level(previous_brightness)
    )
    return send_light_level(
        light_name,
        brightness_to_level(brightness),
        previous_level=prev_level,
    )
