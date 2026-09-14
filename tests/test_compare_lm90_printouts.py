from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.compare_lm90_printouts import (
    Rung,
    align_programs,
    extract_outputs,
    format_rung_list,
    parse_printout,
)

ORIG = ROOT / "source/airknife/AIRKNIFE_2026-02-27.txt"
LATER = ROOT / "source/airknife/AIRKNIFE_2026-09-10.txt"


def test_format_rung_list_uses_ranges() -> None:
    assert format_rung_list([6, 7, 8, 10, 11, 33, 187]) == "6-8, 10-11, 33, 187"


def test_extract_outputs_retentive_coil() -> None:
    rung = Rung(
        block="COAT_WG",
        number=68,
        logic=[
            "|KGAUTO          +-----+",
            "+--] [-----------+ GT_ +-",
            "|                | REAL|",
            "|                |     |                                                 TKO_OUT",
            "|        TKO_ERR-+I1  Q+---------------------------------------------------(SM)-",
            "|                |     |",
            "|        KNIERRU-+I2   |",
            "|                +-----+",
        ],
    )
    assert extract_outputs(rung) == frozenset({"TKO_OUT"})


def test_extract_outputs_dual_move() -> None:
    rung = Rung(
        block="COAT_WG",
        number=137,
        logic=[
            "|ALW_ON  SIMPKNA +-----+                                 +-----+",
            "+--] [-----]/[---+MOVE_+---------------------------------+MOVE_+-",
            "|                | REAL|                                 | REAL|",
            "|                |     |                                 |     |",
            "|        FRA_KTO-+IN  Q+-TKO_SPA                 FRA_KTO-+IN  Q+-SPTOPOP",
            "|                | LEN |                                 | LEN |",
            "|                |00001|                                 |00001|",
            "|                +-----+                                 +-----+",
        ],
    )
    assert extract_outputs(rung) == frozenset({"TKO_SPA", "SPTOPOP"})


def test_airknife_printouts_parse_and_align() -> None:
    original = parse_printout(ORIG, "Original Airknife")
    later = parse_printout(LATER, "91026")
    assert len(original.rungs) == 435
    assert len(later.rungs) == 329
    assert original.block_order[0] == "_MAIN"
    assert "COAT_WG" in original.block_order

    rows = align_programs(original, later)
    assert sum(1 for r in rows if r.original) == 435
    assert sum(1 for r in rows if r.later) == 329

    def lookup_orig(block: str, number: int):
        return next(r for r in rows if r.original and r.original.block == block and r.original.number == number)

    simulat = lookup_orig("_MAIN", 6)
    assert simulat.change == "Deleted"
    assert simulat.later is None
    assert simulat.original.number == 6

    call = lookup_orig("_MAIN", 20)
    assert call.change == "Unchanged"
    assert call.later is not None and call.later.number == 8

    blowena = lookup_orig("_MAIN", 23)
    assert blowena.later is not None and blowena.later.number == 11

    anauto = lookup_orig("_MAIN", 183)
    assert anauto.change == "Modified"
    assert anauto.later is not None and anauto.later.number == 174

    hgtauto = lookup_orig("_MAIN", 187)
    assert hgtauto.change == "Deleted"

    added = [r for r in rows if r.change == "Added"]
    assert [r.later.number for r in added if r.later] == [168, 169, 170, 171, 172]
    assert all(r.block == "_MAIN" for r in added)

    modified = [r for r in rows if r.change == "Modified"]
    for row in modified:
        if row.original.outputs and row.later.outputs:
            assert row.original.outputs & row.later.outputs
