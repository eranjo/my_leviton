"""Unit tests for duplicate-entity cleanup (no Home Assistant install required)."""
from __future__ import annotations

import importlib.util
import re
import sys
import types
import unicodedata
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _install_ha_stubs() -> None:
    """Install minimal homeassistant stubs before loading entity_cleanup."""
    if "homeassistant" in sys.modules and getattr(
        sys.modules["homeassistant"], "__path__", None
    ):
        return

    ha = types.ModuleType("homeassistant")
    ha.__path__ = []  # mark as package
    ha_core = types.ModuleType("homeassistant.core")
    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha_helpers.__path__ = []
    ha_er = types.ModuleType("homeassistant.helpers.entity_registry")
    ha_util = types.ModuleType("homeassistant.util")

    def callback(func):
        return func

    ha_core.HomeAssistant = object
    ha_core.callback = callback
    ha_util.slugify = slugify
    ha_er.async_get = lambda hass: None

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.core"] = ha_core
    sys.modules["homeassistant.helpers"] = ha_helpers
    sys.modules["homeassistant.helpers.entity_registry"] = ha_er
    sys.modules["homeassistant.util"] = ha_util


def _load_cleanup_module():
    """Load entity_cleanup.py without importing package __init__.py."""
    _install_ha_stubs()

    # Stub sibling packages imported by entity_cleanup
    cc = types.ModuleType("custom_components")
    cc.__path__ = [str(ROOT / "custom_components")]
    ml = types.ModuleType("custom_components.my_leviton")
    ml.__path__ = [str(ROOT / "custom_components" / "my_leviton")]
    sys.modules.setdefault("custom_components", cc)
    sys.modules["custom_components.my_leviton"] = ml

    # Load const + constants first (no HA deps)
    for name, rel in (
        ("custom_components.my_leviton.const", "custom_components/my_leviton/const.py"),
        (
            "custom_components.my_leviton.utils",
            "custom_components/my_leviton/utils/__init__.py",
        ),
        (
            "custom_components.my_leviton.utils.constants",
            "custom_components/my_leviton/utils/constants.py",
        ),
    ):
        path = ROOT / rel
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        if name.endswith(".utils"):
            mod.__path__ = [str(path.parent)]
        spec.loader.exec_module(mod)
        if name.endswith(".my_leviton"):
            pass
        parent_attr = name.rsplit(".", 1)[-1]
        parent = sys.modules[name.rsplit(".", 1)[0]]
        setattr(parent, parent_attr, mod)

    # Attach const onto package
    ml.const = sys.modules["custom_components.my_leviton.const"]
    ml.utils = sys.modules["custom_components.my_leviton.utils"]

    path = ROOT / "custom_components/my_leviton/entity_cleanup.py"
    spec = importlib.util.spec_from_file_location(
        "custom_components.my_leviton.entity_cleanup", path
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["custom_components.my_leviton.entity_cleanup"] = mod
    spec.loader.exec_module(mod)
    return mod


cleanup_mod = _load_cleanup_module()
async_cleanup_duplicate_lights = cleanup_mod.async_cleanup_duplicate_lights


@dataclass
class FakeRegistryEntry:
    entity_id: str
    unique_id: str | None
    domain: str = "light"
    platform: str = "my_leviton"
    disabled_by: str | None = None


class FakeRegistry:
    def __init__(self, entries: list[FakeRegistryEntry]):
        self.entities: dict[str, FakeRegistryEntry] = {
            e.entity_id: e for e in entries
        }
        self.removed: list[str] = []
        self.renamed: list[tuple[str, str]] = []

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)
        self.entities.pop(entity_id, None)

    def async_update_entity(self, entity_id: str, **kwargs: Any) -> None:
        entry = self.entities[entity_id]
        new_uid = kwargs.get("new_unique_id")
        if new_uid:
            entry.unique_id = new_uid
        if "disabled_by" in kwargs:
            entry.disabled_by = kwargs["disabled_by"]
        new_id = kwargs.get("new_entity_id")
        if new_id:
            self.entities.pop(entity_id)
            entry.entity_id = new_id
            self.entities[new_id] = entry
            self.renamed.append((entity_id, new_id))


# Public-example style row used only in tests. Not a home name.
SAMPLE = {
    "name": "Office Ceiling Dimmer",
    "type": 0x1E,
    "channel": 0x03,
    "device_id": 0xA201,
    "dimmer": True,
}
ADDRESS_UID = "my_leviton:001e:03:a201"
LEGACY_UID = "my_leviton::Office Ceiling Dimmer"
SHORT_ID = "light.office_ceiling_dimmer"
LONG_ID = "light.leviton_usb_lighting_office_ceiling_dimmer"


class CleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = cleanup_mod.inventory
        self._source = self.inventory.INVENTORY_SOURCE
        self._lights = [dict(light) for light in self.inventory.LIGHTS]
        self.inventory._set_lights([dict(SAMPLE)])
        self.inventory.INVENTORY_SOURCE = "yaml"
        self.hass = MagicMock()

    def tearDown(self) -> None:
        self.inventory._set_lights(self._lights)
        self.inventory.INVENTORY_SOURCE = self._source

    def _use(self, registry: FakeRegistry) -> None:
        cleanup_mod.er.async_get = lambda _hass: registry

    def test_address_unique_id_omits_name(self):
        self.assertEqual(self.inventory.address_unique_id(SAMPLE), ADDRESS_UID)
        self.assertEqual(
            self.inventory.address_unique_id(
                {"type": "0x1E", "channel": "0x03", "device_id": "0xA201"}
            ),
            ADDRESS_UID,
        )
        self.assertIsNone(self.inventory.address_unique_id({"name": SAMPLE["name"]}))

    def test_removes_device_prefixed_duplicate_for_same_address(self):
        registry = FakeRegistry(
            [
                FakeRegistryEntry(SHORT_ID, ADDRESS_UID),
                FakeRegistryEntry(LONG_ID, ADDRESS_UID),
            ]
        )
        self._use(registry)
        removed = async_cleanup_duplicate_lights(self.hass)
        self.assertEqual(removed, 1)
        self.assertIn(LONG_ID, registry.removed)
        self.assertIn(SHORT_ID, registry.entities)
        self.assertNotIn(LONG_ID, registry.entities)
        self.assertEqual(registry.renamed, [])

    def test_does_not_rename_or_remove_when_short_id_is_missing(self):
        registry = FakeRegistry([FakeRegistryEntry(LONG_ID, ADDRESS_UID)])
        self._use(registry)
        removed = async_cleanup_duplicate_lights(self.hass)
        self.assertEqual(removed, 0)
        self.assertEqual(registry.renamed, [])
        self.assertIn(LONG_ID, registry.entities)
        self.assertEqual(registry.entities[LONG_ID].entity_id, LONG_ID)

    def test_does_not_remove_short_entity_id(self):
        registry = FakeRegistry(
            [
                FakeRegistryEntry(SHORT_ID, None),
                FakeRegistryEntry(LONG_ID, LEGACY_UID),
            ]
        )
        self._use(registry)
        removed = async_cleanup_duplicate_lights(self.hass)
        self.assertEqual(removed, 0)
        self.assertNotIn(SHORT_ID, registry.removed)
        self.assertIn(SHORT_ID, registry.entities)
        self.assertIn(LONG_ID, registry.entities)

    def test_migration_keeps_entity_id(self):
        registry = FakeRegistry([FakeRegistryEntry(SHORT_ID, LEGACY_UID)])
        self._use(registry)
        migrated = cleanup_mod.async_migrate_light_unique_ids(self.hass)
        self.assertEqual(migrated, 1)
        entry = registry.entities[SHORT_ID]
        self.assertEqual(entry.unique_id, ADDRESS_UID)
        self.assertEqual(entry.entity_id, SHORT_ID)
        self.assertEqual(registry.renamed, [])
        self.assertEqual(registry.removed, [])

    def test_migration_leaves_both_when_address_already_on_another_short_id(self):
        other = "light.other_dimmer"
        registry = FakeRegistry(
            [
                FakeRegistryEntry(SHORT_ID, LEGACY_UID),
                FakeRegistryEntry(other, ADDRESS_UID),
            ]
        )
        self._use(registry)
        migrated = cleanup_mod.async_migrate_light_unique_ids(self.hass)
        self.assertEqual(migrated, 0)
        self.assertEqual(registry.entities[SHORT_ID].unique_id, LEGACY_UID)
        self.assertEqual(registry.entities[other].unique_id, ADDRESS_UID)
        self.assertEqual(registry.removed, [])
        self.assertIsNone(registry.entities[SHORT_ID].disabled_by)

    def test_migration_prefers_existing_short_entity_id(self):
        registry = FakeRegistry(
            [
                FakeRegistryEntry(SHORT_ID, LEGACY_UID),
                FakeRegistryEntry(LONG_ID, ADDRESS_UID),
            ]
        )
        self._use(registry)
        migrated = cleanup_mod.async_migrate_light_unique_ids(self.hass)
        self.assertEqual(migrated, 1)
        self.assertEqual(registry.entities[SHORT_ID].unique_id, ADDRESS_UID)
        self.assertEqual(registry.entities[SHORT_ID].entity_id, SHORT_ID)
        self.assertEqual(registry.entities[LONG_ID].unique_id, ADDRESS_UID + ":duplicate")
        self.assertEqual(registry.removed, [])
        removed = async_cleanup_duplicate_lights(self.hass)
        self.assertEqual(removed, 1)
        self.assertIn(SHORT_ID, registry.entities)
        self.assertNotIn(LONG_ID, registry.entities)

    def test_migration_skipped_for_example_table(self):
        registry = FakeRegistry([FakeRegistryEntry(SHORT_ID, LEGACY_UID)])
        self._use(registry)
        self.inventory.INVENTORY_SOURCE = "examples"
        migrated = cleanup_mod.async_migrate_light_unique_ids(self.hass)
        self.assertEqual(migrated, 0)
        self.assertEqual(registry.entities[SHORT_ID].unique_id, LEGACY_UID)

    def test_choose_inventory_order(self):
        private = [dict(SAMPLE)]
        local = [
            {
                "name": "Stair Dimmer",
                "type": 0x1E,
                "channel": 0x01,
                "device_id": 0xB101,
                "dimmer": True,
            }
        ]
        choose = self.inventory.choose_inventory
        source, rows = choose(private, True, local, True)
        self.assertEqual(source, "yaml")
        self.assertEqual(rows[0]["name"], SAMPLE["name"])
        source, rows = choose(None, False, local, True)
        self.assertEqual(source, "constants_local")
        self.assertEqual(rows[0]["name"], "Stair Dimmer")
        source, _rows = choose(None, False, None, False)
        self.assertEqual(source, "examples")
        source, rows = choose(list(self.inventory._EXAMPLE_LIGHTS), True, local, True)
        self.assertEqual(source, "constants_local")
        self.assertEqual(rows[0]["name"], "Stair Dimmer")


if __name__ == "__main__":
    unittest.main()
