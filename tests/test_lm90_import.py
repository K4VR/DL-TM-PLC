from __future__ import annotations

from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.lm90_to_l5x.convert_logic import build_conversion, convert_rung
from tools.lm90_to_l5x.emit_l5x import emit_l5x
from tools.lm90_to_l5x.extract import decode_printout_bytes
from tools.lm90_to_l5x.model import Conversion, TagDef, sanitize_alias
from tools.lm90_to_l5x.parse_source import parse_declarations, parse_program, RawRung


SAMPLE_DECL = """
              V A R I A B L E   D E C L A R A T I O N   T A B L E

          REFERENCE     NICKNAME          REFERENCE DESCRIPTION
          ---------     --------     --------------------------------
          %I0001                     Lamp    Test    On      PB77
          %I0005        PRSLED       RollChg Sled Lock Pin
          %Q0129        S07-01A      Some    Valve
          %R00631                    AGC     Elongtn Set Pnt Value
          %M0717        AGCOS        At2Plc  AGC     Fault   OneShot

                        I D E N T I F I E R    T A B L E
"""

SIMPLE_RUNG = """│%M0257                                                                  %M0717
├──┤/├─────────────────────────────────────────────────────────────────────(^)──
"""

BRANCH_RUNG = """│%M0717  %M0708  %M0705  %M0709                                          %M0718
├──┤ ├─────┤ ├──┬──┤/├─────┤/├─────────────────────────────────────────────( )──
│               │
│%M0718         │
├──┤ ├──────────┘
"""

CALL_RUNG = """│ALW_ON
│%S0007  ┌─────────────┐
├──┤ ├───┤ CALL BGNSCN ├
│        │ (SUBROUTINE)│
│        └─────────────┘
"""

MOVE_RUNG = """│FST_SCN
│%S0001  ┌─────┐
├──┤ ├───┤MOVE_├─
│        │ INT │
│ CONST ─┤IN  Q├─%R00631
│ +00020 │ LEN │
│        │00001│
│        └─────┘
"""

LABEL_RUNG = """├AGCJMP1:(nested)
"""

JUMP_RUNG = """│%M1284
├──┤/├──┬──────────────────────────────────────────────────────────N──>> AGCJMP1
│%M0825 │
├──┤ ├──┘
"""

LABEL_SPACED = """├ JUMP1 :(nested)
"""

RETENTIVE_RUNG = """│%M1390  %M1395                                                          %M1391
├──┤ ├─────┤ ├─────────────────────────────────────────────────────────────(M)──
"""

FALLING_RUNG = """│%M0301                                                                  %M0302
├──┤ ├─────────────────────────────────────────────────────────────────────(v)──
"""

UPCTR_RUNG = """│%M1390  %M0451  %S0006  ┌─────┐                                         %M1395
├──┤ ├─────┤ ├─────┤ ├───>UPCTR├───────────────────────────────────────────(M)──
│                        │     │
│%M1387                  │     │
├──┤ ├───────────────────┤R    │
│                 CONST ─┤PV   │
│                 +00300 │     │
│                        └─────┘
│                        %R01324
"""

BLKMV_RUNG = """│%M0370  ┌─────┐
├──┤ ├───┤BLKMV├─────────────<+>
│        │ INT │
│ CONST ─┤IN1 Q├─%R00218
│ +00006 │     │
│ CONST ─┤IN2  │
│ +00000 │     │
│ CONST ─┤IN3  │
│ +00008 └─────┘
"""

CONT_OUT = """│%M0385  %M0815                                                          %M0897
├──┤ ├─────┤/├─────────────────────────────────────────────────────────────<+>
"""

CONT_IN = """│        %M1860                                                          %M0897
├<+>───────┤ ├─────────────────────────────────────────────────────────────( )──
"""

TWO_CALLS = """│%S0007  ┌─────────────┐ ┌─────────────┐
├──┤ ├───┤ CALL POLISHB├─┤ CALL POLISHT├
│        │ (SUBROUTINE)│ │ (SUBROUTINE)│
│        └─────────────┘ └─────────────┘
"""


