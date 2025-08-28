# utils/commands.py
import logging
from typing import Optional, List, Dict
from .usb_utils import find_usb_device, send_command_interrupt_out
from .constants import VID, PID, LIGHTS

_LOGGER = logging.getLogger(__name__)

def lookup_light(name: str) -> Optional[Dict]:
    for l in LIGHTS:
        if l.get("name") == name:
            return l
    return None

def send_light_command(light_name: str, command_key: str) -> bool:
    """Send 'on' or 'off' command for a named light."""
    light = lookup_light(light_name)
    if not light:
        _LOGGER.error("Unknown light: %s", light_name)
        return False

    cmd_list: List[int] = light.get(command_key)
    if not cmd_list:
        _LOGGER.error("Light '%s' does not define command '%s'", light_name, command_key)
        return False

    dev = find_usb_device(VID, PID)
    if dev is None:
        return False

    data = bytes(cmd_list)
    ok = send_command_interrupt_out(dev, data)
    if not ok:
        _LOGGER.error("Failed to send command '%s' to light '%s'", command_key, light_name)
    return ok
