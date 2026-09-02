from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import (
    KNOWN_SYSTEM,
    Conversion,
    LogixRung,
    TagDef,
    BlockDef,
    collapse_desc,
    datatype_for,
    ge_ref_to_tag,
    sanitize_alias,
)
from .parse_source import REF_RE, RawProgram, RawRung

CONTACT_NO = re.compile(r"┤ ?├")
CONTACT_NC = re.compile(r"┤/├")
COIL_RE = re.compile(r"\((SM|RM| |S|R|/|\^|M|v)\)")
FUNC_NAME_RE = re.compile(r"┤\s*(CALL\s+[A-Z0-9_ ]+|MOVE_|BLK_|BLKMV|ADD_|SUB_|MUL_|DIV_|EQ_|NE_|GT_|GE_|LT_|LE_|TMR|OFDT|ONDTR|AND_|OR_|NOT_|SHL_|BIT_|DO_IO|RANGE|INT_|COMM_|SVC_|UPCTR)\s*[├│]")
FUNC_SCAN_RE = re.compile(
    r"┤\s*(CALL\s+[A-Z0-9_ ]+|MOVE_|BLK_|BLKMV|ADD_|SUB_|MUL_|DIV_|EQ_|NE_|GT_|GE_|LT_|LE_|TMR|OFDT|ONDTR|AND_|OR_|NOT_|SHL_|BIT_|DO_IO|RANGE|INT_|COMM_|SVC_)\s*"
)
INPUT_FUNCS = {"EQ_", "NE_", "GT_", "GE_", "LT_", "LE_", "RANGE"}
TIMER_FUNCS = {"TMR", "OFDT", "ONDTR"}
COUNTER_FUNCS = {"UPCTR"}
OUTPUT_FUNCS = {
    "MOVE_",
    "BLK_",
    "BLKMV",
    "ADD_",
    "SUB_",
    "MUL_",
    "DIV_",
    "AND_",
    "OR_",
    "NOT_",
    "SHL_",
    "BIT_",
    "INT_",
    "DO_IO",
    "COMM_",
    "SVC_",
    "CALL",
} | TIMER_FUNCS | COUNTER_FUNCS
CONST_RE = re.compile(r"([+-]\d+|0[0-9A-Fa-f]+|\d+)")
REF_TOKEN_RE = re.compile(r"%[A-Z]+\d+")


def _norm_box(s: str) -> str:
    return (
        s.replace("╟", "├")
        .replace("╢", "┤")
        .replace("║", "│")
        .replace("═", "─")
        .replace("╥", "┬")
        .replace("╙", "└")
        .replace("╜", "┘")
    )


def _pad(lines: list[str]) -> list[str]:
    width = max((len(l) for l in lines), default=0)
    return [l.ljust(width) for l in lines]


def _refs_on_line(line: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(0)) for m in REF_TOKEN_RE.finditer(line)]


def resolve_name(ref: str, conv: Conversion) -> str:
    tag = ge_ref_to_tag(ref)
    for alias in conv.aliases.values():
        if alias.alias_for == tag:
            return alias.name
    return tag


def ensure_tag(conv: Conversion, ref: str, desc: str = "") -> str:
    tag = ge_ref_to_tag(ref)
    if tag not in conv.tags:
        nick, known_desc = KNOWN_SYSTEM.get(tag, ("", ""))
        conv.tags[tag] = TagDef(name=tag, datatype=datatype_for(tag), description=desc or known_desc)
        if nick and nick not in conv.aliases and nick != tag:
            conv.aliases[nick] = TagDef(
                name=nick,
                datatype=conv.tags[tag].datatype,
                description=conv.tags[tag].description,
                alias_for=tag,
            )
    elif desc and not conv.tags[tag].description:
        conv.tags[tag].description = desc
    return resolve_name("%" + tag if not ref.startswith("%") else ref, conv)


def ensure_bool(conv: Conversion, name: str, desc: str = "") -> str:
    if name not in conv.tags and name not in conv.aliases:
        conv.tags[name] = TagDef(name=name, datatype="BOOL", description=desc)
    return name


def ensure_timer(conv: Conversion, base: str) -> str:
    name = f"{base}_TMR"
    if name not in conv.tags:
        conv.tags[name] = TagDef(name=name, datatype="TIMER", description=f"GE timer at {base}")
    return name


def ensure_counter(conv: Conversion, base: str) -> str:
    name = f"{base}_CTR"
    if name not in conv.tags:
        conv.tags[name] = TagDef(name=name, datatype="COUNTER", description=f"GE up-counter at {base}")
    return name


def cont_tag(block: str, ge_number: int) -> str:
    return f"CONT_{block}_{ge_number}"


def ensure_dint(conv: Conversion, base: str) -> str:
    name = f"{base}D"
    if name not in conv.tags:
        conv.tags[name] = TagDef(
            name=name,
            datatype="DINT",
            description=f"DINT overlay of GE {base}/{_next_reg(base)}",
        )
    return name


def _next_reg(tag: str) -> str:
    m = re.match(r"([A-Z]+)(\d+)$", tag)
    if not m:
        return tag
    width = len(m.group(2))
    return f"{m.group(1)}{int(m.group(2)) + 1:0{width}d}"


@dataclass
class Contact:
    x: int
    y: int
    nc: bool
    operand: str = ""


@dataclass
class Coil:
    x: int
    y: int
    kind: str  # OTE OTL OTU NEG PCOIL
    operand: str = ""


def _assoc_operand(lines: list[str], x: int, y: int) -> str:
    lo, hi = max(0, x - 4), min(len(lines[y]) if lines else 0, x + 8)
    for yy in range(y, -1, -1):
        line = lines[yy]
        for start, ref in _refs_on_line(line):
            if lo - 2 <= start <= hi and datatype_for(ge_ref_to_tag(ref)) == "BOOL":
                return ref
        # stop if we hit another rail
        if yy < y and ("├" in line or "┤ TMR" in line or "┤MOVE_" in line):
            if yy < y - 6:
                break
    return ""


