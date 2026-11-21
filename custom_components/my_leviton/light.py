# light.py
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Dict, List, Optional

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import slugify

from . import turn_light_brightness, turn_light_off, turn_light_on
from .const import DOMAIN
from .utils.status import (
    command_activity_recent,
    invalidate_status_cache,
    level_to_entity_state,
    listen_bus_once,
    listen_idle_sec,
    poll_bus_levels,
    read_light_level,
)

_LOGGER = logging.getLogger(__name__)

# Fallback shared bus poll if the continuous listener is stopped.
SCAN_INTERVAL = timedelta(seconds=30)

# After a local SET, ignore bus-poll / listener overwrites briefly (optimistic).
_LOCAL_CMD_GUARD_SEC = 2.0
# Quiet gap before background first-frame readback (lets fast toggles SET only).
_READBACK_QUIET_SEC = 0.45

_DATA_ENTITIES = "light_entities"
_DATA_LISTEN_TASK = "bus_listen_task"


class LevitonLight(LightEntity):
    """Represents a Leviton light entity in Home Assistant."""

    # Keep legacy entity_id form (light.bedroom_a_ceiling_dimmer), not
    # light.leviton_usb_lighting_bedroom_a_ceiling_dimmer.
    _attr_has_entity_name = False
    # Continuous shared listener is primary; HA poll is a slow safety net.
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
        # Coalesce post-SET readbacks so fast toggles aren't serialized behind
        # status drains (service call returns after SET + optimistic write only).
        self._cmd_gen = 0
        self._local_cmd_mono = 0.0
        self._readback_busy = False
        self._readback_pending = False
        self._readback_reject_off = False

    @property
    def is_on(self):
        return self._is_on

    @property
    def brightness(self):
        return self._brightness if self._dimmer else None

    def _apply_reported_level(self, level: int) -> bool:
        """Update cached HA state from hardware level. True if state changed."""
        is_on, brightness = level_to_entity_state(level, dimmer=self._dimmer)
        changed = is_on != self._is_on
        if self._dimmer:
            changed = changed or brightness != self._brightness
        self._is_on = is_on
        if self._dimmer:
            self._brightness = brightness
            if brightness and brightness > 0:
                self._last_brightness = brightness
        return changed

    def _note_local_command(self) -> None:
        self._cmd_gen += 1
        self._local_cmd_mono = time.monotonic()

    def _local_command_recent(self) -> bool:
        if self._local_cmd_mono <= 0:
            return False
        return (time.monotonic() - self._local_cmd_mono) < _LOCAL_CMD_GUARD_SEC

    def apply_bus_level(self, level: int) -> bool:
        """Apply a shared-listener level; write HA state if it changed.

        Returns True if HA state was written. Skips while a local SET is
        settling so optimistic UI is not clobbered.
        """
        if self._local_command_recent():
            return False
        if not self._apply_reported_level(level):
            return False
        self.async_write_ha_state()
        return True

    async def _async_readback(self, *, reject_off: bool = False, gen: int = -1) -> Optional[int]:
        """First-frame status sample; returns level or None.

        When ``reject_off`` is set (post-ON), a settled 0 is ignored so an
        OFF-echo cannot roll optimistic ON back to OFF. Shared poll reconciles
        later if hardware truly stayed off.
        """
        level = await self.hass.async_add_executor_job(read_light_level, self._name)
        invalidate_status_cache()
        if gen != self._cmd_gen:
            return level
        if level is None:
            return None
        if reject_off and level == 0:
            return level
        if self._apply_reported_level(level):
            self.async_write_ha_state()
        return level

    def _schedule_readback(self, *, reject_off: bool = False) -> None:
        """Debounced background readback; coalesce while one is in flight."""
        self._readback_reject_off = reject_off
        if self._readback_busy:
            self._readback_pending = True
            return
        self.hass.async_create_task(self._async_readback_loop())

    async def _async_readback_loop(self) -> None:
        """Wait for a quiet gap, then one first-frame read for the latest SET."""
        self._readback_busy = True
        try:
            while True:
                gen = self._cmd_gen
                reject_off = self._readback_reject_off
                self._readback_pending = False
                await asyncio.sleep(_READBACK_QUIET_SEC)
                if self._cmd_gen != gen or self._readback_pending:
                    # Another toggle during quiet — restart quiet for newest.
                    continue
                try:
                    await self._async_readback(reject_off=reject_off, gen=gen)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — never fail the UI path
                    _LOGGER.exception("Background readback failed for %s", self._name)
                if not self._readback_pending and self._cmd_gen == gen:
                    break
        finally:
            self._readback_busy = False

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
                # Optimistic immediately; hardware reconcile in background.
                self._note_local_command()
                self._is_on = brightness > 0
                self._brightness = brightness
                if brightness > 0:
                    self._last_brightness = brightness
                self.async_write_ha_state()
                self._schedule_readback(reject_off=brightness > 0)
            return

        ok = await self.hass.async_add_executor_job(turn_light_on, self._name)
        if ok:
            self._note_local_command()
            self._is_on = True
            if self._dimmer:
                # Plain ON restores last hardware level (ARG_ON_LAST).
                self._brightness = self._last_brightness or 255
            self.async_write_ha_state()
            self._schedule_readback(reject_off=True)

    async def async_turn_off(self, **kwargs):
        ok = await self.hass.async_add_executor_job(turn_light_off, self._name)
        if ok:
            self._note_local_command()
            if self._dimmer and self._brightness:
                self._last_brightness = self._brightness
            self._is_on = False
            if self._dimmer:
                self._brightness = 0
            self.async_write_ha_state()
            self._schedule_readback()

    async def async_update(self) -> None:
        """Fallback refresh from a shared bus poll (listener is primary)."""
        # Don't clobber optimistic UI while a local SET is settling.
        if self._local_command_recent() or command_activity_recent():
            return
        levels = await self.hass.async_add_executor_job(poll_bus_levels)
        if self._local_command_recent():
            return
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


