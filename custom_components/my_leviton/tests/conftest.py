"""Test bootstrap: put integration root on path; stub pyusb if missing."""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import usb.core  # noqa: F401
except ImportError:
    usb = types.ModuleType("usb")
    usb.core = types.ModuleType("usb.core")
    usb.util = types.ModuleType("usb.util")
    usb.core.USBError = type("USBError", (Exception,), {})
    usb.core.find = lambda **kwargs: None  # type: ignore[attr-defined]
    usb.util.dispose_resources = lambda *a, **k: None  # type: ignore[attr-defined]
    sys.modules["usb"] = usb
    sys.modules["usb.core"] = usb.core
    sys.modules["usb.util"] = usb.util
