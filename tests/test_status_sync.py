"""Unit tests for hardware→HA state sync helpers."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(rel: str, name: str):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


protocol = _load(
    "custom_components/my_leviton/utils/protocol.py",
    "leviton_protocol_status",
)
# status.py imports from .protocol / .locks / sniff stack — load package-style
for name, rel in (
    (
        "custom_components.my_leviton.utils.constants",
        "custom_components/my_leviton/utils/constants.py",
    ),
    (
        "custom_components.my_leviton.utils.locks",
        "custom_components/my_leviton/utils/locks.py",
    ),
    (
        "custom_components.my_leviton.utils.protocol",
        "custom_components/my_leviton/utils/protocol.py",
    ),
):
    if name not in sys.modules:
        _load(rel, name)

# Stub usb before sniff/status if needed
import types

if "usb" not in sys.modules:
    usb = types.ModuleType("usb")
    usb_core = types.ModuleType("usb.core")
    usb_util = types.ModuleType("usb.util")
    usb_core.USBError = type("USBError", (Exception,), {})
    usb.core = usb_core
    usb.util = usb_util
    sys.modules["usb"] = usb
    sys.modules["usb.core"] = usb_core
    sys.modules["usb.util"] = usb_util

status = _load(
    "custom_components/my_leviton/utils/status.py",
    "custom_components.my_leviton.utils.status",
)

brightness_to_level = protocol.brightness_to_level
level_to_brightness = status.level_to_brightness
level_to_entity_state = status.level_to_entity_state
settle_reported_level = status.settle_reported_level


class TestLevelToBrightness(unittest.TestCase):
    def test_endpoints(self):
        self.assertEqual(level_to_brightness(0), 0)
        self.assertEqual(level_to_brightness(100), 255)

    def test_mid_round_trip(self):
        b = level_to_brightness(70)
        self.assertTrue(1 <= b <= 255)
        self.assertEqual(brightness_to_level(b), 70)

    def test_floor_61(self):
        self.assertEqual(brightness_to_level(level_to_brightness(61)), 61)


class TestLevelToEntityState(unittest.TestCase):
    def test_dimmer_off(self):
        self.assertEqual(level_to_entity_state(0, dimmer=True), (False, 0))

    def test_dimmer_on(self):
        is_on, bri = level_to_entity_state(80, dimmer=True)
        self.assertTrue(is_on)
        self.assertEqual(bri, level_to_brightness(80))

    def test_relay_off(self):
        self.assertEqual(level_to_entity_state(0, dimmer=False), (False, None))

    def test_relay_on(self):
        self.assertEqual(level_to_entity_state(100, dimmer=False), (True, None))
        self.assertEqual(level_to_entity_state(1, dimmer=False), (True, None))


class TestSettleReportedLevel(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(settle_reported_level([]))
        self.assertIsNone(settle_reported_level([None, None]))

    def test_last_wins(self):
        self.assertEqual(settle_reported_level([100, 80]), 80)

    def test_off_echo_then_mid(self):
        self.assertEqual(settle_reported_level([0, 0, 61]), 61)
        self.assertEqual(settle_reported_level([0, None, 70]), 70)

    def test_final_off(self):
        self.assertEqual(settle_reported_level([80, 0]), 0)

    def test_skips_none(self):
        self.assertEqual(settle_reported_level([None, 50, None]), 50)


if __name__ == "__main__":
    unittest.main()