def test_sanitize_hyphen_nickname():
    assert sanitize_alias("S07-01A") == "S07_01A"


def test_parse_declarations():
    decls = parse_declarations(SAMPLE_DECL.splitlines())
    by_ref = {d.ref: d for d in decls}
    assert by_ref["%I0001"].nickname == ""
    assert by_ref["%I0001"].description == "Lamp Test On PB77"
    assert by_ref["%I0005"].nickname == "PRSLED"
    assert by_ref["%Q0129"].nickname == "S07-01A"


def test_simple_nc_oneshot():
    conv = Conversion()
    raw = RawRung(4, 2, "AGC", "latch fault", SIMPLE_RUNG.splitlines())
    out = convert_rung(raw, conv)
    assert out
    text = out[0].text
    assert "XIO(" in text
    assert "ONS(" in text
    assert "OTE(" in text
    assert "GE Rung 4" in out[0].comment
    assert "%" not in text


def test_branch_or_seal_in():
    conv = Conversion()
    raw = RawRung(5, 4, "AGC", "", BRANCH_RUNG.splitlines())
    out = convert_rung(raw, conv)
    text = out[0].text
    assert "OTE(" in text
    assert "[" in text or "XIC(" in text
    assert "%" not in text


def test_call_jsr():
    conv = Conversion()
    raw = RawRung(5, 2, "_MAIN", "Begin Scan", CALL_RUNG.splitlines())
    out = convert_rung(raw, conv)
    joined = " ".join(r.text for r in out)
    assert "JSR(BGNSCN,0)" in joined
    assert "XIC(" in joined


def test_two_calls_are_separate_jsrs():
    conv = Conversion()
    raw = RawRung(23, 83, "POLISHR", "", TWO_CALLS.splitlines())
    joined = " ".join(r.text for r in convert_rung(raw, conv))
    assert "JSR(POLISHB,0)" in joined
    assert "JSR(POLISHT,0)" in joined
    assert "JSR(POLISHB,0)JSR(POLISHT" not in joined.replace(" ", "")


def test_move_const():
    conv = Conversion()
    raw = RawRung(31, 82, "AGC", "Load min max", MOVE_RUNG.splitlines())
    out = convert_rung(raw, conv)
    joined = " ".join(r.text for r in out)
    assert "MOV(20," in joined.replace(" ", "")
    assert "R00631" in joined
    assert "%" not in joined


def test_jump_and_label():
    conv = Conversion()
    lbl = convert_rung(RawRung(29, 80, "AGC", "", LABEL_RUNG.splitlines()), conv)
    assert lbl[0].text == "LBL(AGCJMP1);"
    jmp = convert_rung(RawRung(23, 66, "AGC", "", JUMP_RUNG.splitlines()), conv)
    assert "JMP(AGCJMP1)" in jmp[0].text
    assert "XIO(" in jmp[0].text
    spaced = convert_rung(RawRung(117, 469, "RUNPERM", "", LABEL_SPACED.splitlines()), conv)
    assert spaced[0].text == "LBL(JUMP1);"


def test_retentive_and_falling_coils():
    conv = Conversion()
    ret = convert_rung(RawRung(22, 50, "LUBEM", "", RETENTIVE_RUNG.splitlines()), conv)
    assert "OTE(" in ret[0].text
    assert "%" not in ret[0].text
    fall = convert_rung(RawRung(44, 98, "GLOBAL", "", FALLING_RUNG.splitlines()), conv)
    joined = " ".join(r.text for r in fall)
    assert "ONS(" in joined
    assert "XIO(" in joined


def test_upctr():
    conv = Conversion()
    out = convert_rung(RawRung(31, 86, "LUBEM", "", UPCTR_RUNG.splitlines()), conv)
    joined = " ".join(r.text for r in out)
    assert "CTU(" in joined
    assert "300" in joined
    assert "RES(" in joined


