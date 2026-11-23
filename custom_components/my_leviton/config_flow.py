"""Config flow for My Leviton (USB) — discovery onboarding.

Scan the VRUSB bus for addresses, correlate with the local inventory and HA
entity registry by (type, channel, device_id), and preserve existing
``my_leviton::{name}`` / entity_ids. New addresses prefer bus-defined names
from status frames when present, otherwise placeholders; existing inventory /
HA names are never removed. Users pick which new devices to import and set
friendly names on the same confirm step (matched renames stay optional).
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import DOMAIN, UNIQUE_ID
from .utils.constants import persist_inventory, reload_lights
from .utils.discovery import (
    BusDevice,
    MatchedDevice,
    correlate_devices,
    discover_bus_devices,
    discovery_summary,
    matched_device_option_label,
    matched_rename_field_key,
    merge_inventory_additive,
    new_device_name_field_key,
    new_device_option_label,
    parse_registry_lights,
    resolve_matched_renames,
    resolve_new_name_overrides,
)
from .utils.sniff import _is_clean_bus_name

_LOGGER = logging.getLogger(__name__)

CONFIG_UNIQUE_ID = UNIQUE_ID
_DEFAULT_SCAN_SEC = 8.0

# Human-readable schema keys so HA shows these as field labels even when
# translation lookup fails (same pattern as dynamic ``Name — …`` fields).
FIELD_IMPORT_DEVICES = "New devices to import"
FIELD_RENAME_DEVICES = "Matched devices to rename (optional)"
# Legacy machine keys still accepted from older form submissions.
_LEGACY_IMPORT_KEY = "import_devices"
_LEGACY_RENAME_KEY = "rename_devices"


def _registry_lights(hass: HomeAssistant) -> list:
    registry = er.async_get(hass)
    rows = [
        {
            "entity_id": entry.entity_id,
            "unique_id": entry.unique_id,
            "platform": entry.platform,
            "domain": entry.domain,
            "original_name": entry.original_name,
            "name": entry.name,
            "disabled_by": entry.disabled_by,
        }
        for entry in registry.entities.values()
        if entry.platform == DOMAIN and entry.domain == "light"
    ]
    return parse_registry_lights(rows)


async def _discover_and_correlate(
    hass: HomeAssistant,
    *,
    duration_sec: float = _DEFAULT_SCAN_SEC,
) -> tuple[list[MatchedDevice], dict[str, Any]]:
    """Scan USB (executor) then correlate with inventory + entity registry."""
    inventory = list(reload_lights())
    registry = _registry_lights(hass)

    def _scan() -> list[BusDevice]:
        return discover_bus_devices(duration_sec)

    bus = await hass.async_add_executor_job(_scan)
    matched = correlate_devices(bus, inventory, registry)
    summary = discovery_summary(matched)
    summary["bus_raw"] = len(bus)
    return matched, summary


def _new_devices(matched: list[MatchedDevice]) -> list[MatchedDevice]:
    return [m for m in matched if m.is_new]


def _matched_online(matched: list[MatchedDevice]) -> list[MatchedDevice]:
    """Matched inventory devices seen on this scan (candidates for rename)."""
    return [
        m
        for m in matched
        if not m.is_new and m.match_source == "inventory_addr" and m.frame_count > 0
    ]


def _form_list(user_input: dict[str, Any], *keys: str, default: Any = None) -> list[str]:
    """Read a multi-select value, trying human-readable then legacy keys."""
    raw: Any = None
    for key in keys:
        if key in user_input:
            raw = user_input[key]
            break
    if raw is None:
        raw = default if default is not None else []
    if isinstance(raw, str):
        raw = [raw]
    return [str(k) for k in raw]


def _parse_confirm_input(
    matched: list[MatchedDevice], user_input: dict[str, Any]
) -> tuple[list[str], dict[str, str], list[str]]:
    """Extract import keys, name overrides, and rename keys from confirm form."""
    new_devs = _new_devices(matched)
    if new_devs:
        pending_import = _form_list(
            user_input,
            FIELD_IMPORT_DEVICES,
            _LEGACY_IMPORT_KEY,
            default=list({m.form_key() for m in new_devs}),
        )
    else:
        pending_import = []

    overrides = resolve_new_name_overrides(matched, user_input)
    if pending_import:
        selected_set = set(pending_import)
        overrides = {k: v for k, v in overrides.items() if k in selected_set}
    else:
        overrides = {}

    pending_rename = _form_list(
        user_input, FIELD_RENAME_DEVICES, _LEGACY_RENAME_KEY, default=[]
    )
    return pending_import, overrides, pending_rename


def _select_options(mapping: dict[str, str]) -> list[dict[str, str]]:
    """Build SelectSelector options with explicit labels (HA shows these)."""
    return [{"value": k, "label": lbl} for k, lbl in mapping.items()]


def _format_confirm_description(
    summary: dict[str, Any], matched: list[MatchedDevice]
) -> str:
    """Short description above the form: stats + new devices only.

    Matched devices are *not* listed here (HA always renders description
    above the schema). They appear only in the rename multi-select.
    """
    lines = [
        "Bus scan complete. Devices match inventory by USB address "
        "(type / channel / device_id). Existing entity IDs stay unchanged.",
        "",
        f"On bus: {summary.get('bus_raw', 0)} · "
        f"Matched: {summary.get('matched', 0)} · "
        f"New: {summary.get('new', 0)} · "
        f"Inventory offline: {summary.get('offline_inventory', 0)}",
        f"Dimmers: {summary.get('dimmers', 0)} · Relays: {summary.get('relays', 0)}",
    ]

    new_devs = _new_devices(matched)
    if new_devs:
        lines.append("")
        lines.append("—— New devices ——")
        lines.append(
            "Import and name them in the form below (defaults to the bus "
            "wire name when present). Matched devices are listed only in "
            "the optional rename field."
        )
        for m in new_devs[:20]:
            wire = (m.bus_name or "").strip()
            wire_s = f"wire: {wire}" if _is_clean_bus_name(wire) else "wire: (none)"
            lvl = "?" if m.last_level is None else str(m.last_level)
            lines.append(
                f"· {m.name} — {m.kind} "
                f"ch=0x{m.channel:02x} id=0x{m.device_id:04x} "
                f"{wire_s} level={lvl}"
            )
        if len(new_devs) > 20:
            lines.append(f"· …and {len(new_devs) - 20} more")
    else:
        lines.append("")
        lines.append("—— New devices ——")
        lines.append("No new addresses — existing inventory covers the bus.")

    return "\n".join(lines)


def _confirm_schema(matched: list[MatchedDevice]) -> vol.Schema:
    """Confirm form: import → name text fields → rename (matched last)."""
    fields: dict[Any, Any] = {}
    new_devs = _new_devices(matched)
    if new_devs:
        options = {m.form_key(): new_device_option_label(m) for m in new_devs}
        fields[vol.Optional(FIELD_IMPORT_DEVICES, default=list(options))] = (
            SelectSelector(
                SelectSelectorConfig(
                    options=_select_options(options),
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )
        )
        # Friendly-name fields immediately after import (same step).
        # Schema key is the label HA displays (dynamic; not in en.json).
        for m in new_devs:
            field_key = new_device_name_field_key(m)
            fields[vol.Optional(field_key, default=m.name)] = TextSelector()

    rename_candidates = [m for m in _matched_online(matched) if m.entity_id]
    if rename_candidates:
        options = {
            m.form_key(): matched_device_option_label(m) for m in rename_candidates
        }
        fields[vol.Optional(FIELD_RENAME_DEVICES, default=[])] = SelectSelector(
            SelectSelectorConfig(
                options=_select_options(options),
                multiple=True,
                mode=SelectSelectorMode.LIST,
            )
        )

    if not fields:
        # Nothing interactive — empty schema still allows Confirm.
        fields[vol.Optional("noop", default=True)] = bool
    return vol.Schema(fields)


def _rename_schema(
    matched: list[MatchedDevice], selected_keys: list[str]
) -> vol.Schema:
    by_key = {m.form_key(): m for m in matched if not m.is_new}
    fields: dict[Any, Any] = {}
    for key in selected_keys:
        m = by_key.get(key)
        if m is None:
            continue
        current = (m.friendly_name or m.name or "").strip()
        fields[vol.Optional(matched_rename_field_key(m), default=current)] = (
            TextSelector()
        )
    if not fields:
        fields[vol.Optional("noop", default=True)] = bool
    return vol.Schema(fields)


def _format_rename_description(
    matched: list[MatchedDevice], selected_keys: list[str]
) -> str:
    by_key = {m.form_key(): m for m in matched if not m.is_new}
    lines = [
        "Edit friendly names for the matched devices you selected. "
        "Entity IDs and unique_ids stay the same (registry override only).",
        "",
    ]
    for key in selected_keys:
        m = by_key.get(key)
        if m is None:
            continue
        wire = (m.bus_name or "").strip()
        wire_s = f"Wire: {wire}" if _is_clean_bus_name(wire) else "Wire: (none)"
        eid = m.entity_id or "(no entity yet)"
        lines.append(
            f"· {m.name} — {wire_s} — {eid} "
            f"(ch=0x{m.channel:02x} id=0x{m.device_id:04x})"
        )
    return "\n".join(lines)


def _persist_selected_new(
    matched: list[MatchedDevice],
    selected_keys: list[str],
    name_overrides: dict[str, str],
) -> int:
    """Append selected new bus addresses to inventory. Returns count added."""
    if not selected_keys:
        return 0
    inventory = list(reload_lights())
    merged = merge_inventory_additive(
        inventory,
        matched,
        include_new=True,
        selected_keys=selected_keys,
        name_overrides=name_overrides,
    )
    added = len(merged) - len(inventory)
    if added <= 0:
        return 0
    written = persist_inventory(merged)
    reload_lights()
    _LOGGER.warning(
        "Discovery appended %s new light(s) to %s (existing names unchanged)",
        added,
        ", ".join(str(p) for p in written),
    )
    return added


async def _apply_matched_renames(
    hass: HomeAssistant,
    changes: list[tuple[MatchedDevice, str]],
) -> int:
    """Apply friendly-name overrides via entity registry (preserve entity_id)."""
    if not changes:
        return 0
    registry = er.async_get(hass)
    applied = 0
    for device, new_name in changes:
        if not device.entity_id:
            continue
        registry.async_update_entity(device.entity_id, name=new_name)
        applied += 1
        _LOGGER.info(
            "Discovery renamed friendly name for %s → %r (unique_id unchanged)",
            device.entity_id,
            new_name,
        )
    return applied


class MyLevitonConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for My Leviton (USB)."""

    VERSION = 2

    def __init__(self) -> None:
        self._matched: list[MatchedDevice] = []
        self._summary: dict[str, Any] = {}
        self._pending_import: list[str] = []
        self._pending_name_overrides: dict[str, str] = {}
        self._pending_rename_keys: list[str] = []

    async def async_step_user(self, user_input=None):
        """Start onboarding — one USB controller / one config entry."""
        await self.async_set_unique_id(CONFIG_UNIQUE_ID)
        self._abort_if_unique_id_configured()

        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return await self.async_step_discover()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
            description_placeholders={
                "scan_sec": str(int(_DEFAULT_SCAN_SEC)),
            },
        )

    async def async_step_discover(self, user_input=None):
        """Scan the bus and correlate with local inventory + registry."""
        try:
            self._matched, self._summary = await _discover_and_correlate(self.hass)
        except Exception:  # noqa: BLE001
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({}),
                errors={"base": "usb_error"},
                description_placeholders={
                    "scan_sec": str(int(_DEFAULT_SCAN_SEC)),
                },
            )
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input=None):
        """Import new devices (with names) and optional matched renames."""
        if user_input is not None:
            (
                self._pending_import,
                self._pending_name_overrides,
                self._pending_rename_keys,
            ) = _parse_confirm_input(self._matched, user_input)

            if self._pending_rename_keys:
                return await self.async_step_rename()
            return await self._async_finish_setup()

        return self.async_show_form(
            step_id="confirm",
            data_schema=_confirm_schema(self._matched),
            description_placeholders={
                "summary": _format_confirm_description(self._summary, self._matched),
            },
        )

    async def async_step_rename(self, user_input=None):
        """Friendly-name edits for selected matched devices."""
        if user_input is not None:
            changes = resolve_matched_renames(
                self._matched, self._pending_rename_keys, user_input
            )
            await _apply_matched_renames(self.hass, changes)
            return await self._async_finish_setup()

        return self.async_show_form(
            step_id="rename",
            data_schema=_rename_schema(self._matched, self._pending_rename_keys),
            description_placeholders={
                "summary": _format_rename_description(
                    self._matched, self._pending_rename_keys
                ),
            },
        )

    async def _async_finish_setup(self):
        await self.hass.async_add_executor_job(
            _persist_selected_new,
            self._matched,
            self._pending_import,
            self._pending_name_overrides,
        )
        data = {
            "discovery": {
                "matched": self._summary.get("matched"),
                "new": len(self._pending_import),
                "bus_raw": self._summary.get("bus_raw"),
            }
        }
        return self.async_create_entry(title="My Leviton (USB)", data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return MyLevitonOptionsFlow()


class MyLevitonOptionsFlow(config_entries.OptionsFlow):
    """Rescan bus / import new addresses without removing existing entities."""

    def __init__(self) -> None:
        self._matched: list[MatchedDevice] = []
        self._summary: dict[str, Any] = {}
        self._pending_import: list[str] = []
        self._pending_name_overrides: dict[str, str] = {}
        self._pending_rename_keys: list[str] = []

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return await self.async_step_discover()
        return self.async_show_form(step_id="init", data_schema=vol.Schema({}))

    async def async_step_discover(self, user_input=None):
        try:
            self._matched, self._summary = await _discover_and_correlate(self.hass)
        except Exception:  # noqa: BLE001
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({}),
                errors={"base": "usb_error"},
            )
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input=None):
        if user_input is not None:
            (
                self._pending_import,
                self._pending_name_overrides,
                self._pending_rename_keys,
            ) = _parse_confirm_input(self._matched, user_input)

            if self._pending_rename_keys:
                return await self.async_step_rename()
            return await self._async_finish_options()

        return self.async_show_form(
            step_id="confirm",
            data_schema=_confirm_schema(self._matched),
            description_placeholders={
                "summary": _format_confirm_description(self._summary, self._matched),
            },
        )

    async def async_step_rename(self, user_input=None):
        if user_input is not None:
            changes = resolve_matched_renames(
                self._matched, self._pending_rename_keys, user_input
            )
            await _apply_matched_renames(self.hass, changes)
            return await self._async_finish_options()

        return self.async_show_form(
            step_id="rename",
            data_schema=_rename_schema(self._matched, self._pending_rename_keys),
            description_placeholders={
                "summary": _format_rename_description(
                    self._matched, self._pending_rename_keys
                ),
            },
        )

    async def _async_finish_options(self):
        added = await self.hass.async_add_executor_job(
            _persist_selected_new,
            self._matched,
            self._pending_import,
            self._pending_name_overrides,
        )
        if added:
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
        return self.async_create_entry(
            title="",
            data={
                "last_discovery": {
                    "matched": self._summary.get("matched"),
                    "new": len(self._pending_import),
                    "bus_raw": self._summary.get("bus_raw"),
                    "added": added,
                }
            },
        )
