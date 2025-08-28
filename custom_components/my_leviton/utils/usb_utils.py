# utils/usb_utils.py
import usb.core
import usb.util
import logging

_LOGGER = logging.getLogger(__name__)

def find_usb_device(vid: int, pid: int):
    """Locate and prepare the USB device by VID/PID."""
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if dev is None:
        _LOGGER.warning("USB device %04x:%04x not found.", vid, pid)
        return None

    try:
        # Detach kernel driver (Linux) if necessary
        try:
            if dev.is_kernel_driver_active(0):
                try:
                    dev.detach_kernel_driver(0)
                except Exception as e:
                    _LOGGER.debug("Detach kernel driver failed: %s", e)
        except Exception:
            # Not all backends/interfaces expose is_kernel_driver_active; ignore
            pass

        # Ensure configuration is set
        try:
            dev.set_configuration()
        except Exception as e:
            _LOGGER.debug("set_configuration not needed or failed: %s", e)
    except Exception as e:
        _LOGGER.debug("Device preparation minor issue: %s", e)

    return dev

def send_command_interrupt_out(dev, data: bytes, endpoint: int = 0x01, timeout_ms: int = 1000) -> bool:
    """Send bytes via interrupt/bulk OUT endpoint."""
    try:
        # Some devices expect fixed-size frames; if so, pad here:
        # data = data.ljust(64, b"\x00")
        written = dev.write(endpoint, data, timeout=timeout_ms)
        return written == len(data)
    except Exception as e:
        _LOGGER.error("USB write failed: %s", e)
        return False

# Alternative HID Set_Report (uncomment and use in commands.py if needed)
# def send_command_hid_set_report(dev, data: bytes, report_id: int = 0, timeout_ms: int = 1000) -> bool:
#     try:
#         bmRequestType = 0x21  # Host to device | Class | Interface
#         bRequest = 0x09       # SET_REPORT
#         wValue = (0x02 << 8) | (report_id & 0xFF)  # Output report
#         wIndex = 0  # Interface 0
#         dev.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, data, timeout=timeout_ms)
#         return True
#     except Exception as e:
#         _LOGGER.error("HID Set_Report failed: %s", e)
#         return False

def reset_usb_device(dev):
    """Safely reset the USB device and release resources."""
    try:
        if dev:
            dev.reset()
            usb.util.dispose_resources(dev)
    except Exception as e:
        _LOGGER.error("Failed to reset USB device: %s", e)
