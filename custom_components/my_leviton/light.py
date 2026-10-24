# light.py
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity

from . import turn_light_brightness, turn_light_off, turn_light_on


class LevitonLight(LightEntity):
    """Represents a Leviton light entity in Home Assistant."""

    def __init__(self, name: str, *, dimmer: bool = False):
        self._name = name
        self._is_on = False
        self._brightness = 255 if dimmer else None
        self._dimmer = dimmer
        self._attr_name = name
        self._attr_unique_id = f"my_leviton::{name}"
        if dimmer:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF

    @property
    def is_on(self):
        return self._is_on

    @property
    def brightness(self):
        return self._brightness if self._dimmer else None

    async def async_turn_on(self, **kwargs):
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if self._dimmer and brightness is not None:
            ok = await self.hass.async_add_executor_job(
                turn_light_brightness, self._name, brightness
            )
            if ok:
                self._is_on = brightness > 0
                self._brightness = brightness
                self.async_write_ha_state()
            return

        ok = await self.hass.async_add_executor_job(turn_light_on, self._name)
        if ok:
            self._is_on = True
            if self._dimmer:
                # Plain ON uses ARG_ON (0x00); treat as full for UI.
                self._brightness = 255
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        ok = await self.hass.async_add_executor_job(turn_light_off, self._name)
        if ok:
            self._is_on = False
            if self._dimmer:
                self._brightness = 0
            self.async_write_ha_state()

    @property
    def device_info(self):
        return {
            "identifiers": {("my_leviton", "usb_controller")},
            "manufacturer": "Leviton",
            "name": "Leviton USB Lighting",
            "model": "USB Controller",
        }


async def async_setup_entry(hass, entry, async_add_entities):
    from .utils.constants import LIGHTS

    entities = [
        LevitonLight(light["name"], dimmer=bool(light.get("dimmer")))
        for light in LIGHTS
    ]
    async_add_entities(entities, update_before_add=False)


def setup_platform(hass, config, add_entities, discovery_info=None):
    from .utils.constants import LIGHTS

    lights = [
        LevitonLight(light["name"], dimmer=bool(light.get("dimmer")))
        for light in LIGHTS
    ]
    add_entities(lights)
