from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import collapse_desc

RUNG_RE = re.compile(r"<< RUNG\s+(\d+)\s+STEP\s+#(\d+)\s*>>")
BLOCK_RE = re.compile(r"\(\*\s+BLOCK:\s+(\S+)\s+\*\)")
REF_RE = re.compile(r"%([A-Z]+\d+)")
DECL_REF_RE = re.compile(r"^\s+(%[A-Z]+\d+)\s+")
IDENT_RE = re.compile(r"^\s+([A-Z][A-Z0-9_]{0,7})\s+SUBROUTINE\s+(.*)$")
PAGE_PROGRAM_RE = re.compile(r"^Program:\s+\S+")
PAGE_DATE_RE = re.compile(r"GE FANUC SERIES")


@dataclass
class Decl:
    ref: str
    nickname: str
    description: str


@dataclass
class RawRung:
    ge_number: int
    step: int
    block: str
    comment: str
    lines: list[str] = field(default_factory=list)


@dataclass
class RawProgram:
    decls: list[Decl]
    identifiers: dict[str, str]
    rungs: list[RawRung]
    block_order: list[str]


PAGE_TITLES = {
    "USS01 90-30 PLC",
    "Temper_Mill",
    "910PIO",
    "PIOPLC",
}

IL_LINE_RE = re.compile(r"^[│| ]*#\d+\s+")
IL_PARAM_RE = re.compile(r"^[│| ]*P\d+:")


def _is_page_header(line: str) -> bool:
    s = line.strip()
    if PAGE_PROGRAM_RE.match(line):
        return True
    if PAGE_DATE_RE.search(line):
        return True
    if s in PAGE_TITLES:
        return True
    if "IL TEXT FOR RUNG CONTINUED" in line:
        return True
    return False


def _is_il_listing(line: str) -> bool:
    s = line.replace("│", " ").replace("|", " ")
    if IL_LINE_RE.match(s) or IL_PARAM_RE.match(s):
        return True
    if "END OF PROGRAM" in line or "END OF SUBR" in line:
        if re.search(r"#\d+", line):
            return True
    return False


def _is_xref_noise(line: str) -> bool:
    s = line.strip()
    if "Cross reference for" in line:
        return True
    if re.search(r"-] ?\[-|-]/\[-|-\(S\)|-\(R\)|-\(\^\)|FBIO\s+", s):
        return True
    if s.startswith("NONE") and "│" not in line:
        return True
    if s.startswith("=========="):
        return True
    return False


def parse_declarations(lines: list[str]) -> list[Decl]:
    decls: list[Decl] = []
    in_table = False
    seen: set[str] = set()
    for line in lines:
        if "V A R I A B L E   D E C L A R A T I O N   T A B L E" in line:
            in_table = True
            continue
        if in_table:
            if "I D E N T I F I E R" in line or "START OF PROGRAM LOGIC" in line or "START OF SUBROUTINE LOGIC" in line:
                in_table = False
                continue
            if _is_page_header(line) or "NO  VARIABLE" in line:
                continue
            m = DECL_REF_RE.match(line)
            if not m:
                continue
            ref = m.group(1)
            nick = line[24:32].strip() if len(line) > 24 else ""
            if nick in {"---------", "--------"}:
                nick = ""
            desc = line[37:].strip() if len(line) > 37 else ""
            if ref in seen:
                if nick or desc:
                    for d in decls:
                        if d.ref == ref:
                            if nick and not d.nickname:
                                d.nickname = nick
                            if desc and not d.description:
                                d.description = collapse_desc(desc)
                            break
                continue
            seen.add(ref)
            decls.append(Decl(ref=ref, nickname=nick, description=collapse_desc(desc)))
    return decls