def _parse_const(token: str) -> str:
    token = token.strip()
    if re.fullmatch(r"[+-]\d+", token):
        return str(int(token, 10))
    if re.fullmatch(r"[0-9A-Fa-f]+", token) and re.search(r"[A-Fa-f]", token):
        return str(int(token, 16))
    if re.fullmatch(r"\d+", token):
        return str(int(token, 10))
    return token


def _find_const_left(lines: list[str], x: int, y: int) -> str | None:
    """CONST values sit to the left of the pin, often on the next print line."""
    for yy in range(max(0, y - 1), min(len(lines), y + 4)):
        left = lines[yy][max(0, x - 26) : x]
        m = re.search(r"([+-]\d{1,12})", left)
        if m:
            return _parse_const(m.group(1))
    return None


def _left_has_const(lines: list[str], x: int, y: int) -> bool:
    for yy in range(max(0, y - 1), min(len(lines), y + 2)):
        if "CONST" in lines[yy][max(0, x - 26) : x + 2]:
            return True
    return False


def _find_operand_near(lines: list[str], x: int, y: int, conv: Conversion) -> str:
    """Find CONST or %ref near a function pin at column x."""
    if _left_has_const(lines, x, y):
        const = _find_const_left(lines, x, y)
        if const is not None:
            return const
    lo, hi = max(0, x - 18), min(len(lines[0]) if lines else 0, x + 2)
    for yy in range(max(0, y - 1), min(len(lines), y + 8)):
        line = lines[yy]
        refs = _refs_on_line(line)
        for start, ref in refs:
            if lo <= start < x:
                return ensure_tag(conv, ref)
        if "CONST" in line[max(0, lo - 8) : x + 2]:
            const = _find_const_left(lines, x, yy)
            if const is not None:
                return const
    return "0"


@dataclass
class FuncBox:
    x: int
    name: str
    ftype: str = ""
    pins: dict[str, str] = field(default_factory=dict)
    enable_out: bool = True


def _scan_func_boxes(lines: list[str], conv: Conversion) -> list[FuncBox]:
    boxes: list[FuncBox] = []
    padded = _pad(lines)
    for y, line in enumerate(padded):
        matches: list[tuple[str, int]] = [(m.group(1).strip(), m.start()) for m in FUNC_SCAN_RE.finditer(line)]
        for m in re.finditer(r">UPCTR", line):
            matches.append(("UPCTR", m.start()))
        for name, x in matches:
            ftype = ""
            if y + 1 < len(padded):
                nxt = padded[y + 1][x : x + 12]
                ftype = collapse_desc(re.sub(r"[│┌└┘┐├┤─]", " ", nxt))
            if y + 2 < len(padded) and not ftype:
                nxt = padded[y + 2][x : x + 12]
                ftype = collapse_desc(re.sub(r"[│┌└┘┐├┤─]", " ", nxt))
            # second type line (e.g. CLR_ / WORD, SET_ / WORD, TO_ / BCD4, REQ)
            extra = ""
            if y + 2 < len(padded):
                extra = collapse_desc(re.sub(r"[│┌└┘┐├┤─]", " ", padded[y + 2][x : x + 12]))
            if extra and extra not in ftype:
                ftype = collapse_desc(f"{ftype} {extra}")
            box = FuncBox(x=x, name=name, ftype=ftype)
            # pins on following lines
            for yy in range(y, min(len(padded), y + 16)):
                seg = padded[yy]
                # look for pin labels in the box column range
                region = seg[x : x + 16]
                pin_m = re.search(
                    r"┤(IN1|IN2|IN3|IN4|IN5|IN6|IN7|IN|I1|I2|Q|PV|LEN|BIT|ST|END|ALT|FNC|PARM|SYSID|TASK|L1|L2|FT|N|B1|B2|R)\s*",
                    region,
                )
                if pin_m:
                    pin = pin_m.group(1)
                    px = x + pin_m.start()
                    if pin in {"IN", "I1", "IN1", "IN2", "IN3", "IN4", "IN5", "IN6", "IN7", "PV", "BIT", "ST", "FNC", "PARM", "L1", "L2", "I2"}:
                        box.pins[pin] = _find_operand_near(padded, px, yy, conv)
                    if pin == "R":
                        op = _assoc_operand(padded, px, yy)
                        if op:
                            box.pins["R"] = ensure_tag(conv, op)
                    if pin in {"Q"} or ("Q" in region[:8] and "IN  Q" in region):
                        # output often to the right of the box
                        right = seg[x + 6 : x + 28]
                        rm = REF_TOKEN_RE.search(right)
                        if rm:
                            box.pins["Q"] = ensure_tag(conv, rm.group(0))
                        else:
                            # Q may be on same IN  Q line to the right
                            for start, ref in _refs_on_line(seg):
                                if start > x + 4:
                                    box.pins["Q"] = ensure_tag(conv, ref)
                                    break
                    if pin == "I2":
                        box.pins["I2"] = _find_operand_near(padded, px, yy, conv)
                # timer register below box
                if name in {"TMR", "OFDT", "ONDTR", "UPCTR"}:
                    for start, ref in _refs_on_line(seg):
                        if x - 2 <= start <= x + 14 and ge_ref_to_tag(ref).startswith("R"):
                            box.pins.setdefault("TREG", ensure_tag(conv, ref))
                # CALL target
                if name.startswith("CALL"):
                    target = name.replace("CALL", "").strip()
                    box.pins["ROUTINE"] = target
            # IN  Q combined
            if "IN  Q" in line[x : x + 16] or any("IN  Q" in padded[yy][x : x + 16] for yy in range(y, min(len(padded), y + 12))):
                for yy in range(y, min(len(padded), y + 12)):
                    if "┤IN  Q├" in padded[yy][x - 2 : x + 18] or "┤IN  Q" in padded[yy][x : x + 16]:
                        left = padded[yy][max(0, x - 18) : x + 2]
                        right = padded[yy][x + 6 : x + 24]
                        lm = REF_TOKEN_RE.search(left)
                        rm = REF_TOKEN_RE.search(right)
                        if "CONST" in left:
                            box.pins.setdefault("IN", _find_operand_near(padded, x, yy, conv))
                        elif lm:
                            box.pins.setdefault("IN", ensure_tag(conv, lm.group(0)))
                        if rm:
                            box.pins.setdefault("Q", ensure_tag(conv, rm.group(0)))
                    if "┤I1  Q├" in padded[yy][x - 2 : x + 18] or "┤I1  Q" in padded[yy][x : x + 16]:
                        left = padded[yy][max(0, x - 18) : x + 2]
                        right = padded[yy][x + 6 : x + 24]
                        lm = REF_TOKEN_RE.search(left)
                        rm = REF_TOKEN_RE.search(right)
                        if lm:
                            box.pins.setdefault("I1", ensure_tag(conv, lm.group(0)))
                        if rm:
                            box.pins.setdefault("Q", ensure_tag(conv, rm.group(0)))
                        if "CONST" in left:
                            box.pins.setdefault("I1", _find_operand_near(padded, x, yy, conv))
            boxes.append(box)
    uniq: dict[int, FuncBox] = {}
    for b in boxes:
        uniq[b.x] = b
    result = [uniq[k] for k in sorted(uniq)]
    _bind_function_pins(_pad(lines), result, conv)
    return result


