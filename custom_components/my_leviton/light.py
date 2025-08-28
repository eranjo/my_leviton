# light.py
from homeassistant.components.light import LightEntity, ColorMode
from . import turn_light_on, turn_light_off

class LevitonLight(LightEntity):
    """Represents a Leviton light entity in Home Assistant."""
    def __init__(self, name: str):
        self._name = name
        self._is_on = False
        self._attr_name = name
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_color_mode = ColorMode.ONOFF
        self._attr_unique_id = f"my_leviton::{name}"

    @property
    def is_on(self):
        return self._is_on

    async def async_turn_on(self, **kwargs):
        ok = await self.hass.async_add_executor_job(turn_light_on, self._name)
        if ok:
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        ok = await self.hass.async_add_executor_job(turn_light_off, self._name)
        if ok:
            self._is_on = False
            self.async_write_ha_state()

    @property
    def device_info(self):
        return {
            "identifiers": {("my_leviton", "usb_controller")} ,
            "manufacturer": "Leviton",
            "name": "Leviton USB Lighting",
            "model": "USB Controller",
        }

async def async_setup_entry(hass, entry, async_add_entities):
    from .utils.constants import LIGHTS
    entities = [LevitonLight(light["name"]) for light in LIGHTS]
    async_add_entities(entities, update_before_add=False)

def setup_platform(hass, config, add_entities, discovery_info=None):
    from .utils.constants import LIGHTS
    lights = [LevitonLight(light["name"]) for light in LIGHTS]
    add_entities(lights)
