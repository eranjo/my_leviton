import logging
from typing import Optional, Dict

from .usb_utils import send_bytes_once
from .constants import VID, PID, LIGHTS
from .locks import initialize_usb_lock
from .protocol import (
    ARG_OFF,
    ARG_ON,
    build_set_command,
    brightness_to_level,
    level_to_arg,
)

_LOGGER = logging.getLogger(__name__)
_USB_LOCK = initialize_usb_lock()


def lookup_light(name: str) -> Optional[Dict]:
    for light in LIGHTS:
        if light.get("name") == name:
            return light
    return None


def _send_arg(light: Dict, arg: int) -> bool:
    data = build_set_command(
        light["type"],
        light["channel"],
        light["device_id"],
        arg,
    )
    with _USB_LOCK:
        ok = send_bytes_once(VID, PID, data)
        if not ok:
            _LOGGER.error(
                "Failed to send arg=0x%02x to light '%s'", arg, light.get("name")
            )
        return ok


def send_light_command(light_name: str, command_key: str) -> bool:
    """Send 'on' or 'off' for a named light."""
    light = lookup_light(light_name)
    if not light:
        _LOGGER.error("Unknown light: %s", light_name)
        return False

    if command_key == "on":
        arg = ARG_ON
    elif command_key == "off":
        arg = ARG_OFF
    else:
        _LOGGER.error("Light '%s' unsupported command '%s'", light_name, command_key)
        return False

    return _send_arg(light, arg)


def send_light_level(light_name: str, level: int) -> bool:
    """Send absolute dimmer level 0-100 for a named light."""
    light = lookup_light(light_name)
    if not light:
        _LOGGER.error("Unknown light: %s", light_name)
        return False
    if not light.get("dimmer"):
        _LOGGER.error("Light '%s' is not a dimmer", light_name)
        return False

    return _send_arg(light, level_to_arg(level))


def send_light_brightness(light_name: str, brightness: int) -> bool:
    """Send Home Assistant brightness 0-255 for a dimmer."""
    return send_light_level(light_name, brightness_to_level(brightness))
