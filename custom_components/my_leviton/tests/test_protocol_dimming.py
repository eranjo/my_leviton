"""Unit tests for VRUSB dimmer SET encoding (1.1.0b14).

Live probes (Office Ceiling) established:
- arg 0x00 = full 100%; 0x01 = off; 0x0A = last-level ON
- ARG_ON + percent in level data byte forced ~100% (b7–b12 bug)
- mid-levels use arg = 6 + round(level/10) (~10% steps; 13→70 … 15→90)
- arg 0x10 (~95–99%) is ignored when already on; use ARG_ON instead (b14)
"""

from __future__ import annotations

from utils.protocol import (
    ARG_OFF,
    ARG_ON,
    ARG_ON_LAST,
    FADE_LEVEL,
    FADE_ON_OFF,
    LEVEL_MAX,
    LEVEL_MIN,
    brightness_to_level,
    build_set_command,
    build_set_level_command,
    crc8_maxim,
    is_dimmer_type,
    level_to_dim_arg,
    parse_address_from_packet,
)

TYOHUONE = {"device_type": 0x1E, "channel": 0x03, "device_id": 0xA204}

# Golden TX: Office level 80 → arg 14 (0x0e), level_byte 0
PROBE_TX_LEVEL_80 = bytes.fromhex(
    "02 12 01 7e 25 0d bc 00 1e 03 10 52 05 0e 03 00 00 00 a0 7f 8f"
)


def _fields(pkt: bytes):
    idx = list(pkt).index(0x05, 8)
    return pkt[idx + 1], pkt[idx + 2], pkt[idx + 3]


class TestCrc8Maxim:
    def test_level_80_checksum(self):
        payload = bytes.fromhex("25 0d bc 00 1e 03 10 52 05 0e 03 00 00 00")
        assert crc8_maxim(payload) == PROBE_TX_LEVEL_80[-3]


class TestFadeRegression:
    def test_default_fade_is_non_zero(self):
        assert FADE_LEVEL == 0x03
        assert FADE_ON_OFF == 0x03

    def test_set_level_packet_uses_fade_03(self):
        arg, fade, level_b = _fields(build_set_level_command(**TYOHUONE, level=80))
        assert fade == 0x03
        assert level_b == 0
        assert arg == 14


class TestLevelToDimArg:
    def test_off_and_full(self):
        assert level_to_dim_arg(0) == ARG_OFF
        assert level_to_dim_arg(100) == ARG_ON

    def test_ten_percent_steps(self):
        # reported ≈ (arg - 6) * 10 for args 13..15 in live probes
        assert level_to_dim_arg(70) == 13
        assert level_to_dim_arg(80) == 14
        assert level_to_dim_arg(90) == 15
        assert level_to_dim_arg(50) == 11
        assert level_to_dim_arg(25) == 9

    def test_near_full_uses_arg_on_not_0x10(self):
        """Regression b14: arg 0x10 ignored when already on (16→96 / 80→96)."""
        for level in (95, 96, 99):
            assert level_to_dim_arg(level) == ARG_ON, f"level={level}"
            arg, _, level_b = _fields(
                build_set_level_command(**TYOHUONE, level=level)
            )
            assert arg == ARG_ON
            assert level_b == 0

    def test_never_emits_arg_0x10(self):
        for level in range(0, 101):
            assert level_to_dim_arg(level) != 0x10, f"level={level}"

    def test_never_uses_arg_on_with_mid_percent_as_level_byte(self):
        """Regression: ARG_ON + level_byte=percent forced ~100%."""
        for level in (1, 25, 50, 75, 90):
            arg, fade, level_b = _fields(
                build_set_level_command(**TYOHUONE, level=level)
            )
            assert level_b == 0, f"level={level}"
            assert arg == level_to_dim_arg(level)
            assert not (arg == ARG_ON and level_b == level)


class TestBuildSetLevelCommand:
    def test_level_80_matches_probe_encoding(self):
        pkt = build_set_level_command(**TYOHUONE, level=80)
        assert pkt == PROBE_TX_LEVEL_80

    def test_level_0_is_off_packet(self):
        pkt = build_set_level_command(**TYOHUONE, level=0)
        off = build_set_command(
            TYOHUONE["device_type"],
            TYOHUONE["channel"],
            TYOHUONE["device_id"],
            ARG_OFF,
            fade=FADE_LEVEL,
            level=0,
        )
        assert pkt == off
        assert _fields(pkt) == (ARG_OFF, FADE_LEVEL, 0)

    def test_level_100_is_full_on_opcode(self):
        arg, fade, level_b = _fields(build_set_level_command(**TYOHUONE, level=100))
        assert arg == ARG_ON
        assert fade == FADE_LEVEL
        assert level_b == 0

    def test_level_96_matches_full_on_packet(self):
        assert build_set_level_command(**TYOHUONE, level=96) == build_set_level_command(
            **TYOHUONE, level=100
        )

    def test_level_1_is_not_off(self):
        assert build_set_level_command(**TYOHUONE, level=1) != build_set_level_command(
            **TYOHUONE, level=0
        )
        arg, _, level_b = _fields(build_set_level_command(**TYOHUONE, level=1))
        assert arg != ARG_OFF
        assert level_b == 0

    def test_on_last_opcode_constant(self):
        assert ARG_ON_LAST == 0x0A
        assert ARG_ON_LAST != ARG_ON
        assert ARG_ON_LAST != ARG_OFF

    def test_clamps(self):
        assert build_set_level_command(**TYOHUONE, level=-5) == build_set_level_command(
            **TYOHUONE, level=0
        )
        assert build_set_level_command(**TYOHUONE, level=150) == build_set_level_command(
            **TYOHUONE, level=100
        )


class TestByteStuffing:
    def test_escape_frame_markers_in_payload(self):
        pkt = build_set_command(0x1E, 0x03, 0x7E00, ARG_ON, fade=0x03, level=0)
        assert pkt[0] == 0x02
        assert pkt[3] == 0x7E
        assert pkt[-2] == 0x7F
        assert bytes([0x7C, 0xFE]) in pkt
        assert pkt[4:-2].count(0x7E) == 0


class TestBrightnessToLevel:
    def test_off_and_full(self):
        assert brightness_to_level(0) == LEVEL_MIN
        assert brightness_to_level(255) == LEVEL_MAX

    def test_midpoint(self):
        assert brightness_to_level(128) == 50

    def test_never_zero_for_positive_brightness(self):
        for b in range(1, 256):
            level = brightness_to_level(b)
            assert 1 <= level <= 100


class TestHelpers:
    def test_is_dimmer_type(self):
        assert is_dimmer_type(0x1E)
        assert is_dimmer_type(0x2E)
        assert not is_dimmer_type(0x26)

    def test_parse_address_from_packet(self):
        addr = parse_address_from_packet(PROBE_TX_LEVEL_80)
        assert addr == {
            "type": 0x1E,
            "channel": 0x03,
            "device_id": 0xA204,
        }