def parse_identifiers(lines: list[str]) -> dict[str, str]:
    idents: dict[str, str] = {}
    in_table = False
    for line in lines:
        if "I D E N T I F I E R    T A B L E" in line:
            in_table = True
            continue
        if in_table:
            if "START OF PROGRAM LOGIC" in line or "START OF SUBROUTINE LOGIC" in line:
                in_table = False
                continue
            if "SUBR " in line and "┌" in "".join(lines[max(0, lines.index(line) - 2) : lines.index(line) + 1] if False else []):
                pass
            if line.strip().startswith("SUBR ") or (line.strip().startswith("┌") or "LANG: LD" in line):
                in_table = False
                continue
            if "NO  IDENTIFIER" in line or _is_page_header(line):
                continue
            m = IDENT_RE.match(line)
            if m:
                idents[m.group(1)] = collapse_desc(m.group(2))
    return idents


def parse_rungs(lines: list[str]) -> tuple[list[RawRung], list[str]]:
    rungs: list[RawRung] = []
    block_order: list[str] = []
    current_block = "_MAIN"
    pending_comment: list[str] = []
    current: RawRung | None = None

    def flush_comment() -> str:
        nonlocal pending_comment
        text = " ".join(pending_comment)
        pending_comment = []
        text = re.sub(r"\(\*+", "", text)
        text = re.sub(r"\*+\)", "", text)
        text = text.replace("│", " ")
        text = collapse_desc(text)
        junk = (
            "BLOCK SIZE",
            "HIGHEST REFERENCE",
            "PROGRAM SIZE",
            "DECLARATIONS (ENTRIES)",
            "PLC PROGRAM ENVIRONMENT",
            "INPUT (%I)",
            "START OF LD",
            "LANG: LD",
        )
        if any(j in text for j in junk):
            return ""
        if text.upper() in {"COMMENT", "COMMENTS"}:
            return ""
        if len(text) > 800:
            text = text[:800] + "…"
        return text

    for line in lines:
        bm = BLOCK_RE.search(line)
        if bm:
            current_block = bm.group(1)
            if current_block not in block_order:
                block_order.append(current_block)
            pending_comment = []
            continue
        m = re.search(r"Block:\s+(\S+)", line)
        if m and not RUNG_RE.search(line):
            name = m.group(1).split("(")[0]
            if name:
                current_block = name
                if current_block not in block_order:
                    block_order.append(current_block)

        if _is_page_header(line):
            continue
        if line.strip().startswith("=========="):
            if current is not None:
                rungs.append(current)
                current = None
            pending_comment = []
            continue

        rm = RUNG_RE.search(line)
        if rm:
            if current is not None:
                rungs.append(current)
            current = RawRung(
                ge_number=int(rm.group(1)),
                step=int(rm.group(2)),
                block=current_block,
                comment=flush_comment(),
            )
            continue

        if "START OF LD" in line or "START LD SUBROUTINE" in line or "START OF SUBROUTINE LOGIC" in line or "START OF PROGRAM LOGIC" in line:
            pending_comment = []
            continue
        if current is None:
            if "(*" in line and "BLOCK:" not in line:
                pending_comment.append(line)
            continue

        if "END OF PROGRAM LOGIC" in line or "END OF SUBROUTINE LOGIC" in line:
            rungs.append(current)
            current = None
            continue

        if _is_xref_noise(line):
            continue
        if "(*" in line and "BLOCK:" not in line and not any(ch in line for ch in "├┤┌└"):
            extra = collapse_desc(re.sub(r"[│()*]", " ", line))
            if extra and extra.upper() not in {"COMMENT", "COMMENTS"}:
                if current.comment:
                    current.comment = collapse_desc(current.comment + " " + extra)
                else:
                    current.comment = extra
            continue
        if _is_il_listing(line):
            continue
        if not line.strip():
            continue
        current.lines.append(line.rstrip())

    if current is not None:
        rungs.append(current)
    if "_MAIN" not in block_order:
        block_order.insert(0, "_MAIN")
    return rungs, block_order


def parse_program(text: str) -> RawProgram:
    lines = text.splitlines()
    decls = parse_declarations(lines)
    idents = parse_identifiers(lines)
    rungs, block_order = parse_rungs(lines)
    return RawProgram(decls=decls, identifiers=idents, rungs=rungs, block_order=block_order)
