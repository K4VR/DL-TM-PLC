from __future__ import annotations

from pathlib import Path
import sys

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.compare_lm90_printouts import parse_printout
from tools.compare_revisions import build_diff, build_workbook

EARLIER = ROOT / "source/airknife/AIRKNIFE_2026-09-10.txt"
LATER = ROOT / "source/airknife/AIRKNIFE_2026-10-21.txt"
FEB = ROOT / "source/airknife/AIRKNIFE_2026-02-27.txt"


def _report():
    earlier = parse_printout(EARLIER, "09/10/2026")
    later = parse_printout(LATER, "10/21/2026")
    return build_diff(earlier, later, "09/10/2026", "10/21/2026")


def test_page_titles_are_not_ladder() -> None:
    feb = parse_printout(FEB, "Feb")
    earlier = parse_printout(EARLIER, "09/10")
    later = parse_printout(LATER, "10/21")
    assert feb.title == "Airknife"
    assert earlier.title == "9_10_2026"
    assert later.title == "Airknif"
    assert later.program_size == "16496"
    assert earlier.program_size == "16368"
    for rung in later.rungs:
        assert all(line.strip() != "Airknif" for line in rung.logic)


def test_1021_logic_changes_are_elevator_stab_and_two_constants() -> None:
    report = _report()
    assert len(report.earlier.rungs) == 329
    assert len(report.later.rungs) == 333
    assert [(row.change, row.block, row.old.number if row.old else None, row.new.number if row.new else None)
            for row in report.logic] == [
        ("Added", "_MAIN", None, 157),
        ("Added", "_MAIN", None, 158),
        ("Added", "_MAIN", None, 159),
        ("Added", "_MAIN", None, 160),
        ("Modified", "_MAIN", 158, 162),
        ("Modified", "_MAIN", 160, 164),
        ("Modified", "_MAIN", 164, 168),
        ("Modified", "_MAIN", 166, 170),
        ("Modified", "_MAIN", 240, 244),
        ("Modified", "_MAIN", 242, 246),
        ("Modified", "COAT_WG", 26, 26),
        ("Modified", "COAT_WG", 38, 38),
    ]
    assert report.deleted == []
    by_new = {row.new.number: row for row in report.logic if row.new}
    assert "ELUPPB" in by_new[157].detail and "ELUPOS" in by_new[157].detail
    assert "%P00067" in by_new[158].detail and "ELTPUP" in by_new[158].detail
    assert "ST5FWD" in by_new[162].detail and "ELDRSEL" in by_new[162].detail
    assert "SS1" in by_new[244].detail and "%I00041" in by_new[244].detail
    assert "+10.50000 to +10.62500" in by_new[26].detail
    assert "+5.650000 to +5.870000" in by_new[38].detail

    main_shifts = {
        row.new_number - row.old_number
        for row in report.mapping
        if row.block == "_MAIN" and row.old_number and row.new_number and row.old_number >= 158
    }
    coat_shifts = {
        row.new_number - row.old_number
        for row in report.mapping
        if row.block == "COAT_WG" and row.old_number and row.new_number
    }
    assert main_shifts == {4}
    assert coat_shifts == {0}


def test_declarations_and_cleared_comments() -> None:
    report = _report()
    kinds = {}
    for row in report.decls:
        kinds[row.change] = kinds.get(row.change, 0) + 1
    assert kinds == {
        "Added": 36,
        "Renamed": 6,
        "Nickname assigned": 8,
        "Description": 6,
    }
    by_ref = {row.ref: row for row in report.decls}
    assert by_ref["%M00586"].new_nick == "ELUPOS"
    assert "not used in the 09/10 ladder" in by_ref["%M00586"].note
    assert by_ref["%R00875"].new_nick == "STTILT"
    assert "already used" in by_ref["%R00875"].note
    assert by_ref["%M00568"].old_nick == "HTRVFLT"
    assert by_ref["%M00568"].new_nick == "KHTNEG"
    assert "Operator" in by_ref["%R00965"].note
    assert by_ref["%R00967"].note.startswith("Spelling")
    assert len(report.comments) == 87
    assert all(row.new_comment == "(COMMENT placeholder)" for row in report.comments)
    assert any("DRIVE SIDE ELEVATOR UP" in row.old_comment for row in report.comments)


def test_workbook_sheets(tmp_path: Path) -> None:
    report = _report()
    path = tmp_path / "changes.xlsx"
    build_workbook(report).save(path)
    wb = load_workbook(path)
    assert wb.sheetnames == [
        "Summary",
        "Logic Changes",
        "Rung Comments",
        "Declarations",
        "Rung Map",
        "Program Header",
    ]
    logic = wb["Logic Changes"]
    assert logic["A5"].value == "Added"
    assert logic["E5"].value == 157
    assert "10.62500" in wb["Summary"]["A11"].value
