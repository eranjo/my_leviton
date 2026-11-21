"""Absolute level sweep packet shape (VRUSB arg encoding, 1.1.0b14)."""

from __future__ import annotations

from utils.protocol import (
    ARG_OFF,
    ARG_ON,
    FADE_LEVEL,
    build_set_level_command,
    crc8_maxim,
    level_to_dim_arg,
)

DEVICE = (0x1E, 0x03, 0xA204)
SWEEP = (0, 25, 50, 75, 100)


def _cmd_fields(pkt: bytes):
    idx = list(pkt).index(0x05, 8)
    return pkt[idx + 1], pkt[idx + 2], pkt[idx + 3]


class TestLevelSweepPackets:
    def test_sweep_arg_fade_level_bytes(self):
        for level in SWEEP:
            pkt = build_set_level_command(*DEVICE, level)
            arg, fade, level_b = _cmd_fields(pkt)
            assert fade == FADE_LEVEL, f"level={level}"
            assert level_b == 0, f"level={level}: data byte must stay 0"
            assert arg == level_to_dim_arg(level)
            if level == 0:
                assert arg == ARG_OFF
            elif level == 100:
                assert arg == ARG_ON

    def test_sweep_packets_are_distinct(self):
        packets = [build_set_level_command(*DEVICE, lv) for lv in SWEEP]
        assert len(set(packets)) == len(SWEEP)

    def test_sweep_crc_matches_unstuffed_payload(self):
        for level in SWEEP:
            pkt = build_set_level_command(*DEVICE, level)
            assert pkt[2] == 0x01 and pkt[3] == 0x7E and pkt[-2] == 0x7F
            inner = list(pkt[4:-2])
            *payload, cs = inner
            assert crc8_maxim(payload) == cs, f"level={level}"

    def test_mid_brightness_path_hits_sweep_point(self):
        from utils.protocol import brightness_to_level

        assert brightness_to_level(128) == 50
        assert build_set_level_command(*DEVICE, 50) == build_set_level_command(
            *DEVICE, brightness_to_level(128)
        )
