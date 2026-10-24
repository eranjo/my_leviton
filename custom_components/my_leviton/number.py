"""Number entities for per-dimmer hardware dim-start calibration."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .const import DOMAIN, OPTIONS_DIM_START


class LevitonDimStartNumber(NumberEntity):
    """Hardware low-end / first-visible level (0-100) for one dimmer.

    HA brightness is mapped onto ``dim_start``..100 so the light slider uses
    the dimmer's usable range (Leviton software trim / curve).
    """

    _attr_has_entity_name = False
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = "%"

    def __init__(self, entry: ConfigEntry, light_name: str):
        self._entry = entry
        self._light_name = light_name
        self._attr_name = f"{light_name} dim start"
        self._attr_unique_id = f"my_leviton::dim_start::{light_name}"
        self._attr_suggested_object_id = slugify(f"{light_name}_dim_start")

    def _entry_now(self) -> ConfigEntry:
        entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        return entry or self._entry

    @property
    def native_value(self) -> float:
        raw = self._entry_now().options.get(OPTIONS_DIM_START, {}).get(
            self._light_name, 0
        )
        try:
            return float(max(0, min(100, int(raw))))
        except (TypeError, ValueError):
            return 0.0

    async def async_set_native_value(self, value: float) -> None:
        level = max(0, min(100, int(round(value))))
        entry = self._entry_now()
        options = dict(entry.options)
        dim_starts = dict(options.get(OPTIONS_DIM_START, {}))
        dim_starts[self._light_name] = level
        options[OPTIONS_DIM_START] = dim_starts
        self.hass.config_entries.async_update_entry(entry, options=options)
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "usb_controller")},
            manufacturer="Leviton",
            name="Leviton USB Lighting",
            model="USB Controller",
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    from .utils.constants import LIGHTS

    entities = [
        LevitonDimStartNumber(entry, light["name"])
        for light in LIGHTS
        if light.get("dimmer")
    ]
    async_add_entities(entities, update_before_add=False)