def test_blkmv_constants():
    conv = Conversion()
    out = convert_rung(RawRung(10, 20, "MISC", "", BLKMV_RUNG.splitlines()), conv)
    joined = " ".join(r.text for r in out)
    assert "MOV(6,R00218)" in joined.replace(" ", "")
    assert "CONT_MISC_10" in joined


def test_continuation_pair():
    conv = Conversion()
    first = RawRung(4, 2, "AUTOLVL", "", CONT_OUT.splitlines())
    second = RawRung(5, 12, "AUTOLVL", "", CONT_IN.splitlines())
    a = convert_rung(first, conv)
    b = convert_rung(second, conv, prev=first)
    assert any("OTE(CONT_AUTOLVL_4)" in r.text for r in a)
    assert any("XIC(CONT_AUTOLVL_4)" in r.text for r in b)
    assert any("OTE(" in r.text and "M0897" in r.text for r in b)


AGC_MOVE_BRANCH = """│%M0713          ┌─────┐
├──┤ ├──┬────────┤MOVE_├─────────────<+>
│        │        │ INT │
│%M0728 │        │     │
├──┤ ├──┘%R00631─┤IN  Q├─%R00411
│                │ LEN │
│                │00004│
│                └─────┘
"""


def test_move_does_not_treat_register_as_contact():
    conv = Conversion()
    out = convert_rung(RawRung(43, 108, "AGC", "", AGC_MOVE_BRANCH.splitlines()), conv)
    joined = " ".join(r.text for r in out)
    assert "XIC(R00631)" not in joined
    assert "COP(R00631,R00411,4)" in joined.replace(" ", "")


NICK_CALL = """│ALW_ON  ┌─────────────┐
├──┤ ├───┤ CALL TOP_OP ├
│        │ (SUBROUTINE)│
│        └─────────────┘
"""

NICK_NE = """│CTL_ON  ┌─────┐                                                         %T0004
├──┤ ├───┤ NE_ │┌──────────────────────────────────────────────────────────( )──
│        │ INT ││
│        │     ││
│TOP_REQ─┤I1  Q├┘
│        │     │
│ CONST ─┤I2   │
│ +00000 └─────┘
"""

MOD_DINT = """│%M0016          ┌─────┐                                 ┌─────┐
├──┤ ├───────────┤ DIV_├─────────────────────────────────┤ MOD_├─
│                │ DINT│                                 │ DINT│
│                │     │                                 │     │
│        %R00365─┤I1  Q├─%R00367                 %R00385─┤I1  Q├─%R00369
│                │     │                                 │     │
│        %R00363─┤I2   │                         %R00363─┤I2   │
│                └─────┘                                 └─────┘
"""


NICK_MOVE = """│%T0004  ┌─────┐                 ┌─────┐                 ┌─────┐
├──┤ ├───┤MOVE_├─────────────────┤MOVE_├─────────────────┤MOVE_├─
│        │ INT │                 │ INT │                 │ INT │
│        │     │                 │     │                 │     │
│TOP_REQ─┤IN  Q├─TOP_UPD  CONST ─┤IN  Q├─TOP_REQ  CONST ─┤IN  Q├─TOP_CTR
│        │ LEN │          +00000 │ LEN │          +00000 │ LEN │
│        │00001│                 │00001│                 │00001│
│        └─────┘                 └─────┘                 └─────┘
"""


def _conv_with_nicks() -> Conversion:
    conv = Conversion()
    conv.tags["I0003"] = TagDef("I0003", "BOOL", "Gap Ctl Requested On")
    conv.tags["R00001"] = TagDef("R00001", "INT", "Top Op Gap Requested Output")
    conv.tags["R00002"] = TagDef("R00002", "INT", "Top Op Gap Updated Output Req")
    conv.tags["R00004"] = TagDef("R00004", "INT", "Top Op Gap Output Counter")
    conv.aliases["CTL_ON"] = TagDef("CTL_ON", "BOOL", "Gap Ctl Requested On", alias_for="I0003")
    conv.aliases["TOP_REQ"] = TagDef("TOP_REQ", "INT", "Top Op Gap Requested Output", alias_for="R00001")
    conv.aliases["TOP_UPD"] = TagDef("TOP_UPD", "INT", "Top Op Gap Updated Output Req", alias_for="R00002")
    conv.aliases["TOP_CTR"] = TagDef("TOP_CTR", "INT", "Top Op Gap Output Counter", alias_for="R00004")
    conv.nicknames["CTL_ON"] = "CTL_ON"
    conv.nicknames["TOP_REQ"] = "TOP_REQ"
    conv.nicknames["TOP_UPD"] = "TOP_UPD"
    conv.nicknames["TOP_CTR"] = "TOP_CTR"
    return conv


