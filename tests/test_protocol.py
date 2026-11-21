"""Offline tests for CRC-8/MAXIM framing (no Home Assistant / USB required)."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "custom_components" / "my_leviton" / "utils" / "protocol.py"

spec = importlib.util.spec_from_file_location("leviton_protocol", PROTOCOL_PATH)
protocol = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(protocol)


# Example samples only (fake names + IDs). Packets rebuilt via protocol helpers.
CAPTURED = [
    # name, type, channel, device_id, on_packet, off_packet
    (
        "Sample Relay Wall",
        0x26,
        0x3,
        0xa101,
        [2, 18, 1, 126, 37, 13, 188, 0, 38, 3, 161, 1, 5, 0, 3, 0, 0, 0, 166, 127, 143],
        [2, 18, 1, 126, 37, 13, 188, 0, 38, 3, 161, 1, 5, 1, 3, 0, 0, 0, 107, 127, 143],
    ),
    (
        "Sample Dimmer A",
        0x1e,
        0x3,
        0xa201,
        [2, 18, 1, 126, 37, 13, 188, 0, 30, 3, 162, 1, 5, 0, 3, 0, 0, 0, 145, 127, 143],
        [2, 18, 1, 126, 37, 13, 188, 0, 30, 3, 162, 1, 5, 1, 3, 0, 0, 0, 92, 127, 143],
    ),
    (
        "Sample Dimmer B",
        0x1e,
        0x3,
        0xa202,
        [2, 18, 1, 126, 37, 13, 188, 0, 30, 3, 162, 2, 5, 0, 3, 0, 0, 0, 214, 127, 143],
        [2, 18, 1, 126, 37, 13, 188, 0, 30, 3, 162, 2, 5, 1, 3, 0, 0, 0, 27, 127, 143],
    ),
    (
        "Sample Relay Counter",
        0x26,
        0xc3,
        0xa102,
        [2, 18, 1, 126, 37, 13, 188, 0, 38, 195, 161, 2, 5, 0, 3, 0, 0, 0, 150, 127, 143],
        [2, 18, 1, 126, 37, 13, 188, 0, 38, 195, 161, 2, 5, 1, 3, 0, 0, 0, 91, 127, 143],
    ),
]


class ProtocolTests(unittest.TestCase):
    def test_rebuild_on_off_samples(self):
        for name, typ, ch, dev, on_pkt, off_pkt in CAPTURED:
            with self.subTest(name=name, cmd="on"):
                self.assertEqual(
                    list(protocol.build_set_command(typ, ch, dev, protocol.ARG_ON)),
                    on_pkt,
                )
            with self.subTest(name=name, cmd="off"):
                self.assertEqual(
                    list(protocol.build_set_command(typ, ch, dev, protocol.ARG_OFF)),
                    off_pkt,
                )

    def test_crc_residual_zero(self):
        payload = [0x25, 0x0D, 0xBC, 0x00, 0x1E, 0x03, 0xA2, 0x01, 0x05, 0x00, 0x03, 0x00, 0x00, 0x00]
        cs = protocol.crc8_maxim(payload)
        self.assertEqual(protocol.crc8_maxim(payload + [cs]), 0)

    def test_dimmer_levels_use_arg_encoding(self):
        """Absolute levels encode percent in the arg byte; level data stays 0."""
        packets = {
            level: protocol.build_set_level_command(0x1E, 0x03, 0xA201, level)
            for level in (0, 25, 50, 75, 100)
        }
        # Off uses classic OFF arg; level byte stays 0.
        self.assertEqual(packets[0][13], protocol.ARG_OFF)
        self.assertEqual(packets[0][15], 0)
        # Mid levels use VRUSB step args; level data byte stays 0.
        self.assertEqual(packets[50][13], protocol.level_to_dim_arg(50))
        self.assertEqual(packets[50][14], protocol.FADE_LEVEL)
        self.assertEqual(packets[50][15], 0)
        self.assertEqual(packets[100][13], protocol.ARG_ON)
        self.assertEqual(packets[100][15], 0)
        # Plain ON (restore last) still has level byte 0 — distinct mid packets.
        on_pkt = protocol.build_set_command(0x1E, 0x03, 0xA201, protocol.ARG_ON)
        self.assertEqual(on_pkt[13], protocol.ARG_ON)
        self.assertEqual(on_pkt[15], 0)
        self.assertEqual(packets[100], on_pkt)
        self.assertEqual(len(set(packets.values())), 5)

    def test_level_one_is_not_off_packet(self):
        """Regression: level-in-arg must not make 1% identical to OFF (arg=0x01)."""
        off_pkt = protocol.build_set_command(0x1E, 0x03, 0xA201, protocol.ARG_OFF)
        one_pkt = protocol.build_set_level_command(0x1E, 0x03, 0xA201, 1)
        self.assertNotEqual(list(one_pkt), list(off_pkt))
        self.assertEqual(one_pkt[13], protocol.level_to_dim_arg(1))
        self.assertEqual(one_pkt[15], 0)

    def test_brightness_mapping(self):
        self.assertEqual(protocol.brightness_to_level(0), 0)
        self.assertEqual(protocol.brightness_to_level(255), 100)
        self.assertEqual(protocol.brightness_to_level(128), 50)

if __name__ == "__main__":
    unittest.main()
