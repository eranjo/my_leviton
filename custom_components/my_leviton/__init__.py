from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, PLATFORMS, UNIQUE_ID
from .entity_cleanup import (
    async_cleanup_duplicate_lights,
    async_remove_extra_config_entries,
)
from .utils.commands import send_light_command

_LOGGER = logging.getLogger(__name__)

DEFAULT_LISTEN_LIGHT = "Työhuone katto"
SERVICE_LISTEN_DIMMER = "listen_dimmer"


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

    async def async_listen_dimmer(call: ServiceCall) -> None:
        """Capture USB frames while the physical dimmer is used."""
        light = call.data.get("light", DEFAULT_LISTEN_LIGHT)
        duration = float(call.data.get("duration", 90))

        def _run() -> tuple[str, list[str]]:
            from .utils.sniff import listen_dimmer

            lines = listen_dimmer(light, duration)
            path = hass.config.path("my_leviton_capture.log")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            return path, lines

        try:
            path, lines = await hass.async_add_executor_job(_run)
        except Exception as err:  # noqa: BLE001 — surface to HA log for the user
            _LOGGER.exception("listen_dimmer failed: %s", err)
            return

        _LOGGER.warning(
            "listen_dimmer finished (%s events). Capture file: %s",
            len(lines),
            path,
        )
        # Also dump into the log so it can be copied from Settings → Logs.
        for line in lines:
            _LOGGER.warning("CAPTURE %s", line)

    hass.services.async_register(DOMAIN, SERVICE_LISTEN_DIMMER, async_listen_dimmer)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if hass.services.has_service(DOMAIN, SERVICE_LISTEN_DIMMER):
        hass.services.async_remove(DOMAIN, SERVICE_LISTEN_DIMMER)
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
