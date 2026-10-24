"""Offline tests for status-frame parsing helpers."""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    # Stub usb so sniff.py imports cleanly offline.
    usb = types.ModuleType("usb")
    usb_core = types.ModuleType("usb.core")
    usb_util = types.ModuleType("usb.util")
    usb_core.USBError = type("USBError", (Exception,), {})
    usb.core = usb_core
    usb.util = usb_util
    sys.modules["usb"] = usb
    sys.modules["usb.core"] = usb_core
    sys.modules["usb.util"] = usb_util

    # Load constants first
    for name, rel in (
        (
            "custom_components.my_leviton.utils.constants",
            "custom_components/my_leviton/utils/constants.py",
        ),
        (
            "custom_components.my_leviton.utils.locks",
            "custom_components/my_leviton/utils/locks.py",
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


class SniffParseTests(unittest.TestCase):
    def test_match_tyohuone_katto(self):
        light = sniff.lookup_light("Työhuone katto")
        self.assertIsNotNone(light)
        # Settled status from capture: 7E type ch id 04 LEVEL ...
        frame = [0x7E, 0x1E, 0x03, 0x10, 0x52, 0x04, 0x32, 0x27, 0x7F]
        self.assertTrue(sniff._match_light(frame, light))
        self.assertEqual(sniff._level_from_frame(frame), 0x32)

    def test_live_dim_level_not_byte6(self):
        # Physical dim stream: level is at index 10, not the 0x03 at index 6.
        frame = [
            0x7E,
            0x1E,
            0x03,
            0x10,
            0x52,
            0x22,
            0x03,
            0x0D,
            0xBB,
            0x03,
            0x40,
            0x00,
            0xE7,
            0x7F,
        ]
        self.assertEqual(sniff._level_from_frame(frame), 0x40)

    def test_reject_garbage_level(self):
        frame = [0x7E, 0x1E, 0x03, 0x10, 0x52, 0x04, 0xEF, 0x7F]
        self.assertIsNone(sniff._level_from_frame(frame))

    def test_extract_frames(self):
        raw = [0x00, 0x7E, 0x1E, 0x03, 0x10, 0x52, 0x04, 0x20, 0x7F, 0x00]
        frames = sniff._extract_frames(raw)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][0], 0x7E)
        self.assertEqual(frames[0][-1], 0x7F)


if __name__ == "__main__":
    unittest.main()
