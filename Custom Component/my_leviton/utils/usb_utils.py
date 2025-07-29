# utils/usb_utils.py

import usb.core
import usb.util
import logging

# Set up a logger for USB-related operations
_LOGGER = logging.getLogger(__name__)

def find_usb_device(vid, pid):
    """
    Locate the USB device using the provided Vendor ID and Product ID.
    
    Args:
        vid (int): Vendor ID of the USB device.
        pid (int): Product ID of the USB device.

    Returns:
        device (usb.core.Device or None): Found USB device, or None if not found.
    """
    device = usb.core.find(idVendor=vid, idProduct=pid)
    if device is None:
        _LOGGER.warning("USB device not found.")
    return device


def reset_usb_device(device):
    """
    Safely reset the USB device and release its resources.
    
    Args:
        device (usb.core.Device): The USB device to reset.
    """
    try:
        if device:
            device.reset()
            usb.util.dispose_resources(device)  # Free up the device's resources
    except Exception as e:
        _LOGGER.error("Failed to reset USB device: %s", e)