def _bind_function_pins(padded: list[str], boxes: list[FuncBox], conv: Conversion) -> None:
    if not boxes:
        return

    def nearest(px: int) -> FuncBox | None:
        box = min(boxes, key=lambda b: abs(b.x - px))
        return box if abs(box.x - px) <= 14 else None

    pin_re = re.compile(
        r"┤\s*(IN1|IN2|IN3|IN4|IN5|IN6|IN7|IN|I1|I2|PV|L1|L2|FNC|PARM|BIT|ST|END|ALT|N|R)\s*(Q)?\s*├?"
    )
    for y, line in enumerate(padded):
        for m in pin_re.finditer(line):
            box = nearest(m.start())
            if box is None:
                continue
            pin = m.group(1)
            has_q = m.group(2) == "Q"
            left = line[max(0, m.start() - 22) : m.start()]
            right = line[m.end() : m.end() + 18]
            left_ref = REF_TOKEN_RE.search(left)
            right_ref = REF_TOKEN_RE.search(right)
            if "CONST" in left or _left_has_const(padded, m.start(), y):
                val = _find_const_left(padded, m.start(), y)
                if val is None:
                    val = _find_operand_near(padded, m.start(), y, conv)
                if pin in {"IN", "IN1"}:
                    box.pins["IN"] = val
                    box.pins[pin] = val
                else:
                    box.pins[pin] = val
            elif left_ref:
                name = ensure_tag(conv, left_ref.group(0))
                box.pins[pin] = name
                if pin in {"IN", "IN1"}:
                    box.pins["IN"] = name
            if has_q:
                if not right_ref:
                    for yy in range(y, min(len(padded), y + 3)):
                        chunk = padded[yy][m.end() : m.end() + 28]
                        right_ref = REF_TOKEN_RE.search(chunk)
                        if right_ref:
                            break
                if right_ref:
                    box.pins["Q"] = ensure_tag(conv, right_ref.group(0))
            elif pin == "Q" and right_ref:
                box.pins.setdefault("Q", ensure_tag(conv, right_ref.group(0)))
            if pin == "R":
                op = _assoc_operand(padded, m.start(), y)
                if op:
                    box.pins["R"] = ensure_tag(conv, op)
        # LEN
        for box in boxes:
            region = line[box.x : box.x + 12]
            lm = re.search(r"0(\d{4})", region)
            if lm and "LEN" in "".join(padded[max(0, y - 2) : y + 1]):
                try:
                    box.pins.setdefault("LEN", str(int(lm.group(1))))
                except ValueError:
                    pass


def _scan_contacts_coils(lines: list[str], conv: Conversion) -> tuple[list[Contact], list[Coil]]:
    padded = _pad([_norm_box(l) for l in lines])
    contacts: list[Contact] = []
    coils: list[Coil] = []
    for y, line in enumerate(padded):
        if "├" not in line and "( " not in line and "(S)" not in line and "(R)" not in line:
            if "(^)" not in line and "(/)" not in line and "(M)" not in line and "(v)" not in line:
                continue
        for m in CONTACT_NC.finditer(line):
            x = m.start()
            op = _assoc_operand(padded, x, y)
            if op:
                contacts.append(Contact(x=x, y=y, nc=True, operand=ensure_tag(conv, op)))
        for m in CONTACT_NO.finditer(line):
            x = m.start()
            # skip if this is actually NC (┤/├ contains ┤ then / then ├)
            if x + 1 < len(line) and line[x + 1] == "/":
                continue
            op = _assoc_operand(padded, x, y)
            if op:
                contacts.append(Contact(x=x, y=y, nc=False, operand=ensure_tag(conv, op)))
        for m in COIL_RE.finditer(line):
            kind_ch = m.group(1)
            x = m.start()
            kind = {
                " ": "OTE",
                "S": "OTL",
                "R": "OTU",
                "/": "NEG",
                "^": "PCOIL",
                "SM": "OTL",
                "RM": "OTU",
                "M": "OTE",
                "v": "NTRANS",
            }.get(kind_ch, "OTE")
            op = _assoc_operand(padded, x, y)
            if op:
                coils.append(Coil(x=x, y=y, kind=kind, operand=ensure_tag(conv, op)))
    return contacts, coils


def _xic(c: Contact) -> str:
    return f"XIO({c.operand})" if c.nc else f"XIC({c.operand})"