async def _async_bus_listen_loop(hass, entities: List[LevitonLight]) -> None:
    """Shared short-burst USB status listener → push levels to all entities.

    Cycle ≈ listen_wait (~0.35s) + idle (~0.25s) so wall-button status frames
    surface in well under 1s, while yielding USB_LOCK for SETs between bursts
    and aborting mid-drain on ``mark_command_activity``.
    """
    idle = listen_idle_sec()
    # Let entities finish platform add before writing state.
    await asyncio.sleep(1.0)
    _LOGGER.warning(
        "Leviton bus status listener started (burst+idle≈%.2fs cycle)",
        0.55 + idle,
    )
    try:
        while True:
            if command_activity_recent():
                await asyncio.sleep(0.1)
                continue
            try:
                levels: Dict[str, int] = await hass.async_add_executor_job(
                    listen_bus_once
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — keep listening
                _LOGGER.exception("Bus status listener burst failed")
                await asyncio.sleep(idle)
                continue

            if command_activity_recent():
                await asyncio.sleep(0.1)
                continue

            for entity in entities:
                if not entity.hass:
                    continue
                level = levels.get(entity._name)
                if level is None:
                    continue
                try:
                    entity.apply_bus_level(level)
                except Exception:  # noqa: BLE001 — one bad entity must not stop loop
                    _LOGGER.exception(
                        "Failed applying bus level to %s", entity._name
                    )

            await asyncio.sleep(idle)
    except asyncio.CancelledError:
        _LOGGER.info("Leviton bus status listener stopped")
        raise


async def async_setup_entry(hass, entry, async_add_entities):
    from .utils.constants import LIGHTS

    # Guard against double registration within one hass instance.
    domain_data = hass.data.setdefault(DOMAIN, {})
    already = bool(domain_data.get("lights_added"))
    if already:
        entities = domain_data.get(_DATA_ENTITIES) or []
    else:
        domain_data["lights_added"] = True
        entities = [
            LevitonLight(light["name"], dimmer=bool(light.get("dimmer")))
            for light in LIGHTS
        ]
        async_add_entities(entities, update_before_add=False)
        domain_data[_DATA_ENTITIES] = entities

    # Cancel any leftover listener (reload), then start shared wall-sync loop.
    # Must be a *background* task (not async_create_task): an infinite listen
    # loop otherwise blocks HA bootstrap / startup wrap-up.
    prev = domain_data.pop(_DATA_LISTEN_TASK, None)
    if prev is not None and not prev.done():
        prev.cancel()

    if not entities:
        _LOGGER.error("No Leviton light entities available for bus listener")
        return

    def _start_listener() -> None:
        task = domain_data.get(_DATA_LISTEN_TASK)
        if task is not None and not task.done():
            return
        domain_data[_DATA_LISTEN_TASK] = hass.async_create_background_task(
            _async_bus_listen_loop(hass, entities),
            name="my_leviton_bus_status_listener",
        )

    if hass.state is CoreState.running:
        _start_listener()
    else:
        @callback
        def _on_started(_event) -> None:
            _start_listener()

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started)


async def async_unload_entry(hass, entry) -> bool:
    """Stop the shared listener when the light platform unloads."""
    domain_data = hass.data.get(DOMAIN) or {}
    task = domain_data.pop(_DATA_LISTEN_TASK, None)
    domain_data.pop(_DATA_ENTITIES, None)
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return True
