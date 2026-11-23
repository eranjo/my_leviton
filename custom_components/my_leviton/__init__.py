from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, OPTIONS_DIM_START, PLATFORMS, UNIQUE_ID
from .entity_cleanup import (
    async_cleanup_dim_start_numbers,
    async_cleanup_duplicate_lights,
    async_disable_example_lights,
    async_reenable_inventory_lights,
    async_remove_extra_config_entries,
)
from .utils.commands import send_light_command
from .utils import constants as inventory

_LOGGER = logging.getLogger(__name__)


def _default_listen_light() -> str:
    """Current private inventory dimmer, for service defaults."""
    if not inventory.using_private_inventory():
        return ""
    for light in inventory.LIGHTS:
        if light.get("dimmer") and isinstance(light.get("name"), str):
            return light["name"]
    if inventory.LIGHTS and isinstance(inventory.LIGHTS[0].get("name"), str):
        return inventory.LIGHTS[0]["name"]
    return ""


SERVICE_LISTEN_DIMMER = "listen_dimmer"
SERVICE_DUMP_BUS = "dump_bus"
SERVICE_PROBE_LEVEL = "probe_level"


def _write_capture(
    hass: HomeAssistant,
    filename: str,
    lines: List[str],
    *,
    append: bool = False,
) -> str:
    path = hass.config.path(filename)
    mode = "a" if append else "w"
    with open(path, mode, encoding="utf-8") as handle:
        if append and lines:
            handle.write("\n----- probe run -----\n")
        handle.write("\n".join(lines) + "\n")
    return path


async def _run_capture(
    hass: HomeAssistant,
    filename: str,
    runner: Callable[[], List[str]],
    label: str,
    *,
    append: bool = False,
) -> None:
    def _job() -> tuple[str, list[str]]:
        lines = runner()
        path = _write_capture(hass, filename, lines, append=append)
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


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry versions (additive; never renames entities)."""
    if entry.version < 2:
        # v2: discovery onboarding metadata may live in entry.data["discovery"].
        data = dict(entry.data)
        data.setdefault("discovery", {})
        hass.config_entries.async_update_entry(entry, data=data, version=2)
        _LOGGER.info("Migrated my_leviton config entry to version 2")
    return True


def _registry_light_rows(hass: HomeAssistant) -> list[dict]:
    """Entity-registry light rows for this integration. Names are not logged."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    rows: list[dict] = []
    for entry in registry.entities.values():
        rows.append(
            {
                "domain": entry.domain,
                "platform": entry.platform,
                "unique_id": entry.unique_id,
                "disabled_by": entry.disabled_by,
                "supported_features": getattr(entry, "supported_features", 0) or 0,
                "capabilities": getattr(entry, "capabilities", None),
            }
        )
    return rows