def _emit_coil(coil: Coil, cond: str, conv: Conversion, ge_n: int, block: str) -> list[str]:
    texts = []
    if coil.kind == "OTE":
        texts.append(f"{cond}OTE({coil.operand});")
    elif coil.kind == "OTL":
        texts.append(f"{cond}OTL({coil.operand});")
    elif coil.kind == "OTU":
        texts.append(f"{cond}OTU({coil.operand});")
    elif coil.kind == "NEG":
        # Q = NOT(cond). Invert by wrapping with a holding bit.
        hold = ensure_bool(conv, f"NEG_{block}_{ge_n}_{coil.operand}", "Negated GE coil hold")
        if cond:
            texts.append(f"{cond}OTE({hold});")
        else:
            texts.append(f"OTE({hold});")
        texts.append(f"XIO({hold})OTE({coil.operand});")
    elif coil.kind == "PCOIL":
        osr = ensure_bool(conv, f"OSR_{coil.operand}", f"One-shot storage for {coil.operand}")
        texts.append(f"{cond}ONS({osr})OTE({coil.operand});")
    elif coil.kind == "NTRANS":
        hold = ensure_bool(conv, f"NT_{block}_{ge_n}_{coil.operand}", f"Falling-edge hold for {coil.operand}")
        osr = ensure_bool(conv, f"OSF_{coil.operand}", f"Falling one-shot storage for {coil.operand}")
        if cond:
            texts.append(f"{cond}OTE({hold});")
        else:
            texts.append(f"OTE({hold});")
        texts.append(f"XIO({hold})ONS({osr})OTE({coil.operand});")
    else:
        texts.append(f"{cond}OTE({coil.operand});")
    return texts


def _series_contacts(contacts: list[Contact]) -> str:
    return "".join(_xic(c) for c in sorted(contacts, key=lambda c: (c.y, c.x)))


def _boolean_texts(rung: RawRung, conv: Conversion) -> list[str] | None:
    lines = [_norm_box(l) for l in rung.lines]
    contacts, coils = _scan_contacts_coils(lines, conv)
    if not coils:
        return None
    padded = _pad(lines)
    # Group contacts by rail row. OR-join if a lower rail ends with ┘ at a ┬ column.
    rails: dict[int, list[Contact]] = {}
    for c in contacts:
        rails.setdefault(c.y, []).append(c)
    if not rails:
        # coils with no contacts (should not happen often)
        texts = []
        for coil in coils:
            texts.extend(_emit_coil(coil, "", conv, rung.ge_number, rung.block))
        return texts

    main_y = min(rails)
    main = sorted(rails[main_y], key=lambda c: c.x)
    or_groups: list[list[Contact]] = []
    for y, cs in rails.items():
        if y == main_y:
            continue
        line = padded[y] if y < len(padded) else ""
        if "┘" in line:
            or_groups.append(sorted(cs, key=lambda c: c.x))
        elif "<+>" in line[:12]:
            main.extend(sorted(cs, key=lambda c: c.x))
        else:
            # extra series contacts on other rows feeding the same AND (rare) — OR them
            or_groups.append(sorted(cs, key=lambda c: c.x))

    if or_groups:
        # Find split point: contacts on main rail left of first ┬ vs right
        split_x = None
        if main_y < len(padded) and "┬" in padded[main_y]:
            split_x = padded[main_y].find("┬")
        left = [c for c in main if split_x is None or c.x < split_x]
        right = [c for c in main if split_x is not None and c.x > split_x]
        parallel = []
        if left:
            parallel.append(_series_contacts(left))
        else:
            parallel.append("")
        for grp in or_groups:
            parallel.append(_series_contacts(grp))
        # drop empty
        parallel = [p for p in parallel if p]
        if len(parallel) == 1:
            cond = parallel[0]
        else:
            inner = ",".join(parallel)
            cond = f"[{inner}]"
        cond += _series_contacts(right)
    else:
        cond = _series_contacts(main)

    texts: list[str] = []
    for coil in sorted(coils, key=lambda c: (c.y, c.x)):
        texts.extend(_emit_coil(coil, cond, conv, rung.ge_number, rung.block))
    return texts


def _timebase_ms(box: FuncBox, lines: list[str]) -> int:
    blob = "\n".join(lines)
    if "0.01s" in blob or "0.01 s" in blob:
        unit = 10
    elif "1.00s" in blob or "1.0s" in blob:
        unit = 1000
    else:
        unit = 100  # 0.10s default from this mill program
    pv = box.pins.get("PV", "0")
    try:
        return int(pv) * unit
    except ValueError:
        return unit


def _cmp_mnemo(name: str) -> str:
    return {
        "EQ_": "EQU",
        "NE_": "NEQ",
        "GT_": "GRT",
        "GE_": "GEQ",
        "LT_": "LES",
        "LE_": "LEQ",
    }[name]


def _is_dint(ftype: str) -> bool:
    return "DINT" in ftype.upper()


def _operand_datatype(conv: Conversion, name: str) -> str:
    if name in conv.tags:
        return conv.tags[name].datatype
    if name in conv.aliases:
        return conv.aliases[name].datatype
    if re.match(r"(?:R|AI|AQ)\d+$", name):
        return "INT"
    return "BOOL"


def _src(val: str, dint: bool, conv: Conversion) -> str:
    if val.lstrip("+-").isdigit():
        return val
    if dint and re.match(r"R\d+$", val.replace("_", "")):
        base = val if val in conv.tags else ge_ref_to_tag(val)
        if base.startswith("R") and base[1:].isdigit():
            return ensure_dint(conv, base)
    return val
    if val.lstrip("+-").isdigit():
        return val
    if dint and re.match(r"R\d+$", val.replace("_", "")):
        base = val if val in conv.tags else ge_ref_to_tag(val)
        if base.startswith("R") and base[1:].isdigit():
            return ensure_dint(conv, base)
    return val


