#!/usr/bin/env python3
"""Stdlib test runner (no pytest). Logs to my_leviton/test_output.log only.

Usage::

    python3 tests/run_tests.py
"""

from __future__ import annotations

import importlib
import inspect
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "test_output.log"
TESTS_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS_DIR))
import conftest  # noqa: E402,F401 — path + optional usb stub


MODULES = (
    "test_protocol_dimming",
    "test_sniff_level_parse",
    "test_commands_brightness",
    "test_level_sweep",
    "test_probe_csv",
    "test_jump_scenarios",
    "test_status_sync",
)


def _iter_cases():
    for mod_name in MODULES:
        mod = importlib.import_module(mod_name)
        for cls_name, cls in inspect.getmembers(mod, inspect.isclass):
            if cls.__module__ != mod.__name__ or not cls_name.startswith("Test"):
                continue
            instance = cls()
            for meth_name, _ in inspect.getmembers(cls, predicate=inspect.isfunction):
                if not meth_name.startswith("test_"):
                    continue
                yield f"{mod_name}.{cls_name}.{meth_name}", getattr(instance, meth_name)


def main() -> int:
    lines = [f"my_leviton dimming unit tests (root={ROOT})", "=" * 60]
    passed = failed = errored = 0

    for qual, fn in _iter_cases():
        try:
            fn()
            passed += 1
            lines.append(f"PASS {qual}")
        except AssertionError as err:
            failed += 1
            lines.append(f"FAIL {qual}: {err}")
            lines.append(traceback.format_exc())
        except Exception as err:  # noqa: BLE001
            errored += 1
            lines.append(f"ERROR {qual}: {err}")
            lines.append(traceback.format_exc())

    ok = failed == 0 and errored == 0 and passed > 0
    lines.append("-" * 60)
    lines.append(f"Summary: pass={passed} fail={failed} error={errored}")
    lines.append("RESULT: " + ("OK" if ok else "FAILED"))
    text = "\n".join(lines) + "\n"
    LOG.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
