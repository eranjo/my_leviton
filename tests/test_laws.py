"""Laws that can be checked without USB hardware or Home Assistant.

Each test below was observed failing before the assertion was trusted.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "custom_components" / "my_leviton"

OFFICE = {
    "name": "Office Ceiling",
    "type": 0x1E,
    "channel": 0x03,
    "device_id": 0xA201,
    "dimmer": True,
}
OFFICE_UID = "my_leviton:001e:03:a201"
OTHER = {
    "name": "Office Ceiling",
    "type": 0x1E,
    "channel": 0x03,
    "device_id": 0xA202,
    "dimmer": True,
}


def _stub_home_assistant() -> None:
    """Fill only the Home Assistant names this package imports in unit tests."""

    def _mod(name: str, *, package: bool = False) -> types.ModuleType:
        existing = sys.modules.get(name)
        if isinstance(existing, types.ModuleType):
            if package and not hasattr(existing, "__path__"):
                existing.__path__ = []
            return existing
        mod = types.ModuleType(name)
        if package:
            mod.__path__ = []
        sys.modules[name] = mod
        return mod

    ha = _mod("homeassistant", package=True)
    _mod("homeassistant.components", package=True)
    _mod("homeassistant.helpers", package=True)

    class ColorMode:
        BRIGHTNESS = "brightness"
        ONOFF = "onoff"

    class LightEntity:
        def async_write_ha_state(self) -> None:
            return None

    light_mod = _mod("homeassistant.components.light")
    light_mod.ATTR_BRIGHTNESS = getattr(light_mod, "ATTR_BRIGHTNESS", "brightness")
    light_mod.ColorMode = getattr(light_mod, "ColorMode", ColorMode)
    light_mod.LightEntity = getattr(light_mod, "LightEntity", LightEntity)

    const_mod = _mod("homeassistant.const")
    const_mod.EVENT_HOMEASSISTANT_STARTED = getattr(
        const_mod, "EVENT_HOMEASSISTANT_STARTED", "homeassistant_started"
    )

    class CoreState:
        running = "running"

    def callback(func):
        return func

    core = _mod("homeassistant.core")
    core.CoreState = getattr(core, "CoreState", CoreState)
    core.callback = getattr(core, "callback", callback)
    core.HomeAssistant = getattr(core, "HomeAssistant", object)
    core.ServiceCall = getattr(core, "ServiceCall", object)

    entries = _mod("homeassistant.config_entries")
    entries.ConfigEntry = getattr(entries, "ConfigEntry", object)

    class DeviceInfo(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

    entity = _mod("homeassistant.helpers.entity")
    entity.DeviceInfo = getattr(entity, "DeviceInfo", DeviceInfo)

    util = _mod("homeassistant.util")
    if not hasattr(util, "slugify"):

        def slugify(text: str) -> str:
            return text.lower().replace(" ", "_")

        util.slugify = slugify

    er = _mod("homeassistant.helpers.entity_registry")
    if not hasattr(er, "async_get"):
        er.async_get = lambda hass: None

    ha.core = core
    ha.const = const_mod
    ha.components = sys.modules["homeassistant.components"]
    ha.helpers = sys.modules["homeassistant.helpers"]


def _stub_usb() -> None:
    if "usb.core" in sys.modules and hasattr(sys.modules["usb.core"], "USBError"):
        return
    usb = types.ModuleType("usb")
    usb_core = types.ModuleType("usb.core")
    usb_util = types.ModuleType("usb.util")
    usb_core.USBError = type("USBError", (Exception,), {})
    usb_core.find = lambda **_kwargs: None
    usb_util.dispose_resources = lambda *_args, **_kwargs: None
    usb.core = usb_core
    usb.util = usb_util
    sys.modules["usb"] = usb
    sys.modules["usb.core"] = usb_core
    sys.modules["usb.util"] = usb_util


def _load(name: str, rel: str, *, package: bool = False):
    existing = sys.modules.get(name)
    if existing is not None and getattr(existing, "__file__", None):
        return existing
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    if package:
        mod.__path__ = [str(path.parent)]
    sys.modules[name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    parent_name, _, attr = name.rpartition(".")
    parent = sys.modules.get(parent_name)
    if parent is not None:
        setattr(parent, attr, mod)
    return mod


def _load_stack():
    _stub_home_assistant()
    _stub_usb()

    cc = sys.modules.get("custom_components")
    if cc is None:
        cc = types.ModuleType("custom_components")
        cc.__path__ = [str(ROOT / "custom_components")]
        sys.modules["custom_components"] = cc
    pkg = sys.modules.get("custom_components.my_leviton")
    if pkg is None:
        pkg = types.ModuleType("custom_components.my_leviton")
        sys.modules["custom_components.my_leviton"] = pkg
    pkg.__path__ = [str(PKG)]
    cc.my_leviton = pkg

    _load(
        "custom_components.my_leviton.const",
        "custom_components/my_leviton/const.py",
    )
    utils = sys.modules.get("custom_components.my_leviton.utils")
    if utils is None:
        utils = _load(
            "custom_components.my_leviton.utils",
            "custom_components/my_leviton/utils/__init__.py",
            package=True,
        )
    utils.__path__ = [str(PKG / "utils")]
    pkg.utils = utils

    constants = _load(
        "custom_components.my_leviton.utils.constants",
        "custom_components/my_leviton/utils/constants.py",
    )
    locks = _load(
        "custom_components.my_leviton.utils.locks",
        "custom_components/my_leviton/utils/locks.py",
    )
    protocol = _load(
        "custom_components.my_leviton.utils.protocol",
        "custom_components/my_leviton/utils/protocol.py",
    )
    _load(
        "custom_components.my_leviton.utils.usb_utils",
        "custom_components/my_leviton/utils/usb_utils.py",
    )
    sniff = _load(
        "custom_components.my_leviton.utils.sniff",
        "custom_components/my_leviton/utils/sniff.py",
    )
    status = _load(
        "custom_components.my_leviton.utils.status",
        "custom_components/my_leviton/utils/status.py",
    )
    commands = _load(
        "custom_components.my_leviton.utils.commands",
        "custom_components/my_leviton/utils/commands.py",
    )
    discovery = _load(
        "custom_components.my_leviton.utils.discovery",
        "custom_components/my_leviton/utils/discovery.py",
    )
    _load(
        "custom_components.my_leviton.entity_cleanup",
        "custom_components/my_leviton/entity_cleanup.py",
    )
    if not hasattr(pkg, "turn_light_on"):
        spec = importlib.util.spec_from_file_location(
            "custom_components.my_leviton",
            PKG / "__init__.py",
        )
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(pkg)
    light = _load(
        "custom_components.my_leviton.light",
        "custom_components/my_leviton/light.py",
    )
    return constants, locks, protocol, sniff, status, commands, discovery, light


constants, locks, protocol, sniff, status, commands, discovery, light = _load_stack()


def _status_frame(device_id: int, level: int, *, name: str | None = None) -> list[int]:
    frame = [0x7E, 0x1E, 0x03, (device_id >> 8) & 0xFF, device_id & 0xFF, 0x04, level]
    if name is not None:
        frame.append(0x7C)
        frame.extend(name.encode("ascii"))
    frame.append(0x7F)
    return frame


class _RegEntry:
    def __init__(self, entity_id: str, unique_id: str) -> None:
        self.entity_id = entity_id
        self.unique_id = unique_id
        self.platform = "my_leviton"
        self.domain = "light"
        self.supported_features = 1
        self.original_name = "Office Ceiling"
        self.disabled_by = None


class _Registry:
    def __init__(self, entries: list[_RegEntry]) -> None:
        self.entities = {entry.entity_id: entry for entry in entries}


class LawTests(unittest.TestCase):
    def test_vid_pid_is_omni_bus_interface(self) -> None:
        self.assertEqual(constants.VID, 0x04D8)
        self.assertEqual(constants.PID, 0xFE93)
        source = (PKG / "utils" / "sniff.py").read_text(encoding="utf-8")
        self.assertIn("idVendor=VID", source)
        self.assertIn("idProduct=PID", source)

    def test_status_and_commands_share_one_usb_lock(self) -> None:
        self.assertIs(commands._USB_LOCK, locks.initialize_usb_lock())
        self.assertIs(locks.initialize_usb_lock(), locks.USB_LOCK)

    def test_waiting_set_preempts_listen_between_reads(self) -> None:
        status._CMD_MONO = 0.0
        reads = {"n": 0}
        original_open = status._open_usb
        original_poll = status._poll
        original_read = status._read_raw

        class _Usb:
            def __enter__(self):
                return object()

            def __exit__(self, *_args):
                return False

        def _read(_dev, timeout_ms=100):
            del timeout_ms
            reads["n"] += 1
            if reads["n"] >= 2:
                status.mark_command_activity()
            time.sleep(0.02)
            return None

        status._open_usb = lambda: _Usb()
        status._poll = lambda _dev: None
        status._read_raw = _read
        try:
            started = time.monotonic()
            status._collect_levels(1.2, abort_on_command=True)
            elapsed = time.monotonic() - started
        finally:
            status._open_usb = original_open
            status._poll = original_poll
            status._read_raw = original_read
            status._CMD_MONO = 0.0
        self.assertGreaterEqual(reads["n"], 2)
        self.assertLess(elapsed, 0.7)

    def test_readback_quiet_gap_stays_near_0_45s(self) -> None:
        self.assertGreaterEqual(light._READBACK_QUIET_SEC, 0.35)
        self.assertLessEqual(light._READBACK_QUIET_SEC, 0.55)

    def test_older_status_does_not_clobber_recent_set(self) -> None:
        entity = light.LevitonLight(
            "Office Ceiling", dimmer=True, unique_id=OFFICE_UID
        )
        entity.async_write_ha_state = lambda: None
        entity._is_on = True
        entity._brightness = 255
        entity._local_cmd_mono = time.monotonic()
        self.assertFalse(entity.apply_bus_level(0))
        self.assertTrue(entity.is_on)
        self.assertEqual(entity.brightness, 255)
        entity._local_cmd_mono = time.monotonic() - (light._LOCAL_CMD_GUARD_SEC + 0.5)
        self.assertTrue(entity.apply_bus_level(0))
        self.assertFalse(entity.is_on)

    def test_reported_percent_is_on_only_above_zero(self) -> None:
        self.assertEqual(status.level_to_entity_state(0, dimmer=True), (False, 0))
        self.assertEqual(status.level_to_entity_state(0, dimmer=False), (False, None))
        on, brightness = status.level_to_entity_state(1, dimmer=True)
        self.assertTrue(on)
        self.assertGreater(brightness, 0)
        on_full, full = status.level_to_entity_state(100, dimmer=True)
        self.assertTrue(on_full)
        self.assertEqual(full, 255)

    def test_missing_samples_are_not_off(self) -> None:
        self.assertIsNone(status.settle_reported_level([]))
        self.assertIsNone(status.settle_reported_level([None, None]))
        self.assertNotEqual(status.settle_reported_level([]), 0)

    def test_command_byte_zero_is_not_status_off(self) -> None:
        self.assertEqual(protocol.ARG_ON, 0x00)
        self.assertEqual(protocol.ARG_OFF, 0x01)
        self.assertEqual(protocol.level_to_dim_arg(0), protocol.ARG_OFF)
        self.assertEqual(protocol.level_to_dim_arg(100), protocol.ARG_ON)
        on_packet = protocol.build_set_command(0x1E, 0x03, 0xA201, protocol.ARG_ON)
        frame = list(on_packet[on_packet.index(0x7E) :])
        self.assertIsNone(sniff._level_from_frame(frame))
        self.assertEqual(on_packet[13], 0x00)
        status_off = _status_frame(0xA201, 0)
        self.assertEqual(sniff._level_from_frame(status_off), 0)
        self.assertFalse(status.level_to_entity_state(0, dimmer=True)[0])

    def test_ha_dimmer_on_without_brightness_is_full_on(self) -> None:
        sent: list[bytes] = []
        original = commands.send_bytes_once

        def _send(vid, pid, data, endpoint=0x01, timeout_ms=1000):
            del endpoint, timeout_ms
            self.assertEqual((vid, pid), (constants.VID, constants.PID))
            sent.append(bytes(data))
            return True

        commands.send_bytes_once = _send
        try:
            entity = light.LevitonLight(
                "Office Ceiling", dimmer=True, unique_id=OFFICE_UID
            )
            entity.async_write_ha_state = lambda: None
            entity._last_brightness = protocol.level_to_brightness(40)
            entity._brightness = entity._last_brightness
            entity._is_on = False

            class _Hass:
                async def async_add_executor_job(self, fn, *args):
                    return fn(*args)

                def async_create_task(self, coro):
                    coro.close()
                    return None

            entity.hass = _Hass()
            asyncio.run(entity.async_turn_on())
        finally:
            commands.send_bytes_once = original
            status._CMD_MONO = 0.0
        self.assertEqual(len(sent), 1)
        expected = protocol.build_set_command(0x1E, 0x03, 0xA201, protocol.ARG_ON)
        self.assertEqual(sent[0], expected)
        self.assertNotEqual(sent[0][13], protocol.ARG_ON_LAST)
        self.assertEqual(entity.brightness, 255)
        self.assertTrue(entity.is_on)

    def test_status_level_is_not_rewritten_to_full(self) -> None:
        on, brightness = status.level_to_entity_state(40, dimmer=True)
        self.assertTrue(on)
        self.assertEqual(brightness, protocol.level_to_brightness(40))
        self.assertNotEqual(brightness, 255)

    def test_unique_id_omits_display_name(self) -> None:
        uid = constants.address_unique_id(OFFICE)
        self.assertEqual(uid, OFFICE_UID)
        self.assertNotIn("Office Ceiling", uid or "")
        self.assertNotIn("office", uid or "")
        self.assertNotEqual(uid, constants.legacy_name_unique_id("Office Ceiling"))

    def test_crc8_maxim_matches_vectors(self) -> None:
        # Published CRC-8/MAXIM check value for "123456789".
        self.assertEqual(protocol.crc8_maxim(b"123456789"), 0xA1)
        # Checksum byte from the captured Office Ceiling on-packet vector.
        payload = [
            0x25,
            0x0D,
            0xBC,
            0x00,
            0x1E,
            0x03,
            0xA2,
            0x01,
            0x05,
            0x00,
            0x03,
            0x00,
            0x00,
            0x00,
        ]
        self.assertEqual(protocol.crc8_maxim(payload), 145)

    def test_store_is_not_replaced_by_examples(self) -> None:
        source, devices, save = constants.choose_setup_devices(
            [dict(OFFICE)], None, False, None, False, False
        )
        self.assertEqual(source, "store")
        self.assertFalse(save)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["device_id"], OFFICE["device_id"])

    def test_existing_entities_withhold_examples(self) -> None:
        source, devices, save = constants.choose_setup_devices(
            None, None, False, None, False, True
        )
        self.assertEqual(source, "withheld_examples")
        self.assertEqual(devices, [])
        self.assertFalse(save)
        empty_source, empty_devices, _empty_save = constants.choose_setup_devices(
            None, None, False, None, False, False
        )
        self.assertEqual(empty_source, "examples")
        self.assertTrue(empty_devices)

    def test_preserved_entities_are_not_sample_lights(self) -> None:
        saved_lights = [dict(row) for row in constants.LIGHTS]
        saved_source = constants.INVENTORY_SOURCE
        registry = _Registry(
            [_RegEntry("light.office_ceiling", "my_leviton::Office Ceiling")]
        )
        er = sys.modules["homeassistant.helpers.entity_registry"]
        original_get = er.async_get
        er.async_get = lambda _hass: registry
        try:
            constants._set_lights([])
            constants.INVENTORY_SOURCE = "withheld_examples"
            entities = light._entities_for_inventory(object(), constants)
        finally:
            constants._set_lights(saved_lights)
            constants.INVENTORY_SOURCE = saved_source
            er.async_get = original_get
        unique_ids = [entity._attr_unique_id for entity in entities]
        self.assertIn("my_leviton::Office Ceiling", unique_ids)
        self.assertNotIn(OFFICE_UID, unique_ids)

    def test_frame_does_not_match_a_different_address(self) -> None:
        frame = _status_frame(OFFICE["device_id"], 40)
        self.assertTrue(sniff._match_light(frame, OFFICE))
        self.assertFalse(sniff._match_light(frame, OTHER))
        self.assertEqual(sniff._level_from_frame(frame), 40)
        other = _status_frame(OTHER["device_id"], 10)
        self.assertFalse(sniff._match_light(other, OFFICE))
        self.assertEqual(sniff._level_from_frame(other), 10)

    def test_glued_frame_does_not_carry_the_other_name(self) -> None:
        glued = _status_frame(OFFICE["device_id"], 40)[:-1]
        glued.extend(_status_frame(OTHER["device_id"], 10, name="Office Ceiling")[1:])
        self.assertIsNone(sniff._bus_name_from_frame(glued))
        own = _status_frame(OFFICE["device_id"], 40, name="Office Ceiling")
        self.assertEqual(sniff._bus_name_from_frame(own), "Office Ceiling")
        self.assertEqual(
            sniff._prefer_longer_name("Office Ceiling", "WireLabelLonger"),
            "Office Ceiling",
        )

    def test_stored_address_keeps_entity_id(self) -> None:
        bus = discovery.BusDevice(
            type=0x1E,
            channel=0x03,
            device_id=0xA201,
            last_level=40,
            frame_count=3,
        )
        registry = [
            discovery.RegistryLight(
                entity_id="light.office_ceiling",
                unique_id=OFFICE_UID,
                name="Office Ceiling",
            )
        ]
        matched = discovery.correlate_devices([bus], [dict(OFFICE)], registry)
        kept = [row for row in matched if row.device_id == OFFICE["device_id"]]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].entity_id, "light.office_ceiling")
        self.assertFalse(kept[0].is_new)

    def test_no_stored_address_does_not_reuse_entity_id(self) -> None:
        bus = discovery.BusDevice(
            type=0x1E,
            channel=0x03,
            device_id=0xA201,
            last_level=40,
            frame_count=3,
        )
        registry = [
            discovery.RegistryLight(
                entity_id="light.office_ceiling",
                unique_id="my_leviton::Office Ceiling",
                name="Office Ceiling",
            )
        ]
        matched = discovery.correlate_devices([bus], [], registry)
        self.assertTrue(matched)
        self.assertTrue(all(row.entity_id != "light.office_ceiling" for row in matched))
        self.assertTrue(all(row.is_new for row in matched))

    def test_private_inventory_stays_out_of_the_public_repo(self) -> None:
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        ignored = {line.strip() for line in ignore.splitlines() if line.strip()}
        self.assertIn("**/constants_local.py", ignored)
        self.assertIn("**/my_leviton_lights.yaml", ignored)
        self.assertFalse((PKG / "utils" / "constants_local.py").exists())
        self.assertFalse((ROOT / "my_leviton_lights.yaml").exists())


if __name__ == "__main__":
    unittest.main()
