"""Unit tests for hardware→HA state sync helpers."""

from __future__ import annotations

from utils.protocol import brightness_to_level
from utils.status import (
    level_to_brightness,
    level_to_entity_state,
    settle_reported_level,
)


class TestLevelToBrightness:
    def test_endpoints(self):
        assert level_to_brightness(0) == 0
        assert level_to_brightness(100) == 255

    def test_mid_round_trip(self):
        # 70% → brightness → level should stay near 70
        b = level_to_brightness(70)
        assert 1 <= b <= 255
        assert brightness_to_level(b) == 70

    def test_floor_61(self):
        assert brightness_to_level(level_to_brightness(61)) == 61


class TestLevelToEntityState:
    def test_dimmer_off(self):
        assert level_to_entity_state(0, dimmer=True) == (False, 0)

    def test_dimmer_on(self):
        is_on, bri = level_to_entity_state(80, dimmer=True)
        assert is_on is True
        assert bri == level_to_brightness(80)

    def test_relay_off(self):
        assert level_to_entity_state(0, dimmer=False) == (False, None)

    def test_relay_on(self):
        # Relays often report 100 when on; any >0 is on, no brightness.
        assert level_to_entity_state(100, dimmer=False) == (True, None)
        assert level_to_entity_state(1, dimmer=False) == (True, None)


class TestSettleReportedLevel:
    def test_empty(self):
        assert settle_reported_level([]) is None
        assert settle_reported_level([None, None]) is None

    def test_last_wins(self):
        assert settle_reported_level([100, 80]) == 80

    def test_off_echo_then_mid(self):
        # OFF-then-SET: early 0 must not stick if later mid arrives.
        assert settle_reported_level([0, 0, 61]) == 61
        assert settle_reported_level([0, None, 70]) == 70

    def test_final_off(self):
        assert settle_reported_level([80, 0]) == 0

    def test_skips_none(self):
        assert settle_reported_level([None, 50, None]) == 50
