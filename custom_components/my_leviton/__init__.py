from __future__ import annotations

import logging
from typing import Callable, List

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, OPTIONS_DIM_START, PLATFORMS, UNIQUE_ID
from .entity_cleanup import (
    async_cleanup_duplicate_lights,
    async_remove_extra_config_entries,
)
from .utils.commands import send_light_command

_LOGGER = logging.getLogger(__name__)

DEFAULT_LISTEN_LIGHT = "Työhuone katto"
SERVICE_LISTEN_DIMMER = "listen_dimmer"
SERVICE_DUMP_BUS = "dump_bus"
SERVICE_PROBE_LEVEL = "probe_level"


def dim_start_for(hass: HomeAssistant, light_name: str) -> int:
    """Return configured hardware dim-start (0-100) for a light, or 0."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        raw = entry.options.get(OPTIONS_DIM_START, {}).get(light_name, 0)
        try:
            return max(0, min(100, int(raw)))
        except (TypeError, ValueError):
            return 0
    return 0


def _write_capture(hass: HomeAssistant, filename: str, lines: List[str]) -> str:
    path = hass.config.path(filename)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


async def _run_capture(
    hass: HomeAssistant,
    filename: str,
    runner: Callable[[], List[str]],
    label: str,
) -> None:
    def _job() -> tuple[str, list[str]]:
        lines = runner()
        path = _write_capture(hass, filename, lines)
        return path, lines

    try:
        path, lines = await hass.async_add_executor_job(_job)
    except Exception as err:  # noqa: BLE001 — surface to HA log for the user
        _LOGGER.exception("%s failed: %s", label, err)
        return

    _LOGGER.warning(
        "%s finished (%s events). Capture file: %s",
        label,
        len(lines),
        path,
    )
    for line in lines:
        _LOGGER.warning("CAPTURE %s", line)


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

        def _run() -> list[str]:
            from .utils.sniff import listen_dimmer

            return listen_dimmer(light, duration)

        await _run_capture(
            hass, "my_leviton_capture.log", _run, SERVICE_LISTEN_DIMMER
        )

    async def async_dump_bus(call: ServiceCall) -> None:
        """Poll and log every framed USB packet (no light filter)."""
        duration = float(call.data.get("duration", 30))

        def _run() -> list[str]:
            from .utils.sniff import dump_bus

            return dump_bus(duration)

        await _run_capture(hass, "my_leviton_bus_dump.log", _run, SERVICE_DUMP_BUS)

    async def async_probe_level(call: ServiceCall) -> None:
        """Send SET level(s) and log what status the dimmer reports back."""
        light = call.data.get("light", DEFAULT_LISTEN_LIGHT)
        sweep = bool(call.data.get("sweep", False))
        level = call.data.get("level")
        step = int(call.data.get("step", 5))
        fade = call.data.get("fade")
        arg = call.data.get("arg")
        level_in_arg = bool(call.data.get("level_in_arg", False))
        settle = float(call.data.get("settle", 0.8))

        def _run() -> list[str]:
            from .utils.sniff import probe_level

            return probe_level(
                light,
                level=None if level is None else int(level),
                sweep=sweep,
                step=step,
                arg=None if arg is None else int(arg),
                fade=None if fade is None else int(fade),
                level_in_arg=level_in_arg,
                settle_sec=settle,
            )

        await _run_capture(
            hass, "my_leviton_probe.log", _run, SERVICE_PROBE_LEVEL
        )

    hass.services.async_register(DOMAIN, SERVICE_LISTEN_DIMMER, async_listen_dimmer)
    hass.services.async_register(DOMAIN, SERVICE_DUMP_BUS, async_dump_bus)
    hass.services.async_register(DOMAIN, SERVICE_PROBE_LEVEL, async_probe_level)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    for service in (SERVICE_LISTEN_DIMMER, SERVICE_DUMP_BUS, SERVICE_PROBE_LEVEL):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop("lights_added", None)
    return unload_ok


def turn_light_on(light_name: str) -> bool:
    return send_light_command(light_name, "on")


def turn_light_off(light_name: str) -> bool:
    return send_light_command(light_name, "off")


def turn_light_brightness(
    light_name: str, brightness: int, dim_start: int = 0
) -> bool:
    """Set dimmer brightness (Home Assistant 0-255 scale)."""
    from .utils.commands import send_light_brightness

    return send_light_brightness(light_name, brightness, dim_start=dim_start)
