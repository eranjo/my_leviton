"""Remove duplicate light entities left after re-adding / HACS updates."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import DEVICE_ENTITY_ID_PREFIX, DOMAIN
from .utils.constants import LIGHTS

_LOGGER = logging.getLogger(__name__)


def _expected_unique_id(name: str) -> str:
    return f"my_leviton::{name}"


@callback
def async_cleanup_duplicate_lights(hass: HomeAssistant) -> int:
    """Drop device-prefixed duplicates; keep stable short entity IDs.

    After some upgrades users end up with both:
      light.casper_katto_dimmer
      light.leviton_usb_lighting_casper_katto_dimmer
    Only one should remain (the short form, matching unique_id my_leviton::Name).
    """
    registry = er.async_get(hass)
    removed = 0

    by_slug: dict[str, str] = {
        slugify(light["name"]): _expected_unique_id(light["name"]) for light in LIGHTS
    }

    # Prefer handling long (device-prefixed) ids first so renames happen before
    # any orphan cleanup touches the short id.
    our_entries = sorted(
        (
            entry
            for entry in registry.entities.values()
            if entry.domain == "light" and entry.platform == DOMAIN
        ),
        key=lambda e: (0 if e.entity_id.startswith(DEVICE_ENTITY_ID_PREFIX) else 1),
    )

    for entry in our_entries:
        # Re-read live registry — earlier steps may have renamed/removed.
        live = registry.entities.get(entry.entity_id)
        if live is None:
            continue

        entity_id = live.entity_id
        unique_id = live.unique_id

        if not entity_id.startswith(DEVICE_ENTITY_ID_PREFIX):
            continue

        slug = entity_id.removeprefix(DEVICE_ENTITY_ID_PREFIX)
        if slug not in by_slug:
            continue

        short_id = f"light.{slug}"
        expected = by_slug[slug]
        short_entry = registry.entities.get(short_id)

        if short_entry is not None and short_entry.entity_id != entity_id:
            short_ok = short_entry.unique_id == expected
            long_ok = unique_id == expected

            if short_ok or not long_ok:
                _LOGGER.warning(
                    "Removing duplicate entity %s (keeping %s)",
                    entity_id,
                    short_id,
                )
                registry.async_remove(entity_id)
                removed += 1
            else:
                _LOGGER.warning(
                    "Removing orphan %s and renaming %s → %s",
                    short_id,
                    entity_id,
                    short_id,
                )
                registry.async_remove(short_id)
                registry.async_update_entity(entity_id, new_entity_id=short_id)
                removed += 1
        else:
            _LOGGER.warning(
                "Renaming %s → %s to restore stable entity id",
                entity_id,
                short_id,
            )
            registry.async_update_entity(entity_id, new_entity_id=short_id)

    # Second pass: short orphans (no unique_id) when a proper entity exists.
    for entry in list(registry.entities.values()):
        if entry.domain != "light" or entry.platform != DOMAIN:
            continue
        if entry.unique_id:
            continue
        if not entry.entity_id.startswith("light."):
            continue
        slug = entry.entity_id.removeprefix("light.")
        expected = by_slug.get(slug)
        if not expected:
            continue
        # Another registry row already owns this unique_id.
        has_canonical = any(
            other.entity_id != entry.entity_id and other.unique_id == expected
            for other in registry.entities.values()
            if other.domain == "light" and other.platform == DOMAIN
        )
        if has_canonical:
            _LOGGER.warning(
                "Removing orphan entity without unique_id: %s", entry.entity_id
            )
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