def test_cp437_printout_decode():
    raw = "│%I0003        CTL_ON\r\n".encode("cp437")
    text = decode_printout_bytes(raw)
    assert "│%I0003" in text
    assert "CTL_ON" in text


def test_nickname_only_call_uses_alias():
    conv = Conversion()
    out = convert_rung(RawRung(9, 12, "_MAIN", "", NICK_CALL.splitlines()), conv)
    joined = " ".join(r.text for r in out)
    assert "JSR(TOP_OP,0)" in joined
    assert "XIC(ALW_ON)" in joined
    assert "%" not in joined


def test_nickname_compare_and_coil():
    conv = _conv_with_nicks()
    out = convert_rung(RawRung(3, 1, "TOP_OP", "", NICK_NE.splitlines()), conv)
    joined = " ".join(r.text for r in out)
    assert "XIC(CTL_ON)" in joined
    assert "NEQ(TOP_REQ,0)" in joined
    assert "OTE(T0004)" in joined
    assert "%" not in joined
    assert "GE Rung 3" in out[0].comment


def test_nickname_move_destinations():
    conv = _conv_with_nicks()
    out = convert_rung(RawRung(4, 4, "TOP_OP", "", NICK_MOVE.splitlines()), conv)
    joined = " ".join(r.text for r in out).replace(" ", "")
    assert "MOV(TOP_REQ,TOP_UPD)" in joined
    assert "MOV(0,TOP_REQ)" in joined
    assert "MOV(0,TOP_CTR)" in joined
    assert "%" not in joined


def test_mod_dint():
    conv = Conversion()
    out = convert_rung(RawRung(26, 62, "FT_PULS", "", MOD_DINT.splitlines()), conv)
    joined = " ".join(r.text for r in out).replace(" ", "")
    assert "MOD(" in joined
    assert "DIV(" in joined
    assert "R00365D" in joined or "R00365" in joined


def test_full_program_tags_and_l5x():
    text = (ROOT / "source" / "TemperMillProgram.txt").read_text(encoding="utf-8")
    raw = parse_program(text)
    assert len(raw.decls) > 1000
    assert any(d.nickname == "PRSLED" for d in raw.decls)
    assert "AGC" in raw.identifiers
    conv = build_conversion(raw)
    assert "I0005" in conv.tags
    assert conv.tags["I0005"].datatype == "BOOL"
    assert conv.tags["R00631"].datatype == "INT"
    assert "PRSLED" in conv.aliases
    assert conv.aliases["PRSLED"].alias_for == "I0005"
    assert "S07_01A" in conv.aliases
    xml = emit_l5x(conv)
    assert 'ProcessorType="1756-L81E"' in xml
    assert 'ProductCode="164"' in xml
    assert "PRSLED" in xml
    assert "GE Rung" in xml
    assert "<Rung " in xml
    assert 'Name="%I' not in xml
    ET.fromstring(xml)
    agc = next(b for b in conv.blocks if b.name == "AGC")
    comments = " ".join(r.comment for r in agc.rungs[:8])
    assert "BLOCK SIZE" not in comments
    jmp_lbl = " ".join(r.text for r in agc.rungs if r.ge_number in {23, 29})
    assert "JMP(AGCJMP1)" in jmp_lbl
    assert "LBL(AGCJMP1)" in jmp_lbl