def _func_instruction(box: FuncBox, lines: list[str], conv: Conversion) -> str | None:
    n = box.name
    ft = box.ftype.upper()
    dint = _is_dint(ft)
    if n.startswith("CALL"):
        target = box.pins.get("ROUTINE") or n.replace("CALL", "").strip()
        target = target.replace(" ", "")
        if target:
            return f"JSR({target},0)"
        return None
    if n == "MOVE_":
        src = box.pins.get("IN", "0")
        dest = box.pins.get("Q")
        if dest:
            try:
                length = int(box.pins.get("LEN", "1"))
            except ValueError:
                length = 1
            if length > 1 and not src.lstrip("+-").isdigit():
                if _operand_datatype(conv, src) == "BOOL" and _operand_datatype(conv, dest) == "BOOL":
                    return f"BOOLBLK|{src}|{dest}|{length}"
                return f"COP({src},{dest},{length})"
            if _operand_datatype(conv, dest) == "BOOL":
                if src.lstrip("+-").isdigit():
                    return f"OTE({dest})" if int(src) else f"OTU({dest})"
                if _operand_datatype(conv, src) == "BOOL":
                    return f"BOOLCPY|{src}|{dest}"
                return f"INT2BOOL|{src}|{dest}"
            return f"MOV({_src(src, dint, conv)},{_src(dest, dint, conv)})"
        return None
    if n == "BLKMV":
        dest = box.pins.get("Q") or box.pins.get("IN1")
        if not dest:
            return None
        parts = []
        # IN1 is often the first constant when CONST ─┤IN1 Q├─dest
        first = box.pins.get("IN") or box.pins.get("IN1")
        values = []
        if first and first != dest:
            values.append(first)
        for i in range(2, 8):
            if f"IN{i}" in box.pins:
                values.append(box.pins[f"IN{i}"])
        cur = dest
        if not values:
            src = box.pins.get("IN") or box.pins.get("IN1")
            if src and dest:
                return f"COP({src},{dest},1)"
            return None
        for val in values:
            parts.append(f"MOV({val},{cur})")
            if re.match(r"[A-Z]+\d+$", cur):
                cur = _next_reg(cur)
        return "".join(parts)
    if n == "BLK_" and "CLR" in ft:
        dest = box.pins.get("IN") or box.pins.get("Q")
        if dest:
            try:
                length = int(box.pins.get("LEN", "1"))
            except ValueError:
                length = 1
            if length > 1:
                return f"FLL(0,{dest},{length})"
            return f"MOV(0,{dest})"
        return None
    if n in {"ADD_", "SUB_", "MUL_", "DIV_"}:
        inst = n.replace("_", "")
        a = _src(box.pins.get("I1") or box.pins.get("IN1") or box.pins.get("IN", "0"), dint, conv)
        b = _src(box.pins.get("I2") or box.pins.get("IN2", "0"), dint, conv)
        q = _src(box.pins.get("Q", a), dint, conv)
        return f"{inst}({a},{b},{q})"
    if n in {"EQ_", "NE_", "GT_", "GE_", "LT_", "LE_"}:
        a = _src(box.pins.get("I1") or box.pins.get("IN1") or box.pins.get("IN", "0"), dint, conv)
        b = _src(box.pins.get("I2") or box.pins.get("IN2", "0"), dint, conv)
        return f"{_cmp_mnemo(n)}({a},{b})"
    if n in {"TMR", "OFDT", "ONDTR"}:
        treg = box.pins.get("TREG", f"R{box.x}")
        base = ge_ref_to_tag(treg) if treg.startswith("R") or treg.startswith("%") else treg
        timer = ensure_timer(conv, base)
        pre = _timebase_ms(box, lines)
        inst = {"TMR": "TON", "OFDT": "TOF", "ONDTR": "RTO"}[n]
        return f"{inst}({timer},{pre},0)"
    if n == "UPCTR":
        treg = box.pins.get("TREG", f"R{box.x}")
        base = ge_ref_to_tag(treg) if treg.startswith("R") or treg.startswith("%") else treg
        ctr = ensure_counter(conv, base)
        pv = box.pins.get("PV", "0")
        try:
            pre = int(pv)
        except ValueError:
            pre = 0
        return f"CTU({ctr},{pre},0)"
    if n in {"AND_", "OR_"}:
        inst = n.replace("_", "")
        a = box.pins.get("I1") or box.pins.get("IN", "0")
        b = box.pins.get("I2", "0")
        q = box.pins.get("Q", a)
        return f"{inst}({a},{b},{q})"
    if n == "NOT_":
        a = box.pins.get("IN") or box.pins.get("I1", "0")
        q = box.pins.get("Q", a)
        return f"NOT({a},{q})"
    if n == "SHL_":
        a = box.pins.get("IN") or box.pins.get("I1", "0")
        b = box.pins.get("N") or box.pins.get("I2", "1")
        q = box.pins.get("Q", a)
        return f"SHL({a},{b},{q})"
    if n == "BIT_" and "SET" in ft:
        dest = box.pins.get("IN")
        if dest:
            return f"OTL({dest})"
        return None
    if n == "BIT_" and "CLR" in ft:
        dest = box.pins.get("IN")
        if dest:
            return f"OTU({dest})"
        return None
    if n == "RANGE":
        lo = box.pins.get("L1", "0")
        hi = box.pins.get("L2", "0")
        inn = box.pins.get("IN", "0")
        try:
            a, b = int(lo), int(hi)
            lo, hi = str(min(a, b)), str(max(a, b))
        except ValueError:
            pass
        return f"LIM({lo},{inn},{hi})"
    if n == "INT_" and "BCD" in ft:
        src = box.pins.get("IN", "0")
        dest = box.pins.get("Q", src)
        return f"TOD({src},{dest})"
    if n in {"DO_IO", "COMM_", "SVC_"}:
        return "NOP()"
    return None


