"""Keep existing light entity_ids stable across HACS updates."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import DEVICE_ENTITY_ID_PREFIX, DOMAIN
from .utils import constants as inventory
from .utils.constants import address_unique_id, legacy_name_unique_id

_LOGGER = logging.getLogger(__name__)

# Set by Home Assistant when an integration stops providing an entity.
_INTEGRATION_DISABLED = "integration"


def _is_our_light(entry: object) -> bool:
    entity_id = getattr(entry, "entity_id", "") or ""
    if not entity_id.startswith("light."):
        return False
    domain = getattr(entry, "domain", None)
    if domain not in (None, "light"):
        return False
    return getattr(entry, "platform", None) == DOMAIN


def _our_lights(registry) -> list:
    return [entry for entry in registry.entities.values() if _is_our_light(entry)]


def _prefer_existing_entity_id(entries: list):
    """Prefer the short entity_id automations already use."""

    def rank(entry) -> tuple[int, str]:
        long_form = entry.entity_id.startswith(DEVICE_ENTITY_ID_PREFIX)
        return (1 if long_form else 0, entry.entity_id)

    return sorted(entries, key=rank)[0]


def _entries_with_unique_id(registry, unique_id: str) -> list:
    return [
        entry
        for entry in _our_lights(registry)
        if entry.unique_id == unique_id
    ]


@callback
def async_has_light_entries(hass: HomeAssistant) -> bool:
    """True when this integration already has light rows in the registry."""
    registry = er.async_get(hass)
    return any(_is_our_light(entry) for entry in registry.entities.values())


@callback
def async_migrate_light_unique_ids(hass: HomeAssistant) -> int:
    """Point legacy name unique_ids at the bus address. Leave entity_id as-is.

    For each private inventory light, if the registry has ``my_leviton::{name}``,
    that entry's unique_id becomes ``my_leviton:{type:04x}:{channel:02x}:{device_id:04x}``.
    ``new_entity_id`` is never passed.

    If the address unique_id is already on another entry, this does not delete
    or disable either row. A device-prefixed holder is parked so the existing
    short entity_id can take the address; cleanup may then drop that duplicate.
    """
    if not inventory.using_private_inventory():
        return 0
    registry = er.async_get(hass)
    migrated = 0
    for light in inventory.LIGHTS:
        name = light.get("name") if isinstance(light, dict) else None
        if not isinstance(name, str) or not name:
            continue
        new_uid = address_unique_id(light)
        if not new_uid:
            _LOGGER.error(
                "Inventory light has no bus address; unique_id left unchanged"
            )
            continue
        old_uid = legacy_name_unique_id(name)
        legacy_list = _entries_with_unique_id(registry, old_uid)
        if not legacy_list:
            continue
        legacy = _prefer_existing_entity_id(legacy_list)
        holders = [
            entry
            for entry in _entries_with_unique_id(registry, new_uid)
            if entry.entity_id != legacy.entity_id
        ]
        if holders:
            other = _prefer_existing_entity_id(holders)
            legacy_is_prefixed = legacy.entity_id.startswith(DEVICE_ENTITY_ID_PREFIX)
            other_is_prefixed = other.entity_id.startswith(DEVICE_ENTITY_ID_PREFIX)
            if (not legacy_is_prefixed) and other_is_prefixed and len(holders) == 1:
                parked = f"{new_uid}:duplicate"
                try:
                    registry.async_update_entity(
                        other.entity_id, new_unique_id=parked
                    )
                except Exception:  # noqa: BLE001 — keep the old entity_id
                    _LOGGER.exception(
                        "Could not free bus-address unique_id; leaving %s unchanged",
                        legacy.entity_id,
                    )
                    continue
            else:
                _LOGGER.warning(
                    "Bus-address unique_id already exists on %s; leaving %s unchanged",
                    other.entity_id,
                    legacy.entity_id,
                )
                continue
        if _entries_with_unique_id(registry, new_uid):
            _LOGGER.warning(
                "Bus-address unique_id already exists; leaving %s unchanged",
                legacy.entity_id,
            )
            continue
        try:
            registry.async_update_entity(legacy.entity_id, new_unique_id=new_uid)
        except Exception:  # noqa: BLE001 — do not change entity_id on failure
            _LOGGER.exception(
                "Could not migrate unique_id; leaving %s unchanged",
                legacy.entity_id,
            )
            continue
        migrated += 1
    if migrated:
        _LOGGER.warning(
            "Migrated %s light unique_id(s) to bus address; entity_id unchanged",
            migrated,
        )
    return migrated


@callback
def async_cleanup_duplicate_lights(hass: HomeAssistant) -> int:
    """Drop a device-prefixed duplicate when the short entity_id already exists.

    Example: ``light.leviton_usb_lighting_office_ceiling_dimmer`` is removed
    only when ``light.office_ceiling_dimmer`` already has the same bus-address
    unique_id. Short entity_ids are never removed or renamed.
    """
    if not inventory.using_private_inventory():
        return 0
    registry = er.async_get(hass)
    removed = 0
    by_slug: dict[str, str] = {}
    for light in inventory.LIGHTS:
        if not isinstance(light, dict):
            continue
        name = light.get("name")
        uid = address_unique_id(light) if isinstance(name, str) else None
        if isinstance(name, str) and uid:
            by_slug[slugify(name)] = uid

    for entry in list(registry.entities.values()):
        if not _is_our_light(entry):
            continue
        entity_id = entry.entity_id
        if not entity_id.startswith(DEVICE_ENTITY_ID_PREFIX):
            continue
        live = registry.entities.get(entity_id)
        if live is None:
            continue
        slug = entity_id.removeprefix(DEVICE_ENTITY_ID_PREFIX)
        expected = by_slug.get(slug)
        if not expected:
            continue
        short_id = f"light.{slug}"
        short = registry.entities.get(short_id)
        if short is None or short.entity_id == entity_id:
            continue
        if short.unique_id != expected:
            continue
        _LOGGER.warning(
            "Removing device-prefixed duplicate %s (keeping %s)",
            entity_id,
            short_id,
        )
        registry.async_remove(entity_id)
        removed += 1
    return removed


def _wanted_unique_ids() -> set[str]:
    wanted: set[str] = set()
    if inventory.using_private_inventory():
        for light in inventory.LIGHTS:
            if not isinstance(light, dict):
                continue
            name = light.get("name")
            if not isinstance(name, str):
                continue
            uid = address_unique_id(light)
            if uid:
                wanted.add(uid)
            wanted.add(legacy_name_unique_id(name))
        return wanted
    return wanted


@callback
def async_reenable_inventory_lights(hass: HomeAssistant) -> int:
    """Clear integration-disabled on lights this setup still provides.

    User disables are left alone. Nothing is disabled or deleted.
    When the example table was withheld, existing my_leviton lights are
    cleared the same way so a placeholder list cannot leave them disabled.
    """
    registry = er.async_get(hass)
    if inventory.INVENTORY_SOURCE == "withheld_examples":
        wanted = {
            entry.unique_id for entry in _our_lights(registry) if entry.unique_id
        }
    else:
        wanted = _wanted_unique_ids()
    if not wanted:
        return 0
    cleared = 0
    for entry in list(_our_lights(registry)):
        if entry.unique_id not in wanted:
            continue
        disabled_by = getattr(entry, "disabled_by", None)
        if disabled_by is None:
            continue
        # Only undo Home Assistant's automatic disable, not a user choice.
        if str(disabled_by) not in {_INTEGRATION_DISABLED, "RegistryEntryDisabler.INTEGRATION"}:
            if getattr(disabled_by, "value", None) != _INTEGRATION_DISABLED:
                continue
        registry.async_update_entity(entry.entity_id, disabled_by=None)
        cleared += 1
    if cleared:
        _LOGGER.warning(
            "Cleared integration disable on %s my_leviton light(s)",
            cleared,
        )
    return cleared


@callback
def async_disable_example_lights(hass: HomeAssistant) -> int:
    """Do not disable lights because their display name matches the examples.

    A private inventory can use the same words as the public placeholders.
    Disabling those entities would remove real devices from automations.
    """
    return 0


@callback
def async_cleanup_dim_start_numbers(hass: HomeAssistant) -> int:
    """Remove legacy dim_start number entities (platform removed in 1.1.0b14)."""
    registry = er.async_get(hass)
    removed = 0
    for entry in list(registry.entities.values()):
        domain = getattr(entry, "domain", None)
        if domain not in (None, "number"):
            continue
        if not str(getattr(entry, "entity_id", "")).startswith("number."):
            continue
        if getattr(entry, "platform", None) != DOMAIN:
            continue
        unique_id = entry.unique_id or ""
        if not unique_id.startswith("my_leviton::dim_start::"):
            continue
        _LOGGER.warning("Removing legacy dim_start entity %s", entry.entity_id)
        registry.async_remove(entry.entity_id)
        removed += 1
    return removed


@callback
def async_remove_extra_config_entries(hass: HomeAssistant, keep_entry_id: str) -> int:
    """Remove extra my_leviton config entries; keep the active one."""
    removed = 0
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == keep_entry_id:
            continue
        _LOGGER.warning(
            "Removing duplicate config entry %s (%s)", entry.entry_id, entry.title
        )
        hass.async_create_task(hass.config_entries.async_remove(entry.entry_id))
        removed += 1
    return removed
