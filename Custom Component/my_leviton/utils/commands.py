# utils/commands.py

from .constants import VID, PID, LIGHTS, POLL_COMMAND
from .usb_utils import find_usb_device, reset_usb_device
import logging

_LOGGER = logging.getLogger(__name__)

def execute_command(light_name, command_type, lock):
    """
    Execute a USB command to control a specific light.

    Args:
        light_name (str): Name of the light to control.
        command_type (str): Command type, either 'on' or 'off'.
        lock (threading.Lock): Thread-safe lock to ensure USB operations are not concurrent.
    """
    with lock:
        # Locate the USB device
        device = find_usb_device(VID, PID)
        if not device:
            _LOGGER.error("Cannot execute command. USB device is not connected.")
            return

        # Locate the light in the LIGHTS configuration
        light = next((light for light in LIGHTS if light["name"] == light_name), None)
        if not light:
            _LOGGER.error("Light '%s' not found.", light_name)
            return

        # Retrieve the command for the given type
        command = light.get(command_type)
        if not command:
            _LOGGER.error("Invalid command type '%s' for light '%s'.", command_type, light_name)
            return

        # Execute the USB command
        try:
            device.write(1, command)
            _LOGGER.info("Executed '%s' command for '%s'.", command_type, light_name)
        except Exception as e:
            _LOGGER.error("Failed to execute command '%s' for light '%s': %s", command_type, light_name, e)
        finally:
            reset_usb_device(device)  # Ensure the device is reset after the operation