def _has_func(lines: list[str]) -> bool:
    blob = "\n".join(lines)
    return any(
        s in blob
        for s in (
            "┤MOVE_",
            "┤ BLK_",
            "┤BLKMV",
            "┤ ADD_",
            "┤ SUB_",
            "┤ MUL_",
            "┤ DIV_",
            "┤ EQ_",
            "┤ NE_",
            "┤ GT_",
            "┤ GE_",
            "┤ LT_",
            "┤ LE_",
            "┤ TMR",
            "┤OFDT",
            "┤ONDTR",
            "┤ AND_",
            "┤ OR_",
            "┤ NOT_",
            "┤ SHL_",
            "┤ BIT_",
            "┤DO_IO",
            "┤RANGE",
            "┤ INT_",
            "┤COMM_",
            "┤ SVC_",
            "┤ CALL",
            ">UPCTR",
        )
    )


def _has_cont_in(lines: list[str]) -> bool:
    return any(re.search(r"[├╟]<\+>", _norm_box(l)) for l in lines)


def _has_cont_out(lines: list[str]) -> bool:
    for line in lines:
        n = _norm_box(line)
        if "<+>" not in n:
            continue
        if re.search(r"[├╟]<\+>", n):
            # left-rail continuation may also continue out on the same line
            if n.rstrip().endswith("<+>") and n.find("<+>") > 8:
                return True
            continue
        return True
    return False


def _r_pin_rows(lines: list[str], box_x: int) -> set[int]:
    rows: set[int] = set()
    padded = _pad(lines)
    for y, line in enumerate(padded):
        region = line[box_x : box_x + 12]
        if re.search(r"┤R\b", region) or "┤R " in region or region.strip().startswith("┤R"):
            rows.add(y)
    return rows


def _wrap_rung(ge_n: int, comment: str, source: str, text: str, mapped: bool = True, notes: list[str] | None = None) -> LogixRung:
    if not text.endswith(";"):
        text += ";"
    return LogixRung(ge_number=ge_n, comment=comment, text=text, source=source, mapped=mapped, notes=notes or [])


