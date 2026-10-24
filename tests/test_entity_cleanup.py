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
        new_id = kwargs.get("new_entity_id")
        if new_id:
            entry = self.entities.pop(entity_id)
            entry.entity_id = new_id
            self.entities[new_id] = entry
            self.renamed.append((entity_id, new_id))


class CleanupTests(unittest.TestCase):
    def test_removes_device_prefixed_duplicate(self):
        registry = FakeRegistry(
            [
                FakeRegistryEntry(
                    "light.casper_katto_dimmer",
                    "my_leviton::Casper Katto (dimmer)",
                ),
                FakeRegistryEntry(
                    "light.leviton_usb_lighting_casper_katto_dimmer",
                    "my_leviton::Casper Katto (dimmer)",
                ),
            ]
        )
        hass = MagicMock()
        cleanup_mod.er.async_get = lambda _hass: registry

        removed = async_cleanup_duplicate_lights(hass)
        self.assertEqual(removed, 1)
        self.assertIn(
            "light.leviton_usb_lighting_casper_katto_dimmer", registry.removed
        )
        self.assertIn("light.casper_katto_dimmer", registry.entities)
        self.assertNotIn(
            "light.leviton_usb_lighting_casper_katto_dimmer", registry.entities
        )

    def test_renames_long_form_when_short_missing(self):
        registry = FakeRegistry(
            [
                FakeRegistryEntry(
                    "light.leviton_usb_lighting_casper_katto_dimmer",
                    "my_leviton::Casper Katto (dimmer)",
                ),
            ]
        )
        hass = MagicMock()
        cleanup_mod.er.async_get = lambda _hass: registry

        async_cleanup_duplicate_lights(hass)
        self.assertEqual(
            registry.renamed,
            [
                (
                    "light.leviton_usb_lighting_casper_katto_dimmer",
                    "light.casper_katto_dimmer",
                )
            ],
        )
        self.assertIn("light.casper_katto_dimmer", registry.entities)

    def test_keeps_short_when_long_has_unique_id_only(self):
        """Short orphan without unique_id is removed; long renamed to short."""
        registry = FakeRegistry(
            [
                FakeRegistryEntry("light.casper_katto_dimmer", None),
                FakeRegistryEntry(
                    "light.leviton_usb_lighting_casper_katto_dimmer",
                    "my_leviton::Casper Katto (dimmer)",
                ),
            ]
        )
        hass = MagicMock()
        cleanup_mod.er.async_get = lambda _hass: registry

        async_cleanup_duplicate_lights(hass)
        self.assertIn("light.casper_katto_dimmer", registry.removed)
        self.assertEqual(
            registry.renamed,
            [
                (
                    "light.leviton_usb_lighting_casper_katto_dimmer",
                    "light.casper_katto_dimmer",
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