def _ensure_durable_inventory(hass: HomeAssistant) -> int:
    """Keep private inventory in the HA config dir and restore it if wiped.

    Returns how many registry names were written into a new config yaml.
    Does not overwrite a yaml that already has non-example lights, and never
    writes the public example table.
    """
    config_dir = Path(hass.config.config_dir)
    yaml_path = config_dir / "my_leviton_lights.yaml"
    inventory.reload_lights(config_dir)

    if inventory.INVENTORY_SOURCE == "constants_local" and not inventory.config_yaml_is_protected(
        yaml_path
    ):
        # Copy the surviving in-package file outside the HACS folder.
        inventory.write_private_yaml(yaml_path, inventory.LIGHTS)
        inventory.reload_lights(config_dir)

    if inventory.using_private_inventory():
        return 0

    restored = inventory.lights_from_registry_rows(_registry_light_rows(hass))
    if not restored:
        _LOGGER.error(
            "No private Leviton inventory and no previous my_leviton lights "
            "in the entity registry; not creating example entities"
        )
        return 0
    if inventory.config_yaml_is_protected(yaml_path):
        return 0
    if not inventory.write_private_yaml(yaml_path, restored):
        return 0
    inventory.reload_lights(config_dir)
    if not inventory.using_private_inventory():
        return 0
    _LOGGER.warning(
        "Restored %s Leviton light name(s) from previous entity-registry "
        "entries into the config inventory yaml. Bus addresses are not stored "
        "in the registry, so those rows have names only.",
        len(restored),
    )
    return len(restored)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up My Leviton from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Discovery does not run here. Inventory is config yaml, then
    # constants_local, then (only if both are missing) a registry restore.
    _ensure_durable_inventory(hass)

    # Older installs could create multiple config entries (flow had no guard).
    async_remove_extra_config_entries(hass, entry.entry_id)

    # Ensure the surviving entry has a stable unique_id for future aborts.
    if entry.unique_id != UNIQUE_ID:
        hass.config_entries.async_update_entry(entry, unique_id=UNIQUE_ID)

    # Example names must not drive registry edits. Re-enable only lights whose
    # unique_id is my_leviton::{name} for the private inventory.
    if inventory.using_private_inventory():
        reenabled = async_reenable_inventory_lights(hass)
        if reenabled:
            _LOGGER.warning(
                "Cleared disabled_by on %s previous my_leviton light(s)", reenabled
            )
        removed = async_cleanup_duplicate_lights(hass)
        if removed:
            _LOGGER.info("Removed %s duplicate Leviton light entit(y/ies)", removed)
    disabled_examples = async_disable_example_lights(hass)
    if disabled_examples:
        _LOGGER.warning(
            "Disabled %s public example my_leviton light(s)", disabled_examples
        )
    if not inventory.using_private_inventory():
        _LOGGER.error(
            "Skipping entity cleanup because the public example table is not "
            "a private inventory"
        )

    # 1.1.0b14: dim_start number entities / options are gone (coarse arg steps).
    n_dim = async_cleanup_dim_start_numbers(hass)
    if n_dim:
        _LOGGER.info("Removed %s legacy dim_start number entit(y/ies)", n_dim)
    if OPTIONS_DIM_START in entry.options:
        new_options = {
            key: value
            for key, value in entry.options.items()
            if key != OPTIONS_DIM_START
        }
        hass.config_entries.async_update_entry(entry, options=new_options)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def async_listen_dimmer(call: ServiceCall) -> None:
        """Capture USB frames while the physical dimmer is used."""
        light = call.data.get("light", _default_listen_light())
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
        light = call.data.get("light", _default_listen_light())
        sweep = bool(call.data.get("sweep", False))
        level = call.data.get("level")
        step = int(call.data.get("step", 5))
        fade = call.data.get("fade")
        arg = call.data.get("arg")
        level_in_arg = bool(call.data.get("level_in_arg", False))
        settle = float(call.data.get("settle", 0.5))
        wait = float(call.data.get("wait", 20.0))
        reset_before = bool(call.data.get("reset_before", True))

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
                wait_sec=wait,
                reset_before=reset_before,
            )

        # Append so multi-step scripts keep every probe in one file.
        await _run_capture(
            hass,
            "my_leviton_probe.log",
            _run,
            SERVICE_PROBE_LEVEL,
            append=True,
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
        domain_data = hass.data.get(DOMAIN, {})
        domain_data.pop("lights_added", None)
        # Listener task is cancelled in light.async_unload_entry; clear leftovers.
        domain_data.pop("bus_listen_task", None)
        domain_data.pop("light_entities", None)
    return unload_ok


def turn_light_on(light_name: str) -> bool:
    return send_light_command(light_name, "on")


def turn_light_off(light_name: str) -> bool:
    return send_light_command(light_name, "off")


def turn_light_brightness(
    light_name: str,
    brightness: int,
    previous_brightness: int | None = None,
) -> bool:
    """Set dimmer brightness (Home Assistant 0-255 scale)."""
    from .utils.commands import send_light_brightness

    return send_light_brightness(
        light_name,
        brightness,
        previous_brightness=previous_brightness,
    )
