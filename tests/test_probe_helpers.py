"""Offline tests for dump/probe helper logic (no USB hardware)."""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    usb = types.ModuleType("usb")
    usb_core = types.ModuleType("usb.core")
    usb_util = types.ModuleType("usb.util")
    usb_core.USBError = type("USBError", (Exception,), {})
    usb_core.Device = type("Device", (), {})
    usb.core = usb_core
    usb.util = usb_util
    sys.modules["usb"] = usb
    sys.modules["usb.core"] = usb_core
    sys.modules["usb.util"] = usb_util

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
        path = ROOT / rel
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)

    path = ROOT / "custom_components/my_leviton/utils/sniff.py"
    spec = importlib.util.spec_from_file_location(
        "custom_components.my_leviton.utils.sniff", path
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


sniff = _load()


class ProbeHelperTests(unittest.TestCase):
    def test_resolve_default_on_puts_level_in_data_byte(self):
        arg_b, level_b = sniff._resolve_arg_level(50, None, level_in_arg=False)
        self.assertEqual(arg_b, 0x00)  # ARG_ON
        self.assertEqual(level_b, 50)

    def test_resolve_default_off(self):
        arg_b, level_b = sniff._resolve_arg_level(0, None, level_in_arg=False)
        self.assertEqual(arg_b, 0x01)  # ARG_OFF
        self.assertEqual(level_b, 0)

    def test_resolve_level_in_arg(self):
        arg_b, level_b = sniff._resolve_arg_level(50, None, level_in_arg=True)
        self.assertEqual(arg_b, 50)
        self.assertEqual(level_b, 0)

    def test_resolve_arg_override(self):
        arg_b, level_b = sniff._resolve_arg_level(40, 0x05, level_in_arg=False)
        self.assertEqual(arg_b, 0x05)
        self.assertEqual(level_b, 40)

    def test_frame_summary_status(self):
        frame = [0x7E, 0x1E, 0x03, 0x10, 0x52, 0x04, 0x32, 0x27, 0x7F]
        text = sniff._frame_summary(frame)
        self.assertIn("type=0x1e", text)
        self.assertIn("id=0x1052", text)
        self.assertIn("status", text)
        self.assertIn("level=50", text)

    def test_probe_requires_level_or_sweep(self):
        with self.assertRaises(ValueError):
            sniff.probe_level("Työhuone katto", level=None, sweep=False)


if __name__ == "__main__":
    unittest.main()