def convert_rung(rung: RawRung, conv: Conversion, prev: RawRung | None = None) -> list[LogixRung]:
    lines = [_norm_box(l) for l in rung.lines]
    comment_parts = [f"GE Rung {rung.ge_number}"]
    if rung.comment:
        comment_parts.append(rung.comment)
    comment = " — ".join(comment_parts)
    source = "\n".join(rung.lines)
    blob = "\n".join(lines)
    cont_in = ""
    if _has_cont_in(lines) and prev is not None and prev.block == rung.block:
        cont_in = ensure_bool(conv, cont_tag(rung.block, prev.ge_number), "GE continuation link")
    elif _has_cont_in(lines):
        cont_in = ensure_bool(conv, cont_tag(rung.block, max(rung.ge_number - 1, 0)), "GE continuation link")

    lbl = re.search(r"├\s*([A-Z][A-Z0-9_]+)\s*:\s*\(nested\)", blob)
    if lbl:
        return [_wrap_rung(rung.ge_number, comment, source, f"LBL({lbl.group(1)});")]
    jmp = re.search(r">>\s*([A-Z][A-Z0-9_]+)", blob)
    if jmp and "RUNG" not in (jmp.group(1) or ""):
        contacts, _coils = _scan_contacts_coils(lines, conv)
        cond = _boolean_enable_only(rung, conv, 10_000, exclude_rows=set()) or _series_contacts(contacts)
        if cont_in:
            cond = f"XIC({cont_in})" + cond
        return [_wrap_rung(rung.ge_number, comment, source, f"{cond}JMP({jmp.group(1)});")]

    if not any(l.strip() for l in lines):
        return [
            _wrap_rung(
                rung.ge_number,
                comment,
                source,
                "NOP();",
                notes=["empty / comment-only GE rung"],
            )
        ]

    out: list[LogixRung] = []

    if _has_func(lines) or any("CALL" in l for l in lines):
        boxes = _scan_func_boxes(lines, conv)
        contacts, coils = _scan_contacts_coils(lines, conv)
        reset_rows: set[int] = set()
        if boxes:
            first_x = min(b.x for b in boxes)
            for box in boxes:
                if box.name in TIMER_FUNCS | COUNTER_FUNCS:
                    reset_rows |= _r_pin_rows(lines, box.x)
            enable = _boolean_enable_only(rung, conv, first_x, exclude_rows=reset_rows) or ""
        else:
            enable = _series_contacts(contacts)
        if cont_in:
            enable = f"XIC({cont_in})" + enable

        insts: list[tuple[FuncBox, str]] = []
        unmapped: list[str] = []
        for box in boxes:
            ins = _func_instruction(box, lines, conv)
            if ins:
                insts.append((box, ins))
                if box.name in {"DO_IO", "COMM_", "SVC_"}:
                    unmapped.append(box.name)
            else:
                unmapped.append(box.name)

        texts: list[str] = []
        notes: list[str] = []
        hardware = {"DO_IO", "COMM_", "SVC_"}
        hw = [n for n in unmapped if n in hardware]
        real_unmapped = [n for n in unmapped if n not in hardware]
        mapped = not real_unmapped
        if hw:
            notes.append(
                "review GE hardware functions: " + ", ".join(hw) + " (NOP — replace with MSG/GSV/IOT)"
            )
        if real_unmapped:
            notes.append("unmapped GE functions: " + ", ".join(real_unmapped))

        input_insts = [ins for box, ins in insts if box.name.split()[0] in INPUT_FUNCS or box.name in INPUT_FUNCS]
        output_pairs = [(box, ins) for box, ins in insts if box.name.split()[0] not in INPUT_FUNCS and box.name not in INPUT_FUNCS]

        if input_insts and coils:
            cond = enable + "".join(input_insts)
            for coil in coils:
                texts.extend(_emit_coil(coil, cond, conv, rung.ge_number, rung.block))
        elif input_insts and not output_pairs:
            texts.append(enable + "".join(input_insts) + "NOP();")

        for box, ins in output_pairs:
            if box.name in TIMER_FUNCS:
                texts.append(f"{enable}{ins};")
                treg = box.pins.get("TREG", "")
                base = ge_ref_to_tag(treg) if treg else ""
                timer = f"{base}_TMR" if base else ""
                rst = box.pins.get("R")
                if rst:
                    texts.append(f"XIC({rst})RES({timer});")
                if coils and timer:
                    for coil in coils:
                        if coil.kind == "NEG":
                            texts.append(f"XIO({timer}.DN)OTE({coil.operand});")
                        elif coil.kind == "OTL":
                            texts.append(f"XIC({timer}.DN)OTL({coil.operand});")
                        elif coil.kind == "OTU":
                            texts.append(f"XIC({timer}.DN)OTU({coil.operand});")
                        else:
                            texts.append(f"XIC({timer}.DN)OTE({coil.operand});")
                    coils = []
            elif box.name in COUNTER_FUNCS:
                texts.append(f"{enable}{ins};")
                treg = box.pins.get("TREG", "")
                base = ge_ref_to_tag(treg) if treg else ""
                ctr = f"{base}_CTR" if base else ""
                rst = box.pins.get("R")
                if rst:
                    texts.append(f"XIC({rst})RES({ctr});")
                if coils and ctr:
                    for coil in coils:
                        texts.append(f"XIC({ctr}.DN)OTE({coil.operand});")
                    coils = []
            else:
                if ins.startswith("BOOLBLK|"):
                    _, src, dest, n = ins.split("|")
                    cur_s, cur_d = src, dest
                    for _ in range(int(n)):
                        texts.append(f"{enable}XIC({cur_s})OTE({cur_d});")
                        texts.append(f"{enable}XIO({cur_s})OTU({cur_d});")
                        cur_s = _next_reg(cur_s)
                        cur_d = _next_reg(cur_d)
                elif ins.startswith("BOOLCPY|"):
                    _, src, dest = ins.split("|")
                    texts.append(f"{enable}XIC({src})OTE({dest});")
                    texts.append(f"{enable}XIO({src})OTU({dest});")
                elif ins.startswith("INT2BOOL|"):
                    _, src, dest = ins.split("|")
                    texts.append(f"{enable}NEQ({src},0)OTE({dest});")
                    texts.append(f"{enable}EQU({src},0)OTU({dest});")
                else:
                    texts.append(f"{enable}{ins};")

        if coils and not input_insts:
            for coil in coils:
                texts.extend(_emit_coil(coil, enable, conv, rung.ge_number, rung.block))

        if _has_cont_out(lines):
            ctag = ensure_bool(conv, cont_tag(rung.block, rung.ge_number), "GE continuation link")
            texts.append(f"{enable}OTE({ctag});")

        if not texts:
            texts = [enable + "NOP();" if enable else "NOP();"]

        for t in texts:
            out.append(_wrap_rung(rung.ge_number, comment, source, t, mapped=mapped, notes=notes))
        if out:
            return out

    bool_texts = _boolean_texts(rung, conv)
    if bool_texts:
        if cont_in:
            bool_texts = [
                t if t.startswith(f"XIC({cont_in})") else f"XIC({cont_in})" + t
                for t in bool_texts
            ]
        if _has_cont_out(lines) and not any("OTE(" + cont_tag(rung.block, rung.ge_number) in t for t in bool_texts):
            ctag = ensure_bool(conv, cont_tag(rung.block, rung.ge_number), "GE continuation link")
            contacts, coils = _scan_contacts_coils(lines, conv)
            if not coils:
                enable = _boolean_enable_only(rung, conv, 10_000) or _series_contacts(contacts)
                if cont_in:
                    enable = f"XIC({cont_in})" + enable
                bool_texts = [f"{enable}OTE({ctag});"]
        for t in bool_texts:
            out.append(_wrap_rung(rung.ge_number, comment, source, t, mapped=True))
        return out

    if "<+>" in blob:
        contacts, coils = _scan_contacts_coils(lines, conv)
        enable = _boolean_enable_only(rung, conv, 10_000) or _series_contacts(contacts)
        if cont_in:
            enable = f"XIC({cont_in})" + enable
        texts = []
        if _has_cont_out(lines) or not coils:
            ctag = ensure_bool(conv, cont_tag(rung.block, rung.ge_number), "GE continuation link")
            texts.append(f"{enable}OTE({ctag});")
        for coil in coils:
            texts.extend(_emit_coil(coil, enable, conv, rung.ge_number, rung.block))
        return [
            _wrap_rung(rung.ge_number, comment, source, t)
            for t in texts
        ]

    return [
        _wrap_rung(
            rung.ge_number,
            comment + "\n" + source,
            source,
            "NOP();",
            mapped=False,
            notes=["unparsed GE rung; original print retained in comment"],
        )
    ]


def _boolean_enable_only(
    rung: RawRung,
    conv: Conversion,
    max_x: int,
    exclude_rows: set[int] | None = None,
) -> str | None:
    lines = [_norm_box(l) for l in rung.lines]
    contacts, _coils = _scan_contacts_coils(lines, conv)
    exclude_rows = exclude_rows or set()
    contacts = [c for c in contacts if c.x < max_x and c.y not in exclude_rows]
    if not contacts:
        return ""
    padded = _pad(lines)
    rails: dict[int, list[Contact]] = {}
    for c in contacts:
        rails.setdefault(c.y, []).append(c)
    main_y = min(rails)
    main = sorted(rails[main_y], key=lambda c: c.x)
    or_groups = []
    for y, cs in rails.items():
        if y == main_y:
            continue
        line = padded[y] if y < len(padded) else ""
        if "<+>" in line[:12]:
            main.extend(sorted(cs, key=lambda c: c.x))
        else:
            or_groups.append(sorted(cs, key=lambda c: c.x))
    split_x = None
    if main_y < len(padded) and "┬" in padded[main_y]:
        bx = padded[main_y].find("┬")
        if bx < max_x:
            split_x = bx
    left = [c for c in main if split_x is None or c.x < split_x]
    right = [c for c in main if split_x is not None and c.x > split_x]
    if or_groups:
        parallel = []
        if left:
            parallel.append(_series_contacts(left))
        for grp in or_groups:
            parallel.append(_series_contacts(grp))
        parallel = [p for p in parallel if p]
        cond = f"[{','.join(parallel)}]" if len(parallel) > 1 else (parallel[0] if parallel else "")
        cond += _series_contacts(right)
        return cond
    return _series_contacts(main)


