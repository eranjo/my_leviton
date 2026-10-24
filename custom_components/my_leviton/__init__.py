from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS, UNIQUE_ID
from .entity_cleanup import (
    async_cleanup_duplicate_lights,
    async_remove_extra_config_entries,
)
from .utils.commands import send_light_command

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """YAML setup (legacy) — lights are only created via config entries."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up My Leviton from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Older installs could create multiple config entries (flow had no guard).
    async_remove_extra_config_entries(hass, entry.entry_id)

    # Ensure the surviving entry has a stable unique_id for future aborts.
    if entry.unique_id != UNIQUE_ID:
        hass.config_entries.async_update_entry(entry, unique_id=UNIQUE_ID)

    removed = async_cleanup_duplicate_lights(hass)
    if removed:
        _LOGGER.info("Removed %s duplicate Leviton light entit(y/ies)", removed)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop("lights_added", None)
    return unload_ok


def turn_light_on(light_name: str) -> bool:
    return send_light_command(light_name, "on")


def turn_light_off(light_name: str) -> bool:
    return send_light_command(light_name, "off")


def turn_light_brightness(light_name: str, brightness: int) -> bool:
    """Set dimmer brightness (Home Assistant 0-255 scale)."""
    from .utils.commands import send_light_brightness

    return send_light_brightness(light_name, brightness)
