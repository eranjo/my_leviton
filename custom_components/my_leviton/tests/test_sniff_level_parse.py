"""Tests for status/live frame level parsing and probe arg layout."""

from __future__ import annotations

from utils.protocol import ARG_OFF, ARG_ON
from utils.sniff import (
    _clamp_level,
    _extract_frames,
    _level_from_frame,
    _match_light,
    _resolve_arg_level,
)

# From probe log RX: status reported 100 after SET 50
PROBE_RX_STATUS_100 = bytes.fromhex("7e 1e 03 10 52 04 64 27 21 13 83 00 1f 02")

# Captured live-dim shape from sniff.py comments:
# 7e type ch id_hi id_lo 22 03 0d bb 03 LEVEL 00 ...
LIVE_LEVEL_72 = bytes.fromhex("7e 1e 03 10 52 22 03 0d bb 03 48 00")

# Glued command-echo + status (sniff handles 25 0d bc 00 04 LEVEL)
GLUED_STATUS_50 = bytes.fromhex("7e 1e 03 10 52 25 0d bc 00 04 32 00")

TYOHUONE = {"type": 0x1E, "channel": 0x03, "device_id": 0xA204, "name": "Office Ceiling"}


class TestClampLevel:
    def test_accepts_0_to_100(self):
        assert _clamp_level(0) == 0
        assert _clamp_level(50) == 50
        assert _clamp_level(100) == 100

    def test_rejects_garbage(self):
        assert _clamp_level(101) is None
        assert _clamp_level(255) is None
        assert _clamp_level(-1) is None


class TestLevelFromFrame:
    def test_status_frame_from_probe_rx(self):
        assert _level_from_frame(PROBE_RX_STATUS_100) == 100

    def test_live_dim_frame(self):
        assert _level_from_frame(LIVE_LEVEL_72) == 72

    def test_glued_command_echo_status(self):
        assert _level_from_frame(GLUED_STATUS_50) == 50

    def test_short_frame(self):
        assert _level_from_frame([0x7E, 0x1E]) is None

    def test_rejects_status_level_over_100(self):
        bad = list(PROBE_RX_STATUS_100)
        bad[6] = 0xC8  # 200
        assert _level_from_frame(bad) is None


class TestMatchLight:
    def test_matches_office(self):
        assert _match_light(PROBE_RX_STATUS_100, TYOHUONE)

    def test_rejects_other_id(self):
        other = {**TYOHUONE, "device_id": 0x9999}
        assert not _match_light(PROBE_RX_STATUS_100, other)


class TestExtractFrames:
    def test_splits_on_7e_7f(self):
        raw = [0xFF, 0x7E, 0x1E, 0x03, 0x10, 0x52, 0x04, 0x32, 0x7F, 0x00]
        frames = _extract_frames(raw)
        assert len(frames) == 1
        assert frames[0][0] == 0x7E
        assert frames[0][-1] == 0x7F


class TestResolveArgLevel:
    """Default layout vs broken level_in_arg (percent in arg)."""

    def test_default_on_puts_level_in_data_byte(self):
        assert _resolve_arg_level(50, None, level_in_arg=False) == (ARG_ON, 50)

    def test_default_off(self):
        assert _resolve_arg_level(0, None, level_in_arg=False) == (ARG_OFF, 0)

    def test_arg_override_keeps_level_byte(self):
        assert _resolve_arg_level(50, 0x00, level_in_arg=False) == (0x00, 50)

    def test_level_in_arg_makes_level_1_look_like_off(self):
        # Documents the broken encoding: arg=1 is ARG_OFF.
        arg, level_b = _resolve_arg_level(1, None, level_in_arg=True)
        assert arg == 1 == ARG_OFF
        assert level_b == 0

    def test_level_in_arg_puts_percent_in_arg(self):
        assert _resolve_arg_level(50, None, level_in_arg=True) == (50, 0)
