import logging
from datetime import timedelta
from homeassistant.helpers.event import async_track_time_interval
from .utils.locks import initialize_usb_lock
from .utils.polling import poll_light_state
from .utils.commands import execute_command

_LOGGER = logging.getLogger(__name__)

# Initialize the USB lock for thread-safe operations
USB_LOCK = initialize_usb_lock()

# Polling interval (adjust as needed)
POLL_INTERVAL = timedelta(seconds=5)

def turn_light_on(light_name):
    """
    Turn on a light by name.

    Args:
        light_name (str): Name of the light to turn on.
    """
    execute_command(light_name, "on", USB_LOCK)

def turn_light_off(light_name):
    """
    Turn off a light by name.

    Args:
        light_name (str): Name of the light to turn off.
    """
    execute_command(light_name, "off", USB_LOCK)

def setup(hass, config):
    """
    Set up the integration and start polling for light states.

    Args:
        hass: Home Assistant instance.
        config: Configuration for the integration.
    """
    def poll_and_update(_):
        """
        Poll the light states and update entities in Home Assistant.
        """
        _LOGGER.debug("Polling light states to update Home Assistant entities.")
        poll_light_state(USB_LOCK, hass)

    # Schedule periodic polling
    async_track_time_interval(hass, poll_and_update, POLL_INTERVAL)

    _LOGGER.info("Leviton Lighting System setup complete.")
    return True