def build_conversion(raw: RawProgram) -> Conversion:
    conv = Conversion(subroutines=raw.identifiers)
    for d in raw.decls:
        tag = ge_ref_to_tag(d.ref)
        conv.tags[tag] = TagDef(name=tag, datatype=datatype_for(tag), description=d.description)
        if d.nickname:
            alias = sanitize_alias(d.nickname)
            if alias == tag:
                continue
            if alias in conv.tags:
                alias = f"{alias}_{tag}"
            conv.aliases[alias] = TagDef(
                name=alias,
                datatype=conv.tags[tag].datatype,
                description=d.description,
                alias_for=tag,
            )
    for key, (nick, desc) in KNOWN_SYSTEM.items():
        ensure_tag(conv, key, desc)

    # collect refs from rungs
    for rung in raw.rungs:
        blob = "\n".join(rung.lines)
        for m in REF_RE.finditer(blob):
            ensure_tag(conv, m.group(0))

    blocks: dict[str, BlockDef] = {}
    for name in raw.block_order:
        desc = raw.identifiers.get(name, "")
        if name == "_MAIN":
            desc = desc or "GE _MAIN block"
        blocks[name] = BlockDef(name="MainRoutine" if name == "_MAIN" else name, description=desc)

    prev_by_block: dict[str, RawRung] = {}
    for rung in raw.rungs:
        key = rung.block
        routine = "MainRoutine" if key == "_MAIN" else key
        if routine not in {b.name for b in blocks.values()}:
            blocks[key] = BlockDef(name=routine, description=raw.identifiers.get(key, ""))
        converted = convert_rung(rung, conv, prev=prev_by_block.get(key))
        prev_by_block[key] = rung
        target = next(b for b in blocks.values() if b.name == routine)
        for lr in converted:
            target.rungs.append(lr)
            if lr.mapped:
                conv.mapped_rungs += 1
            else:
                conv.unmapped_rungs += 1

    # SYSBITS routine first
    sys_rungs = _sysbit_rungs(conv)
    sys_block = BlockDef(name="SYSBITS", description="GE %S/%SA/%SC emulation", rungs=sys_rungs)
    ordered: list[BlockDef] = [sys_block]
    # keep GE order
    seen = {"SYSBITS"}
    for name in raw.block_order:
        routine = "MainRoutine" if name == "_MAIN" else name
        blk = next((b for b in blocks.values() if b.name == routine), None)
        if blk and blk.name not in seen:
            ordered.append(blk)
            seen.add(blk.name)
    for blk in blocks.values():
        if blk.name not in seen:
            ordered.append(blk)
            seen.add(blk.name)
    conv.blocks = ordered
    main = next((b for b in conv.blocks if b.name == "MainRoutine"), None)
    if main is not None:
        main.rungs.insert(
            0,
            LogixRung(
                ge_number=0,
                comment="GE Rung 0 — run system-bit emulation before _MAIN",
                text="JSR(SYSBITS,0);",
                mapped=True,
            ),
        )
    return conv


def _sysbit_rungs(conv: Conversion) -> list[LogixRung]:
    ensure_bool(conv, "FirstScanDone", "Set after first controller scan")
    ensure_tag(conv, "S0001")
    ensure_tag(conv, "S0007")
    ensure_tag(conv, "S0008")
    ensure_tag(conv, "S0004")
    ensure_tag(conv, "S0005")
    ensure_tag(conv, "S0006")
    ensure_timer(conv, "CLK100")
    ensure_timer(conv, "CLKSEC")
    ensure_timer(conv, "CLKMIN")
    fst = resolve_name("S0001", conv)
    alw = resolve_name("S0007", conv)
    t100 = resolve_name("S0004", conv)
    tsec = resolve_name("S0005", conv)
    tmin = resolve_name("S0006", conv)
    return [
        LogixRung(0, "GE Rung 0 — emulate FST_SCN (%S0001)", f"XIO(FirstScanDone)OTE({fst});", mapped=True),
        LogixRung(0, "GE Rung 0 — latch first-scan complete", "OTE(FirstScanDone);", mapped=True),
        LogixRung(0, "GE Rung 0 — emulate ALW_ON (%S0007)", f"OTE({alw});", mapped=True),
        LogixRung(0, "GE Rung 0 — emulate T_100MS (%S0004) timer", "TON(CLK100_TMR,100,0);", mapped=True),
        LogixRung(0, "GE Rung 0 — emulate T_100MS (%S0004) pulse", f"XIC(CLK100_TMR.DN)OTE({t100});", mapped=True),
        LogixRung(0, "GE Rung 0 — emulate T_100MS (%S0004) reset", "XIC(CLK100_TMR.DN)RES(CLK100_TMR);", mapped=True),
        LogixRung(0, "GE Rung 0 — emulate T_SEC (%S0005) timer", "TON(CLKSEC_TMR,1000,0);", mapped=True),
        LogixRung(0, "GE Rung 0 — emulate T_SEC (%S0005) pulse", f"XIC(CLKSEC_TMR.DN)OTE({tsec});", mapped=True),
        LogixRung(0, "GE Rung 0 — emulate T_SEC (%S0005) reset", "XIC(CLKSEC_TMR.DN)RES(CLKSEC_TMR);", mapped=True),
        LogixRung(0, "GE Rung 0 — emulate T_MIN (%S0006) timer", "TON(CLKMIN_TMR,60000,0);", mapped=True),
        LogixRung(0, "GE Rung 0 — emulate T_MIN (%S0006) pulse", f"XIC(CLKMIN_TMR.DN)OTE({tmin});", mapped=True),
        LogixRung(0, "GE Rung 0 — emulate T_MIN (%S0006) reset", "XIC(CLKMIN_TMR.DN)RES(CLKMIN_TMR);", mapped=True),
    ]
