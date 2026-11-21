"""PROBE_CSV row parsing — live probe match/mismatch semantics."""

from __future__ import annotations

from typing import Optional


HEADER = "sent_level,arg,fade,level_byte,reported,match,tx_hex"

# Historical b7–b12 mismatch: ARG_ON + level_byte=50 reported 100 (forced full).
LEGACY_ON_LEVELBYTE_MISMATCH = (
    "PROBE_CSV 50,0,3,50,100,no,"
    "02 12 01 7e 25 0d bc 00 1e 03 10 52 05 00 03 32 00 00 93 7f 8f"
)

# b13 encoding: level 80 → arg 14, level_byte 0 (live probe matched ~80).
NEW_ARG_STEP_80 = (
    "PROBE_CSV 80,14,3,0,80,yes,"
    "02 12 01 7e 25 0d bc 00 1e 03 10 52 05 0e 03 00 00 00 a0 7f 8f"
)


def parse_probe_csv_line(line: str) -> Optional[dict]:
    line = line.strip()
    if not line.startswith("PROBE_CSV "):
        return None
    body = line[len("PROBE_CSV ") :]
    if body.startswith("sent_level,"):
        return None
    parts = body.split(",", 6)
    if len(parts) != 7:
        raise ValueError(f"bad PROBE_CSV row: {line!r}")
    sent, arg, fade, level_byte, reported, match, tx_hex = parts
    return {
        "sent_level": int(sent),
        "arg": int(arg),
        "fade": int(fade),
        "level_byte": int(level_byte),
        "reported": None if reported == "" else int(reported),
        "match": match,
        "tx_hex": tx_hex.strip(),
    }


def classify_match(sent: int, reported: Optional[int]) -> str:
    if reported is None:
        return "none"
    return "yes" if reported == sent else "no"


class TestProbeCsvParse:
    def test_header_constant(self):
        assert HEADER.split(",") == [
            "sent_level",
            "arg",
            "fade",
            "level_byte",
            "reported",
            "match",
            "tx_hex",
        ]

    def test_legacy_mismatch_documents_b12_bug(self):
        row = parse_probe_csv_line(LEGACY_ON_LEVELBYTE_MISMATCH)
        assert row is not None
        assert row["sent_level"] == 50
        assert row["arg"] == 0
        assert row["level_byte"] == 50
        assert row["reported"] == 100
        assert row["match"] == "no"

    def test_new_encoding_row_matches_builder(self):
        from utils.protocol import build_set_level_command

        row = parse_probe_csv_line(NEW_ARG_STEP_80)
        assert row is not None
        assert row["arg"] == 14
        assert row["level_byte"] == 0
        assert row["match"] == "yes"
        expected = build_set_level_command(0x1E, 0x03, 0xA204, 80)
        assert bytes.fromhex(row["tx_hex"]) == expected

    def test_classify_match_labels(self):
        assert classify_match(80, 80) == "yes"
        assert classify_match(50, 100) == "no"
        assert classify_match(50, None) == "none"

    def test_legacy_match_label_consistent(self):
        row = parse_probe_csv_line(LEGACY_ON_LEVELBYTE_MISMATCH)
        assert row is not None
        assert classify_match(row["sent_level"], row["reported"]) == row["match"]
