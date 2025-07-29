# light.py

from homeassistant.components.light import LightEntity, ColorMode
from . import turn_light_on, turn_light_off

class LevitonLight(LightEntity):
    """
    Represents a Leviton light entity in Home Assistant.
    
    Attributes:
        _name (str): Name of the light.
        _is_on (bool): Current state of the light (True for on, False for off).
    """
    def __init__(self, name):
        """
        Initialize the Leviton light entity.
        
        Args:
            name (str): Name of the light.
        """
        self._name = name
        self._is_on = False

    @property
    def name(self):
        """Return the name of the light."""
        return self._name

    @property
    def is_on(self):
        """Return whether the light is currently on."""
        return self._is_on

    @property
    def supported_color_modes(self):
        """Return the supported color modes for the light."""
        return {ColorMode.ONOFF}

    @property
    def color_mode(self):
        """Return the current color mode of the light."""
        return ColorMode.ONOFF

    @property
    def unique_id(self):
        """
        Return the unique ID for this light entity.

        Combines the integration name and light name to ensure uniqueness.
        """
        return f"my_leviton_{self._name.replace(' ', '_').lower()}"

    def turn_on(self, **kwargs):
        """
        Turn the light on.
        
        This sends the 'on' command to the USB device via turn_light_on.
        """
        turn_light_on(self._name)
        self._is_on = True

    def turn_off(self, **kwargs):
        """
        Turn the light off.
        
        This sends the 'off' command to the USB device via turn_light_off.
        """
        turn_light_off(self._name)
        self._is_on = False


def setup_platform(hass, config, add_entities, discovery_info=None):
    """
    Set up the Leviton light platform in Home Assistant.
    
    Args:
        hass (HomeAssistant): The Home Assistant instance.
        config (dict): The configuration for the platform.
        add_entities (function): Function to add entities to Home Assistant.
        discovery_info (dict or None): Discovery information, if available.
    """
    from .utils.constants import LIGHTS  # Import light definitions
    lights = [LevitonLight(light["name"]) for light in LIGHTS]
    add_entities(lights)