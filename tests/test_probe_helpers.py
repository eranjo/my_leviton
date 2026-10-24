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

    def test_read_light_level_waits_for_target_status(self):
        """Simulate round-robin: other devices first, then the target."""
        light = {
            "name": "Työhuone katto",
            "type": 0x1E,
            "channel": 0x03,
            "device_id": 0x1052,
            "dimmer": True,
        }
        other = [0x7E, 0x1E, 0x01, 0x00, 0x11, 0x04, 0x40, 0x00, 0x7F]
        target = [0x7E, 0x1E, 0x03, 0x10, 0x52, 0x04, 0x32, 0x00, 0x7F]
        reads = [
            other + [0xFF] * (64 - len(other)),
            other + [0xFF] * (64 - len(other)),
            target + [0xFF] * (64 - len(target)),
        ]

        class FakeDev:
            def __init__(self) -> None:
                self.writes = 0

            def write(self, _ep, _data, timeout=1000):  # noqa: ANN001
                self.writes += 1
                return len(_data)

            def read(self, _ep, _size, timeout=400):  # noqa: ANN001
                if not reads:
                    raise sniff.usb.core.USBError("timed out")
                return reads.pop(0)

        level, notes = sniff._read_light_level(
            FakeDev(),
            light,
            settle_sec=0.0,
            wait_sec=2.0,
            poll_interval_sec=0.01,
        )
        self.assertEqual(level, 50)
        self.assertTrue(any("wait_ok" in n for n in notes))
        self.assertTrue(any("level=50" in n for n in notes))

    def test_read_light_level_timeout_notes_activity(self):
        light = {
            "name": "Työhuone katto",
            "type": 0x1E,
            "channel": 0x03,
            "device_id": 0x1052,
            "dimmer": True,
        }
        other = [0x7E, 0x1E, 0x01, 0x00, 0x11, 0x04, 0x40, 0x00, 0x7F]

        class FakeDev:
            def write(self, _ep, _data, timeout=1000):  # noqa: ANN001
                return len(_data)

            def read(self, _ep, _size, timeout=400):  # noqa: ANN001
                return other + [0xFF] * (64 - len(other))

        level, notes = sniff._read_light_level(
            FakeDev(),
            light,
            settle_sec=0.0,
            wait_sec=0.6,
            poll_interval_sec=1.5,
        )
        self.assertIsNone(level)
        self.assertTrue(any("wait_timeout" in n for n in notes))
        self.assertTrue(any("frames=" in n for n in notes))
        self.assertTrue(any("seen=" in n for n in notes))
        self.assertTrue(any("0x1e/0x01/0x0011" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
