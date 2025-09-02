import logging
from typing import Optional, List, Dict
from .usb_utils import send_bytes_once
from .constants import VID, PID, LIGHTS
from .locks import initialize_usb_lock

_LOGGER = logging.getLogger(__name__)
_USB_LOCK = initialize_usb_lock()

def lookup_light(name: str) -> Optional[Dict]:
    for l in LIGHTS:
        if l.get("name") == name:
            return l
    return None

def send_light_command(light_name: str, command_key: str) -> bool:
    """Send 'on' or 'off' command for a named light.
    Uses a global lock so multiple HA calls queue up instead of colliding.
    """
    light = lookup_light(light_name)
    if not light:
        _LOGGER.error("Unknown light: %s", light_name)
        return False

    cmd_list: List[int] = light.get(command_key)
    if not cmd_list:
        _LOGGER.error("Light '%s' does not define command '%s'", light_name, command_key)
        return False

    data = bytes(cmd_list)

    with _USB_LOCK:
        ok = send_bytes_once(VID, PID, data)
        if not ok:
            _LOGGER.error("Failed to send command '%s' to light '%s'", command_key, light_name)
        return ok
