from __future__ import annotations

from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.lm90_to_l5x.convert_logic import build_conversion
from tools.lm90_to_l5x.emit_l5x import emit_l5x
from tools.lm90_to_l5x.parse_source import parse_program

SOURCE = ROOT / "source" / "910pio" / "PIOPLC.txt"


def test_pioplc_printout_import():
    raw = parse_program(SOURCE.read_text(encoding="utf-8"))
    assert len(raw.decls) >= 75
    by_ref = {d.ref: d for d in raw.decls}
    assert by_ref["%I0003"].nickname == "CTL_ON"
    assert "Gap Ctl Requested On" in by_ref["%I0003"].description
    assert by_ref["%R00001"].nickname == "TOP_REQ"
    assert by_ref["%R00234"].nickname == "TOPC-Y"
    assert by_ref["%AI0001"].nickname == "BOTPFB"
    assert by_ref["%AQ0001"].nickname == "BOTPSP"
    assert by_ref["%Q0001"].nickname == "TOP_INC"
    assert by_ref["%M0001"].nickname == "HEARTBT"
    assert "TOP_OP" in raw.identifiers
    blocks = {r.block for r in raw.rungs}
    assert blocks >= {"_MAIN", "FT_PULS", "TOP_OP", "TDR_OP", "BOP_OP", "BDR_OP", "ACT_CWG"}
    assert len(raw.rungs) >= 140

    conv = build_conversion(raw)
    assert conv.tags["I0003"].datatype == "BOOL"
    assert conv.tags["Q0001"].datatype == "BOOL"
    assert conv.tags["M0001"].datatype == "BOOL"
    assert conv.tags["R00001"].datatype == "INT"
    assert conv.tags["AI0001"].datatype == "INT"
    assert conv.tags["AQ0001"].datatype == "INT"
    assert conv.tags["I0003"].description == "Gap Ctl Requested On"
    assert "CTL_ON" in conv.aliases
    assert conv.aliases["CTL_ON"].alias_for == "I0003"
    assert conv.aliases["CTL_ON"].description == "Gap Ctl Requested On"
    assert "TOPC_Y" in conv.aliases
    assert conv.aliases["TOPC_Y"].alias_for == "R00234"

    xml = emit_l5x(conv, controller="PIOPLC", program="PIOPLC")
    assert 'ProcessorType="1756-L81E"' in xml
    assert 'ProductCode="164"' in xml
    assert 'Name="PIOPLC"' in xml
    assert 'Name="%I' not in xml
    assert "GE Rung" in xml
    ET.fromstring(xml)

    main = next(b for b in conv.blocks if b.name == "MainRoutine")
    top = next(b for b in conv.blocks if b.name == "TOP_OP")
    ft = next(b for b in conv.blocks if b.name == "FT_PULS")
    assert main.rungs[0].text.startswith("JSR(SYSBITS")
    call_text = " ".join(r.text for r in main.rungs)
    assert "JSR(TOP_OP,0)" in call_text
    assert "XIC(ALW_ON)" in call_text
    assert "XIC(CTL_ON)" in " ".join(r.text for r in top.rungs)
    assert "NEQ(TOP_REQ" in " ".join(r.text for r in top.rungs)
    assert any(r.comment.startswith("GE Rung 3") for r in top.rungs)
    assert any("MOD(" in r.text for r in ft.rungs)
    assert conv.unmapped_rungs == 0
    for block in conv.blocks:
        for rung in block.rungs:
            assert "%" not in rung.text
            if rung.ge_number:
                assert f"GE Rung {rung.ge_number}" in rung.comment
