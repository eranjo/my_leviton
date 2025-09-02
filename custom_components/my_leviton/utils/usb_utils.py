import usb.core
import usb.util
import logging

_LOGGER = logging.getLogger(__name__)

def send_bytes_once(vid: int, pid: int, data: bytes, endpoint: int = 0x01, timeout_ms: int = 1000) -> bool:
    """Find device, write once, then dispose resources.
    Stateless per call so the OS never sees the device as 'busy' between commands.
    """
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if dev is None:
        _LOGGER.error("USB device %04x:%04x not found", vid, pid)
        return False

    try:
        written = dev.write(endpoint, data, timeout=timeout_ms)
        return written == len(data)
    except Exception as e:
        _LOGGER.error("USB write failed: %s", e)
        return False
    finally:
        try:
            usb.util.dispose_resources(dev)
        except Exception as e:
            _LOGGER.debug("dispose_resources issue: %s", e)


def reset_usb_device(dev):
    """Optional helper to reset + release."""
    try:
        if dev:
            dev.reset()
            usb.util.dispose_resources(dev)
    except Exception as e:
        _LOGGER.error("Failed to reset USB device: %s", e)
