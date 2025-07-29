# utils/locks.py

import threading

# Global lock for USB operations
USB_LOCK = None

def initialize_usb_lock():
    """
    Initialize and return a global threading lock for USB operations.

    Returns:
        threading.Lock: A lock object to ensure thread-safe USB access.
    """
    global USB_LOCK
    if USB_LOCK is None:
        USB_LOCK = threading.Lock()
    return USB_LOCK
