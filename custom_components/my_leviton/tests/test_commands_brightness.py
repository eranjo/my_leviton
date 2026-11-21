"""Commands path: brightness → level → SET packet (USB mocked)."""

from __future__ import annotations

import logging
from unittest.mock import patch

from utils import commands
from utils.protocol import (
    ARG_ON,
    ARG_ON_LAST,
    FADE_LEVEL,
    brightness_to_level,
    build_set_level_command,
    level_to_dim_arg,
)

logging.getLogger("utils.commands").setLevel(logging.CRITICAL)


class TestSendLightBrightness:
    @patch("utils.commands.send_bytes_once", return_value=True)
    def test_mid_brightness_uses_dim_arg_not_level_byte(self, mock_send):
        from utils.constants import LIGHTS

        light = next(L for L in LIGHTS if L.get("dimmer"))
        ok = commands.send_light_brightness(light["name"], 128)
        assert ok is True
        data = mock_send.call_args[0][2]
        expected = build_set_level_command(
            light["type"],
            light["channel"],
            light["device_id"],
            brightness_to_level(128),
            fade=FADE_LEVEL,
        )
        assert data == expected
        idx = list(data).index(0x05, 8)
        assert data[idx + 1] == level_to_dim_arg(50)
        assert data[idx + 2] == FADE_LEVEL
        assert data[idx + 3] == 0

    @patch("utils.commands.send_bytes_once", return_value=True)
    def test_near_full_brightness_uses_arg_on(self, mock_send):
        """HA ~96% → level 96 → ARG_ON (not 0x10) so jumps while on work."""
        from utils.constants import LIGHTS

        light = next(L for L in LIGHTS if L.get("dimmer"))
        # 245/255 ≈ 96%
        commands.send_light_brightness(light["name"], 245)
        data = mock_send.call_args[0][2]
        idx = list(data).index(0x05, 8)
        assert brightness_to_level(245) == 96
        assert data[idx + 1] == ARG_ON
        assert data[idx + 3] == 0

    @patch("utils.commands.send_bytes_once", return_value=True)
    def test_plain_on_uses_last_level_opcode(self, mock_send):
        from utils.constants import LIGHTS

        light = next(L for L in LIGHTS if L.get("dimmer"))
        assert commands.send_light_command(light["name"], "on") is True
        data = mock_send.call_args[0][2]
        idx = list(data).index(0x05, 8)
        assert data[idx + 1] == ARG_ON_LAST
        assert data[idx + 3] == 0

    @patch("utils.commands.send_bytes_once", return_value=True)
    def test_unknown_light_returns_false(self, mock_send):
        assert commands.send_light_brightness("not-a-real-light", 128) is False
        mock_send.assert_not_called()

    @patch("utils.commands.send_bytes_once", return_value=True)
    def test_non_dimmer_rejects_level(self, mock_send):
        from utils.constants import LIGHTS

        relay = next((L for L in LIGHTS if not L.get("dimmer")), None)
        if relay is None:
            return
        assert commands.send_light_level(relay["name"], 50) is False
        mock_send.assert_not_called()
