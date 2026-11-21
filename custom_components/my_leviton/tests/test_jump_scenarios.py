"""Basic brightness jump scenarios (from → to).

These are the core cases users hit with the HA slider. Unit tests lock the
encoding + OFF-before-SET policy; live probe evidence is in DIMMING_NOTES /
probe_high_low.log (Office only).
"""

from __future__ import annotations

from unittest.mock import patch

from utils import commands
from utils.protocol import (
    ARG_OFF,
    ARG_ON,
    brightness_to_level,
    level_to_dim_arg,
    needs_off_before_level,
)

# Representative levels: off, low, mid, high-mid, near-full, full.
STEPS = (0, 6, 16, 50, 80, 96, 100)


def _arg(pkt: bytes) -> int:
    idx = list(pkt).index(0x05, 8)
    return pkt[idx + 1]


class TestNeedsOffBeforeLevelPolicy:
    """Policy table for absolute jumps while already on."""

    # (previous, target, expect_off_first)
    CASES = (
        # Basic user reports
        (96, 6, True),  # high→low stayed bright without OFF
        (100, 6, True),
        (16, 96, False),  # low→high uses ARG_ON (b14)
        (50, 96, False),
        (80, 96, False),
        # Off / full endpoints — single opcode is enough
        (96, 0, False),
        (50, 0, False),
        (0, 50, False),
        (0, 96, False),
        (0, 100, False),
        (50, 100, False),
        # Mid→mid: no forced OFF (avoids flicker on gradual slider)
        (50, 80, False),
        (80, 50, False),
        (16, 50, False),
        # Same bucket / no previous
        (50, 50, False),
        (None, 6, False),
        (None, 96, False),
    )

    def test_policy_matrix(self):
        for prev, target, expect in self.CASES:
            got = needs_off_before_level(prev, target)
            assert got is expect, f"{prev}→{target}: got {got}, want {expect}"

    def test_every_full_to_mid_step_requires_off(self):
        for prev in (95, 96, 99, 100):
            for target in (6, 10, 16, 25, 50, 70, 80, 90):
                assert needs_off_before_level(prev, target) is True, (
                    f"{prev}→{target}"
                )

    def test_mid_to_full_never_requires_off(self):
        for prev in (6, 16, 50, 80, 90):
            for target in (95, 96, 100):
                assert needs_off_before_level(prev, target) is False, (
                    f"{prev}→{target}"
                )


class TestJumpArgEncoding:
    """Every basic step maps to a safe arg (never 0x10)."""

    def test_step_args(self):
        expected = {
            0: ARG_OFF,
            6: 7,
            16: 8,
            50: 11,
            80: 14,
            96: ARG_ON,
            100: ARG_ON,
        }
        for level, arg in expected.items():
            assert level_to_dim_arg(level) == arg, f"level={level}"

    def test_all_levels_never_emit_0x10(self):
        for level in range(0, 101):
            assert level_to_dim_arg(level) != 0x10

    def test_ha_brightness_corners(self):
        # ~16% and ~96% HA brightness → levels used in user reports
        assert brightness_to_level(41) == 16  # 41/255≈16.1%
        assert brightness_to_level(245) == 96


class TestJumpCommandSequence:
    """USB write sequence for basic from→to jumps."""

    def _light(self):
        from utils.constants import LIGHTS

        return next(L for L in LIGHTS if L.get("dimmer"))

    @patch("utils.commands.time.sleep")
    @patch("utils.commands.send_bytes_once", return_value=True)
    def test_high_to_low_sends_off_then_mid(self, mock_send, _sleep):
        light = self._light()
        ok = commands.send_light_level(
            light["name"], 6, previous_level=96
        )
        assert ok is True
        assert mock_send.call_count == 2
        off_pkt = mock_send.call_args_list[0][0][2]
        set_pkt = mock_send.call_args_list[1][0][2]
        assert _arg(off_pkt) == ARG_OFF
        assert _arg(set_pkt) == level_to_dim_arg(6) == 7
        _sleep.assert_called_once()

    @patch("utils.commands.time.sleep")
    @patch("utils.commands.send_bytes_once", return_value=True)
    def test_low_to_high_single_arg_on(self, mock_send, _sleep):
        light = self._light()
        ok = commands.send_light_level(
            light["name"], 96, previous_level=16
        )
        assert ok is True
        assert mock_send.call_count == 1
        assert _arg(mock_send.call_args[0][2]) == ARG_ON
        _sleep.assert_not_called()

    @patch("utils.commands.time.sleep")
    @patch("utils.commands.send_bytes_once", return_value=True)
    def test_mid_to_mid_single_write(self, mock_send, _sleep):
        light = self._light()
        commands.send_light_level(light["name"], 80, previous_level=50)
        assert mock_send.call_count == 1
        assert _arg(mock_send.call_args[0][2]) == level_to_dim_arg(80)
        _sleep.assert_not_called()

    @patch("utils.commands.time.sleep")
    @patch("utils.commands.send_bytes_once", return_value=True)
    def test_ha_brightness_96_to_6_sequence(self, mock_send, _sleep):
        """User path: slider ~96% → ~6% via brightness 0-255."""
        light = self._light()
        prev_b = 245  # → level 96
        new_b = 15  # 15/255≈6%
        assert brightness_to_level(prev_b) == 96
        assert brightness_to_level(new_b) == 6
        commands.send_light_brightness(
            light["name"], new_b, previous_brightness=prev_b
        )
        assert mock_send.call_count == 2
        assert _arg(mock_send.call_args_list[0][0][2]) == ARG_OFF
        assert _arg(mock_send.call_args_list[1][0][2]) == 7

    @patch("utils.commands.time.sleep")
    @patch("utils.commands.send_bytes_once", return_value=True)
    def test_full_matrix_write_counts(self, mock_send, _sleep):
        """All STEPS×STEPS: write count matches needs_off_before_level."""
        light = self._light()
        for prev in STEPS:
            for target in STEPS:
                mock_send.reset_mock()
                _sleep.reset_mock()
                commands.send_light_level(
                    light["name"], target, previous_level=prev
                )
                expect_off = needs_off_before_level(prev, target)
                expect_n = 2 if expect_off else 1
                assert mock_send.call_count == expect_n, (
                    f"{prev}→{target}: writes={mock_send.call_count}"
                )
                final = mock_send.call_args_list[-1][0][2]
                assert _arg(final) == level_to_dim_arg(target), (
                    f"{prev}→{target} final arg"
                )
