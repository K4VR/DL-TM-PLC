from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.airknife_rung_comments import (
    comment_rows,
    csv_comment,
    format_printout_comment,
    logix_rung_comment,
)
from tools.compare_lm90_printouts import parse_printout

PRINTOUT = ROOT / "source/airknife/AIRKNIFE_2026-09-10.txt"


def test_format_printout_comment_strips_boxes() -> None:
    raw = [
        "| (****************************************************************************)",
        "| (* THE NEXT THREE RUNGS ARE FOR BLOWER CONTROL.                             *)",
        "| (****************************************************************************)",
    ]
    assert format_printout_comment(raw) == "THE NEXT THREE RUNGS ARE FOR BLOWER CONTROL."


def test_csv_comment_uses_dollar_n() -> None:
    assert csv_comment("GE Rung 8\n\nBlower") == "GE Rung 8$N$NBlower"


def test_91026_comments_attach_to_following_rung() -> None:
    parsed = parse_printout(PRINTOUT, "91026")
    main = {r.number: r for r in parsed.rungs if r.block == "_MAIN"}
    coat = {r.number: r for r in parsed.rungs if r.block == "COAT_WG"}

    r8 = logix_rung_comment(main[8])
    assert r8.startswith("GE Rung 8")
    assert "Simulate Mode for testing ACT Galv Control" in r8
    assert "Toggle (F12) SIMBLAS" in r8
    assert "COAT_WG" in r8
    assert "BLOWER CONTROL" not in r8

    r11 = logix_rung_comment(main[11])
    assert r11.startswith("GE Rung 11")
    assert "THE NEXT THREE RUNGS ARE FOR BLOWER CONTROL." in r11

    r15 = logix_rung_comment(main[15])
    assert "BLOWER DRIVE FAULT DETECTION" in r15

    r5 = logix_rung_comment(coat[5])
    assert r5.startswith("GE Rung 5")
    assert "Map the registers bits sent from ACT" in r5
    assert "Heartbeat Timer to ACT" in r5

    r6 = logix_rung_comment(coat[6])
    assert r6 == "GE Rung 6"


def test_comment_rows_cover_every_printed_rung() -> None:
    parsed = parse_printout(PRINTOUT, "91026")
    rows = comment_rows(parsed.rungs, program="Airknife", main_routine="MainRoutine", location="sequential")
    assert len(rows) == 329
    assert rows[0]["routine"] == "MainRoutine"
    assert rows[0]["ge_rung"] == 8
    assert rows[0]["location"] == 0
    assert all(str(r["comment"]).startswith("GE Rung ") for r in rows)
    coat = [r for r in rows if r["block"] == "COAT_WG"]
    assert coat[0]["routine"] == "COAT_WG"
    assert coat[0]["location"] == 0
    ge_rows = comment_rows(parsed.rungs, program="Airknife", main_routine="MainRoutine", location="ge")
    assert ge_rows[0]["location"] == 8
    assert sum(1 for r in rows if r["has_printout_comment"]) > 50
