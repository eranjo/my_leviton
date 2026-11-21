# light.py
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import slugify

from . import turn_light_brightness, turn_light_off, turn_light_on
from .const import DOMAIN
from .utils.status import (
    invalidate_status_cache,
    level_to_entity_state,
    poll_bus_levels,
    read_light_level,
)

# Shared bus poll (~one USB session / interval via status cache).
SCAN_INTERVAL = timedelta(seconds=30)


class LevitonLight(LightEntity):
    """Represents a Leviton light entity in Home Assistant."""

    # Keep legacy entity_id form (light.bedroom_a_ceiling_dimmer), not
    # light.leviton_usb_lighting_bedroom_a_ceiling_dimmer.
    _attr_has_entity_name = False
    _attr_should_poll = True

    def __init__(self, name: str, *, dimmer: bool = False):
        self._name = name
        self._is_on = False
        self._brightness = 255 if dimmer else None
        # Remember last non-zero brightness for last-level ON (wall-button tap).
        self._last_brightness = 255 if dimmer else None
        self._dimmer = dimmer
        self._attr_name = name
        self._attr_unique_id = f"my_leviton::{name}"
        # suggested_object_id is NOT device-prefixed by HA — keeps short IDs.
        self._attr_suggested_object_id = slugify(name)
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

    def _apply_reported_level(self, level: int) -> None:
        """Update cached HA state from a hardware-reported percent level."""
        is_on, brightness = level_to_entity_state(level, dimmer=self._dimmer)
        self._is_on = is_on
        if self._dimmer:
            self._brightness = brightness
            if brightness and brightness > 0:
                self._last_brightness = brightness

    async def _async_readback(self) -> Optional[int]:
        """Short post-SET status drain; returns settled level or None."""
        level = await self.hass.async_add_executor_job(read_light_level, self._name)
        invalidate_status_cache()
        if level is not None:
            self._apply_reported_level(level)
            self.async_write_ha_state()
        return level

    async def async_turn_on(self, **kwargs):
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if self._dimmer and brightness is not None:
            # Pass prior UI brightness so full→mid jumps can OFF-then-SET
            # (bare mid args are ignored after ARG_ON — see needs_off_before_level).
            prev = self._brightness
            ok = await self.hass.async_add_executor_job(
                turn_light_brightness, self._name, brightness, prev
            )
            if ok:
                # Optimistic until readback; then trust hardware.
                self._is_on = brightness > 0
                self._brightness = brightness
                if brightness > 0:
                    self._last_brightness = brightness
                self.async_write_ha_state()
                await self._async_readback()
            return

        ok = await self.hass.async_add_executor_job(turn_light_on, self._name)
        if ok:
            self._is_on = True
            if self._dimmer:
                # Plain ON restores last hardware level (ARG_ON_LAST).
                self._brightness = self._last_brightness or 255
            self.async_write_ha_state()
            await self._async_readback()

    async def async_turn_off(self, **kwargs):
        ok = await self.hass.async_add_executor_job(turn_light_off, self._name)
        if ok:
            if self._dimmer and self._brightness:
                self._last_brightness = self._brightness
            self._is_on = False
            if self._dimmer:
                self._brightness = 0
            self.async_write_ha_state()
            await self._async_readback()

    async def async_update(self) -> None:
        """Refresh from a shared bus poll (wall-button / other controllers)."""
        levels = await self.hass.async_add_executor_job(poll_bus_levels)
        level = levels.get(self._name)
        if level is None:
            return
        self._apply_reported_level(level)

    @property
    def device_info(self) -> DeviceInfo:
        # Identifier must stay ("my_leviton", "usb_controller") so we do not
        # create a second device next to existing installs.
        return DeviceInfo(
            identifiers={(DOMAIN, "usb_controller")},
            manufacturer="Leviton",
            name="Leviton USB Lighting",
            model="USB Controller",
        )


async def async_setup_entry(hass, entry, async_add_entities):
    from .utils.constants import LIGHTS

    # Guard against double registration within one hass instance.
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("lights_added"):
        return
    domain_data["lights_added"] = True

    entities = [
        LevitonLight(light["name"], dimmer=bool(light.get("dimmer")))
        for light in LIGHTS
    ]
    async_add_entities(entities, update_before_add=False)
