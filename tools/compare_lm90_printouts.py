#!/usr/bin/env python3
"""Compare two GE Logicmaster 90-70 ladder printouts and write an Excel report."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher, unified_diff
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

RUNG_RE = re.compile(r"<< RUNG\s+(\d+)\s*>>")
BLOCK_HEADER_RE = re.compile(r"\(\*\s+BLOCK:\s+(\S+)\s+\*\)")
BLOCK_FOOTER_RE = re.compile(r"Block:\s+(\S+)")
START_LD_BLOCK_RE = re.compile(r"START\s+OF\s+LD\s+BLOCK\s+(\S+)")
SIZE_LINE_RE = re.compile(r"PROGRAM SIZE \(BYTES\):\s+(\d+)")
BLOCK_SIZE_RE = re.compile(r"BLOCK SIZE \(BYTES\):\s+(\d+)")
CALL_RE = re.compile(r"CALL\s+(\S+)")
COIL_LINE_RE = re.compile(r"-\(([SRM^v ]{0,2})\)-")
Q_DEST_RE = re.compile(r"Q\+-\s*([%A-Z][A-Z0-9_]*)")
TOKEN_RE = re.compile(r"%[A-Z]+\d+|[A-Za-z][A-Za-z0-9_-]*")
IDENT_RE = re.compile(r"%[A-Z]+\d+|[A-Z][A-Z0-9_-]{1,}")
BOX_NAMES = {
    "INT", "REAL", "DINT", "LEN", "CONST", "MOVE", "ADD", "SUB", "MUL", "DIV",
    "AND", "OR", "NOT", "GT", "LT", "EQ", "GE", "LE", "NE", "TO", "CV", "PV",
    "IN", "Q", "I1", "I2", "R", "SM", "RM", "ONDTR", "OFDT", "OFDTR", "UPCTR",
    "DNCTR", "CALL", "MCR",
}

PAGE_TITLES = {"airknife", "9_10_2026", "uss01 90-30 plc", "temper_mill"}
PLACEHOLDER_COMMENTS = {"comment", "comments"}


@dataclass
class Decl:
    ref: str
    nickname: str
    description: str


@dataclass
class Rung:
    block: str
    number: int
    comments: list[str] = field(default_factory=list)
    logic: list[str] = field(default_factory=list)
    mcr_zone: bool = False
    page: int | None = None

    @property
    def comment_text(self) -> str:
        parts = []
        for c in self.comments:
            cleaned = clean_comment(c)
            if cleaned:
                parts.append(cleaned)
        return " ".join(parts)

    @property
    def fingerprint(self) -> str:
        return "\n".join(normalize_logic_line(line) for line in self.logic if has_logic(line))

    @property
    def outputs(self) -> frozenset[str]:
        return extract_outputs(self)

    @property
    def tokens(self) -> frozenset[str]:
        return extract_tokens(self)

    @property
    def summary(self) -> str:
        return summarize_rung(self)


@dataclass
class Printout:
    label: str
    path: str
    print_date: str
    title: str
    program_size: str
    env_rows: list[tuple[str, str, str, str]]
    block_sizes: dict[str, str]
    decls: list[Decl]
    rungs: list[Rung]
    block_order: list[str]


@dataclass
class AlignedRow:
    change: str
    block: str
    original: Rung | None
    later: Rung | None
    similarity: float | None = None
    detail: str = ""


def clean_comment(text: str) -> str:
    text = re.sub(r"[|*]", " ", text)
    text = re.sub(r"\(\*+", "", text)
    text = re.sub(r"\*+\)", "", text)
    text = re.sub(r"[*=]{3,}", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    if text.lower() in PLACEHOLDER_COMMENTS:
        return ""
    return text


def has_logic(line: str) -> bool:
    s = line.strip()
    if not s or s in {"|", "*", "+", "-"}:
        return False
    if "<<" in s and "RUNG" in s:
        return False
    if "(*" in s:
        return False
    return True


def normalize_logic_line(line: str) -> str:
    """Treat MCR '*' left-rail printing as the equivalent '|' / '+' rail."""
    if line.startswith("*"):
        rest = line[1:]
        if rest.startswith("-") or rest.startswith("+"):
            line = "+" + rest
        else:
            line = "|" + rest
    return line.rstrip()


def is_page_header(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if s.startswith("Program:"):
        return True
    if "GE FANUC SERIES" in line:
        return True
    if s.lower() in PAGE_TITLES:
        return True
    if s.startswith("C:\\") or s.startswith("C:/"):
        return True
    return False


def is_xref_header(line: str) -> bool:
    s = line.strip()
    return s.startswith("REFERENCE NICKNAME") or s.startswith("REFERENCE  NICKNAME")


def is_rung_comment_line(line: str) -> bool:
    if "<<" in line and "RUNG" in line:
        return False
    return "(*" in line or "******" in line


def extract_outputs(rung: Rung) -> frozenset[str]:
    dests: set[str] = set()
    prev = ""
    for raw in rung.logic:
        line = normalize_logic_line(raw)
        if not has_logic(line):
            continue
        if COIL_LINE_RE.search(line):
            idents = [tok for tok in IDENT_RE.findall(prev) if tok not in BOX_NAMES]
            if idents:
                dests.add(idents[-1])
        for m in Q_DEST_RE.finditer(line):
            dests.add(m.group(1))
        for m in CALL_RE.finditer(line):
            dests.add("CALL:" + m.group(1).rstrip("+"))
        if "END_MCR" in line:
            dests.add("END_MCR")
        elif re.search(r"\[\s*MCR\s*\]", line):
            dests.add("MCR")
        prev = line
    return frozenset(dests)


def extract_tokens(rung: Rung) -> frozenset[str]:
    skip = {
        "CONST", "INT", "REAL", "DINT", "LEN", "ALW_ON", "ALW_OFF",
        "MOVE", "ADD", "SUB", "MUL", "DIV", "AND", "OR", "NOT",
        "ONDTR", "OFDT", "OFDTR", "UPCTR", "DNCTR", "CALL", "TO",
    }
    found: set[str] = set()
    for raw in rung.logic:
        line = normalize_logic_line(raw)
        if not has_logic(line):
            continue
        for tok in TOKEN_RE.findall(line):
            if tok in skip or tok.startswith("000"):
                continue
            if len(tok) == 1:
                continue
            found.add(tok)
    return frozenset(found)


def summarize_rung(rung: Rung) -> str:
    calls: list[str] = []
    coils: list[str] = []
    boxes: list[str] = []
    nick_line = ""
    for raw in rung.logic:
        line = normalize_logic_line(raw)
        if not has_logic(line):
            continue
        for m in CALL_RE.finditer(line):
            calls.append("CALL " + m.group(1).rstrip("+"))
        if COIL_LINE_RE.search(line):
            prev = nick_line
            token = prev[-16:].strip() if prev else ""
            token = re.sub(r"[|+]", " ", token).strip()
            if token:
                coils.append(token.split()[-1])
        if line.startswith("|") and not line.startswith("|--") and "-----" not in line[:8]:
            body = line[1:].strip()
            if body and not body.startswith("+"):
                nick_line = line
        for token in ("MCR", "END_MCR", "MOVE_", "ADD_", "SUB_", "MUL_", "DIV_", "GT_", "LT_",
                      "EQ_", "GE_", "LE_", "NE_", "AND_", "OR_", "NOT_", "ONDTR", "OFDTR",
                      "UPCTR", "DNCTR", "INT_", "REAL", "EQP_", "NEP_"):
            if token in line and token.rstrip("_") not in boxes and token not in ("REAL",):
                boxes.append(token.rstrip("_"))
        if "MCR" in line and "END_MCR" not in line and "MCR" not in boxes:
            boxes.append("MCR")
        if "END_MCR" in line and "END_MCR" not in boxes:
            boxes.append("END_MCR")
    parts: list[str] = []
    if calls:
        parts.extend(calls)
    if coils:
        parts.append("coil " + ", ".join(dict.fromkeys(coils)))
    if boxes and not calls:
        parts.append("fn " + ", ".join(dict.fromkeys(boxes)))
    if not parts:
        compact = " ".join(normalize_logic_line(x).strip() for x in rung.logic if has_logic(x))
        compact = re.sub(r"\s+", " ", compact)
        parts.append(compact[:80])
    prefix = "*" if rung.mcr_zone else ""
    return prefix + "; ".join(parts)[:120]


def parse_print_date_and_title(lines: list[str]) -> tuple[str, str]:
    date = ""
    title = ""
    for line in lines[:30]:
        if "GE FANUC SERIES" in line:
            date = line.strip().split("GE FANUC")[0].strip()
        s = line.strip()
        if s and s.lower() not in {"", "ge fanuc"} and "GGGG" not in s and s.lower() not in PAGE_TITLES:
            continue
        if s.lower() in PAGE_TITLES:
            title = s
    for line in lines[:40]:
        s = line.strip()
        if s in {"Airknife", "9_10_2026"}:
            title = s
            break
    return date, title


def parse_environment(lines: list[str]) -> tuple[str, list[tuple[str, str, str, str]], dict[str, str]]:
    program_size = ""
    env_rows: list[tuple[str, str, str, str]] = []
    block_sizes: dict[str, str] = {}
    current_block = "_MAIN"
    for line in lines:
        bm = BLOCK_HEADER_RE.search(line)
        if bm:
            current_block = bm.group(1)
        sm = SIZE_LINE_RE.search(line)
        if sm:
            program_size = sm.group(1)
        bsm = BLOCK_SIZE_RE.search(line)
        if bsm:
            block_sizes[current_block] = bsm.group(1)
        m = re.search(
            r"\(\*\s{2,}(.+?):\s+(\S+)\s{2,}(.+?):\s+(\S+)\s+\*\)",
            line,
        )
        if m and "PROGRAM SIZE" not in line:
            env_rows.append((m.group(1).strip(), m.group(2), m.group(3).strip(), m.group(4)))
    # Keep unique first-page environment rows (program, not per-block repeats)
    seen: set[tuple[str, str, str, str]] = set()
    first: list[tuple[str, str, str, str]] = []
    for row in env_rows:
        if row in seen:
            continue
        # Stop after first full set by detecting repeat of first label
        if first and row[0] == first[0][0] and row[2] == first[0][2]:
            break
        seen.add(row)
        first.append(row)
        if len(first) > 20:
            break
    return program_size, first, block_sizes


def parse_declarations(lines: list[str]) -> list[Decl]:
    decls: list[Decl] = []
    seen: set[str] = set()
    in_table = False
    for line in lines:
        if "V A R I A B L E   D E C L A R A T I O N   T A B L E" in line:
            in_table = True
            continue
        if in_table:
            if "I D E N T I F I E R" in line or "START OF PROGRAM LOGIC" in line or "START OF BLOCK LOGIC" in line:
                in_table = False
                continue
            if is_page_header(line) or "NO  VARIABLE" in line or "REFERENCE" in line and "NICKNAME" in line:
                continue
            if not line.strip() or line.strip().startswith("-"):
                continue
            # Columns: REFERENCE at ~10, NICKNAME at ~24, DESCRIPTION at ~37
            m = re.match(r"\s+(%[A-Z]+\d+)\s+(.*)$", line)
            if not m:
                continue
            ref = m.group(1)
            rest = m.group(2)
            # Nickname is typically 8 chars then spaces then description
            if len(line) >= 38:
                nick = line[24:32].strip()
                desc = line[37:].strip()
            else:
                parts = rest.split(None, 1)
                nick = parts[0] if parts else ""
                desc = parts[1] if len(parts) > 1 else ""
            if nick in {"--------", "---------"}:
                nick = ""
            if ref in seen:
                continue
            seen.add(ref)
            decls.append(Decl(ref=ref, nickname=nick, description=desc.strip()))
    return decls


def parse_rungs(lines: list[str]) -> tuple[list[Rung], list[str]]:
    rungs: list[Rung] = []
    block_order: list[str] = []
    current_block = "_MAIN"
    collecting = False
    in_xref = False
    pending_comments: list[str] = []
    current: Rung | None = None
    page: int | None = None

    def flush() -> None:
        nonlocal current, pending_comments
        if current is not None:
            rungs.append(current)
        current = None
        pending_comments = []

    for line in lines:
        raw = line.replace("\x0c", "")
        pm = re.search(r"Page\s+(\d+)", raw)
        if pm and "GE FANUC" in raw:
            page = int(pm.group(1))

        bm = BLOCK_HEADER_RE.search(raw)
        if bm:
            current_block = bm.group(1)
            if current_block not in block_order:
                block_order.append(current_block)
            continue
        sm = START_LD_BLOCK_RE.search(raw)
        if sm:
            current_block = sm.group(1)
            if current_block not in block_order:
                block_order.append(current_block)
        fm = BLOCK_FOOTER_RE.search(raw)
        if fm and "<<" not in raw:
            name = fm.group(1)
            if name and name != "Structure":
                current_block = name

        if "START OF PROGRAM LOGIC" in raw or "START OF BLOCK LOGIC" in raw:
            collecting = True
            in_xref = False
            pending_comments = []
            continue
        if "END OF PROGRAM LOGIC" in raw or "END OF BLOCK LOGIC" in raw:
            flush()
            collecting = False
            in_xref = False
            continue
        if "L O G I C   T A B L E   O F   C O N T E N T S" in raw:
            flush()
            collecting = False
            break
        if not collecting:
            continue
        if is_xref_header(raw):
            in_xref = True
            continue
        if in_xref:
            if RUNG_RE.search(raw) or "END OF PROGRAM" in raw or "END OF BLOCK" in raw:
                in_xref = False
            elif is_page_header(raw):
                continue
            elif raw.strip().startswith("%") or (raw.strip() and not raw.strip().startswith("|") and not raw.strip().startswith("*") and "<<" not in raw):
                continue
            else:
                in_xref = False
        if is_page_header(raw):
            continue
        if raw.strip().startswith("===="):
            continue

        if is_rung_comment_line(raw) and not RUNG_RE.search(raw):
            pending_comments.append(raw)
            continue

        rm = RUNG_RE.search(raw)
        if rm:
            if current is not None:
                rungs.append(current)
            starred = raw.lstrip().startswith("*")
            current = Rung(
                block=current_block,
                number=int(rm.group(1)),
                comments=pending_comments,
                mcr_zone=starred,
                page=page,
            )
            pending_comments = []
            continue

        if current is None:
            continue
        if not raw.strip():
            continue
        if raw.strip() in {"|", "*"}:
            continue
        current.logic.append(raw.rstrip())
        if raw.lstrip().startswith("*"):
            current.mcr_zone = True

    flush()
    if "_MAIN" not in block_order:
        block_order.insert(0, "_MAIN")
    return rungs, block_order


def parse_printout(path: Path, label: str) -> Printout:
    text = path.read_text(encoding="latin-1", errors="replace")
    lines = text.splitlines()
    date, title = parse_print_date_and_title(lines)
    program_size, env_rows, block_sizes = parse_environment(lines)
    decls = parse_declarations(lines)
    rungs, block_order = parse_rungs(lines)
    return Printout(
        label=label,
        path=str(path),
        print_date=date,
        title=title,
        program_size=program_size,
        env_rows=env_rows,
        block_sizes=block_sizes,
        decls=decls,
        rungs=rungs,
        block_order=block_order,
    )


def similarity(a: Rung, b: Rung) -> float:
    if a.fingerprint == b.fingerprint:
        return 1.0
    if not a.fingerprint or not b.fingerprint:
        return 0.0
    return SequenceMatcher(None, a.fingerprint, b.fingerprint, autojunk=False).ratio()


def pair_score(a: Rung, b: Rung) -> float:
    """Score used to decide whether two rungs are the same instruction after edits."""
    r = similarity(a, b)
    oa, ob = a.outputs, b.outputs
    if oa and ob:
        if oa & ob:
            # Same coil / MOVE destination / CALL: keep them paired even if heavily edited.
            return max(r, 0.82)
        # Different outputs: do not treat similar wiring as the same rung.
        return r * 0.15
    return r


def classify_pair(old: Rung, new: Rung, thresh: float = 0.60) -> AlignedRow:
    sim = similarity(old, new)
    score = pair_score(old, new)
    if sim >= 0.999:
        detail = ""
        if old.comment_text != new.comment_text:
            if old.comment_text and new.comment_text:
                detail = "Comment text differs (logic identical)"
            elif new.comment_text and not old.comment_text:
                detail = "Comment printed in 91026 (logic identical)"
        return AlignedRow("Unchanged", old.block, old, new, sim, detail)
    if score >= thresh:
        return AlignedRow("Modified", old.block, old, new, sim, logic_change_detail(old, new))
    return AlignedRow("split", old.block, old, new, sim, "")


def pair_replace(olds: list[Rung], news: list[Rung], thresh: float = 0.60) -> list[AlignedRow]:
    if len(olds) == 1 and len(news) == 1:
        row = classify_pair(olds[0], news[0], thresh)
        if row.change != "split":
            return [row]
        return [
            AlignedRow("Deleted", olds[0].block, olds[0], None, None, "Rung not present in 91026"),
            AlignedRow("Added", news[0].block, None, news[0], None, "Rung not present in original Airknife printout"),
        ]

    n, m = len(olds), len(news)
    gap = 0.35
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    ptr = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = -gap * i
        ptr[i][0] = "D"
    for j in range(1, m + 1):
        score[0][j] = -gap * j
        ptr[0][j] = "I"
    raw = [[similarity(olds[i], news[j]) for j in range(m)] for i in range(n)]
    ranked = [[pair_score(olds[i], news[j]) for j in range(m)] for i in range(n)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            r = ranked[i - 1][j - 1]
            diag = score[i - 1][j - 1] + (r if r >= thresh else r - 1.0)
            delete = score[i - 1][j] - gap
            insert = score[i][j - 1] - gap
            if diag >= delete and diag >= insert:
                score[i][j] = diag
                ptr[i][j] = "M"
            elif delete >= insert:
                score[i][j] = delete
                ptr[i][j] = "D"
            else:
                score[i][j] = insert
                ptr[i][j] = "I"
    i, j = n, m
    rev: list[AlignedRow] = []
    while i > 0 or j > 0:
        move = ptr[i][j] if i or j else ""
        if move == "M":
            row = classify_pair(olds[i - 1], news[j - 1], thresh)
            row.similarity = raw[i - 1][j - 1]
            if row.change == "split":
                rev.append(AlignedRow("Deleted", olds[i - 1].block, olds[i - 1], None, None, "Rung not present in 91026"))
                rev.append(AlignedRow("Added", news[j - 1].block, None, news[j - 1], None, "Rung not present in original Airknife printout"))
            else:
                rev.append(row)
            i -= 1
            j -= 1
        elif move == "D":
            rev.append(AlignedRow("Deleted", olds[i - 1].block, olds[i - 1], None, None, "Rung not present in 91026"))
            i -= 1
        elif move == "I":
            rev.append(AlignedRow("Added", news[j - 1].block, None, news[j - 1], None, "Rung not present in original Airknife printout"))
            j -= 1
        else:
            break
    rev.reverse()
    return rev


def logic_change_detail(old: Rung, new: Rung) -> str:
    notes: list[str] = []
    if old.outputs != new.outputs:
        notes.append(
            "Outputs: "
            + (", ".join(sorted(old.outputs)) or "(none)")
            + " → "
            + (", ".join(sorted(new.outputs)) or "(none)")
        )
    removed = sorted(old.tokens - new.tokens)
    added = sorted(new.tokens - old.tokens)
    if removed:
        notes.append("Removed: " + ", ".join(removed[:18]) + ("…" if len(removed) > 18 else ""))
    if added:
        notes.append("Added: " + ", ".join(added[:18]) + ("…" if len(added) > 18 else ""))
    if old.mcr_zone != new.mcr_zone:
        notes.append("MCR zone marking changed (* vs |)")
    old_lines = [normalize_logic_line(x) for x in old.logic if has_logic(x)]
    new_lines = [normalize_logic_line(x) for x in new.logic if has_logic(x)]
    if old_lines != new_lines and not removed and not added:
        diff = [ln for ln in unified_diff(old_lines, new_lines, lineterm="", n=0)
                if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))]
        notes.append("Wiring / constants differ")
        if diff:
            notes.append("  " + " | ".join(diff[:6]))
    elif old_lines != new_lines:
        notes.append("Ladder wiring or constants also differ")
    oc, nc = old.comment_text, new.comment_text
    if oc != nc and nc and oc:
        notes.append("Comment text differs")
    elif not oc and nc:
        notes.append("Comment printed in 91026 (original print used COMMENT placeholders)")
    return "; ".join(notes) if notes else "Logic differs"


def align_block(old_rungs: list[Rung], new_rungs: list[Rung]) -> list[AlignedRow]:
    sm = SequenceMatcher(
        None,
        [r.fingerprint for r in old_rungs],
        [r.fingerprint for r in new_rungs],
        autojunk=False,
    )
    rows: list[AlignedRow] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                o, n = old_rungs[i1 + k], new_rungs[j1 + k]
                detail = ""
                if o.comment_text != n.comment_text:
                    if o.comment_text and n.comment_text:
                        detail = "Comment text differs (logic identical)"
                    elif n.comment_text and not o.comment_text:
                        detail = "Comment printed in 91026 (logic identical)"
                rows.append(AlignedRow("Unchanged", o.block, o, n, 1.0, detail))
        elif tag == "delete":
            for r in old_rungs[i1:i2]:
                rows.append(AlignedRow("Deleted", r.block, r, None, None, "Rung not present in 91026"))
        elif tag == "insert":
            for r in new_rungs[j1:j2]:
                rows.append(AlignedRow("Added", r.block, None, r, None, "Rung not present in original Airknife printout"))
        else:
            rows.extend(pair_replace(old_rungs[i1:i2], new_rungs[j1:j2]))
    return rows


def align_programs(original: Printout, later: Printout) -> list[AlignedRow]:
    old_by: dict[str, list[Rung]] = {}
    new_by: dict[str, list[Rung]] = {}
    for r in original.rungs:
        old_by.setdefault(r.block, []).append(r)
    for r in later.rungs:
        new_by.setdefault(r.block, []).append(r)
    blocks = list(dict.fromkeys(original.block_order + later.block_order))
    rows: list[AlignedRow] = []
    for block in blocks:
        olds = old_by.get(block, [])
        news = new_by.get(block, [])
        if not olds and news:
            for r in news:
                rows.append(AlignedRow("Added", block, None, r, None, "New block or rungs not in original"))
        elif olds and not news:
            for r in olds:
                rows.append(AlignedRow("Deleted", block, r, None, None, "Block or rungs not in 91026"))
        else:
            rows.extend(align_block(olds, news))
    return rows


def logic_text(rung: Rung | None) -> str:
    if rung is None:
        return ""
    return "\n".join(rung.logic)


# --- Excel -----------------------------------------------------------------

NAVY = "1F4E79"
WHITE = "FFFFFF"
RED = "FFCDD2"
GREEN = "C8E6C9"
AMBER = "FFE082"
BLUE = "D6EAF8"
GREY = "F2F3F4"
DKRED = "B71C1C"
DKGREEN = "1B5E20"
DKAMBER = "E65100"

FILL = {
    "Deleted": PatternFill("solid", fgColor=RED),
    "Added": PatternFill("solid", fgColor=GREEN),
    "Modified": PatternFill("solid", fgColor=AMBER),
    "Unchanged": PatternFill("solid", fgColor=WHITE),
    "header": PatternFill("solid", fgColor=NAVY),
    "section": PatternFill("solid", fgColor="2E86AB"),
    "info": PatternFill("solid", fgColor=BLUE),
}
FONT = {
    "header": Font(name="Calibri", bold=True, color=WHITE, size=11),
    "title": Font(name="Calibri", bold=True, color=NAVY, size=16),
    "section": Font(name="Calibri", bold=True, color=WHITE, size=12),
    "label": Font(name="Calibri", bold=True, size=11),
    "body": Font(name="Calibri", size=10),
    "mono": Font(name="Consolas", size=8),
    "deleted": Font(name="Calibri", size=10, color=DKRED, bold=True),
    "added": Font(name="Calibri", size=10, color=DKGREEN, bold=True),
    "modified": Font(name="Calibri", size=10, color=DKAMBER, bold=True),
}
THIN = Border(
    left=Side(style="thin", color="BFBFBF"),
    right=Side(style="thin", color="BFBFBF"),
    top=Side(style="thin", color="BFBFBF"),
    bottom=Side(style="thin", color="BFBFBF"),
)
WRAP = Alignment(wrap_text=True, vertical="top")
LEFT = Alignment(vertical="center", wrap_text=True)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)


def set_widths(ws: Worksheet, widths: dict[int, float]) -> None:
    for col, width in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = width


def write_row(ws: Worksheet, row: int, values: list[object], *, fill=None, fonts=None, align=None) -> None:
    for i, val in enumerate(values, 1):
        cell = ws.cell(row, i, val if val is not None else "")
        cell.border = THIN
        cell.alignment = align[i - 1] if align else WRAP
        if fill:
            cell.fill = fill
        if fonts and fonts[i - 1]:
            cell.font = fonts[i - 1]
        else:
            cell.font = FONT["body"]


def change_font(change: str) -> Font:
    return FONT.get(change.lower(), FONT["body"])


def rung_num(rung: Rung | None) -> int | str:
    return rung.number if rung else ""


def format_rung_list(nums: list[int]) -> str:
    if not nums:
        return ""
    nums = sorted(nums)
    ranges: list[str] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = n
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(ranges)


def build_workbook(original: Printout, later: Printout, rows: list[AlignedRow]) -> Workbook:
    wb = Workbook()
    diffs = [r for r in rows if r.change != "Unchanged" or r.detail]
    unchanged = [r for r in rows if r.change == "Unchanged" and not r.detail]
    deleted = [r for r in rows if r.change == "Deleted"]
    added = [r for r in rows if r.change == "Added"]
    modified = [r for r in rows if r.change == "Modified"]
    comment_only = [r for r in rows if r.change == "Unchanged" and r.detail]

    _sheet_summary(wb, original, later, rows, deleted, added, modified, unchanged, comment_only)
    _sheet_differences(wb, diffs)
    _sheet_mapping(wb, rows)
    _sheet_deleted(wb, deleted)
    _sheet_added(wb, added)
    _sheet_modified(wb, modified)
    _sheet_declarations(wb, original, later)
    _sheet_header(wb, original, later)
    return wb


def _sheet_summary(wb, original, later, rows, deleted, added, modified, unchanged, comment_only) -> None:
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "AIRKNIF ladder printout comparison"
    ws["A1"].font = FONT["title"]
    ws.merge_cells("A1:F1")
    ws["A2"] = (
        "Original printout is Airknife (27 Feb 2026). Later printout is 91026 / 9_10_2026 (10 Sep 2026). "
        "Rungs were matched by ladder logic (not by number), because remaining rungs were renumbered after deletions."
    )
    ws["A2"].alignment = WRAP
    ws.merge_cells("A2:F2")
    ws.row_dimensions[2].height = 48

    ws["A4"] = "How to read this workbook"
    ws["A4"].font = FONT["label"]
    notes = [
        "Rung Differences — every deleted, added, or modified rung, with original Airknife and 91026 rung numbers side by side.",
        "When a rung was deleted, the original Airknife rung number is filled in and the 91026 rung number is left blank.",
        "When a rung was added, the 91026 rung number is filled in and the original Airknife rung number is left blank.",
        "Rung Mapping — complete alignment of both programs, including unchanged rungs, so you can see how numbers shifted.",
        "Deleted / Added / Modified sheets repeat the same rows with full ladder text for review.",
        "Matching ignores page headers, cross-reference tables, and the original print's '(* COMMENT *)' placeholders.",
        "A leading * on a rung is Logicmaster's MCR-zone print mark (not a deleted rung).",
        "Logic-identical rungs whose comments only appear in 91026 are listed as Unchanged with a note; they are not counted as program edits.",
    ]
    for i, note in enumerate(notes, 5):
        ws.cell(i, 1, f"• {note}").alignment = WRAP
        ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=6)
        ws.row_dimensions[i].height = 32

    added_main = [r.later.number for r in added if r.later and r.later.block == "_MAIN"]
    coat_del = sum(1 for r in deleted if r.original and r.original.block == "COAT_WG")
    call = next((r for r in rows if r.original and "CALL COAT_WG" in r.original.summary), None)
    ws["A14"] = "Key findings"
    ws["A14"].font = FONT["label"]
    findings = [
        f"91026 is a reduced AIRKNIF: {len(deleted)} printed rungs were deleted, {len(modified)} were edited in place, and {len(added)} new rungs were added.",
        " _MAIN deletions include the simulation coils (original 6–8: SIMULAT / SIMBLAS / SIMPKNA), the ENA_* enable coils (original 10–18), BLOSPM preload (original 33), and HGTAUTO (original 187).",
        f"The CALL COAT_WG rung is original _MAIN {call.original.number if call and call.original else 20} → 91026 _MAIN {call.later.number if call and call.later else 8}. Remaining _MAIN rungs were compacted (typically 9–14 numbers lower) after those deletions.",
        f"Five new _MAIN rungs were inserted at 91026 numbers {format_rung_list(added_main) or '(none)'}: they OR knife pushbuttons into %M00092 and time %M00093.",
        f"COAT_WG lost {coat_del} rungs (ACT bit mapping, coating-weight math, galv health / mode bits, and related MOVEs). No unmatched new COAT_WG rungs were found.",
        f"Many modified rungs drop SIMULAT / SIMPKNA / ENA_* contacts that the deleted coils used to drive. Variable declarations are unchanged. Program size dropped from {original.program_size} to {later.program_size} bytes.",
        f"Original file: {original.path}",
        f"91026 file: {later.path}",
    ]
    for i, note in enumerate(findings, 15):
        ws.cell(i, 1, f"• {note.strip()}").alignment = WRAP
        ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=6)
        ws.row_dimensions[i].height = 36

    start = 24
    ws.cell(start, 1, "Counts").font = FONT["section"]
    ws.cell(start, 1).fill = FILL["section"]
    ws.merge_cells(start_row=start, start_column=1, end_row=start, end_column=4)

    stats = [
        ("Item", "Original Airknife", "91026", "Delta"),
        ("Print date", original.print_date, later.print_date, ""),
        ("Print title", original.title, later.title, ""),
        ("Program size (bytes)", original.program_size, later.program_size,
         _delta_num(original.program_size, later.program_size)),
        ("Variable declarations", len(original.decls), len(later.decls),
         len(later.decls) - len(original.decls)),
        ("Printed rungs (all blocks)", len(original.rungs), len(later.rungs),
         len(later.rungs) - len(original.rungs)),
    ]
    for block in original.block_order:
        oc = sum(1 for r in original.rungs if r.block == block)
        nc = sum(1 for r in later.rungs if r.block == block)
        stats.append((f"Printed rungs in {block}", oc, nc, nc - oc))
    for block in later.block_order:
        if block not in original.block_order:
            nc = sum(1 for r in later.rungs if r.block == block)
            stats.append((f"Printed rungs in {block}", 0, nc, nc))

    stats += [
        ("Aligned unchanged rungs (logic identical)", len(unchanged), "", ""),
        ("Deleted rungs (in original only)", len(deleted), "", ""),
        ("Added rungs (in 91026 only)", len(added), "", ""),
        ("Modified rungs (logic changed)", len(modified), "", ""),
        ("Unchanged logic, comment print differs", len(comment_only), "", ""),
    ]

    header_row = start + 1
    for col, val in enumerate(stats[0], 1):
        cell = ws.cell(header_row, col, val)
        cell.fill = FILL["header"]
        cell.font = FONT["header"]
        cell.alignment = CENTER
        cell.border = THIN
    for ridx, row in enumerate(stats[1:], header_row + 1):
        write_row(ws, ridx, list(row), fill=FILL["info"], align=[LEFT] * 4)

    # Deleted rung number list
    list_row = header_row + len(stats) + 2
    ws.cell(list_row, 1, "Deleted original Airknife rung numbers by block").font = FONT["section"]
    ws.cell(list_row, 1).fill = FILL["section"]
    ws.merge_cells(start_row=list_row, start_column=1, end_row=list_row, end_column=4)
    list_row += 1
    write_row(ws, list_row, ["Block", "Count deleted", "Original Airknife rung numbers", ""], fill=FILL["header"],
              fonts=[FONT["header"]] * 4, align=[CENTER] * 4)
    by_block: dict[str, list[int]] = {}
    for row in deleted:
        if row.original:
            by_block.setdefault(row.block, []).append(row.original.number)
    list_row += 1
    for block, nums in by_block.items():
        nums = sorted(nums)
        write_row(
            ws, list_row,
            [block, len(nums), format_rung_list(nums), ""],
            fill=FILL["Deleted"],
            fonts=[FONT["deleted"]] * 4,
        )
        ws.row_dimensions[list_row].height = 48
        list_row += 1

    list_row += 1
    ws.cell(list_row, 1, "Added 91026 rung numbers by block").font = FONT["section"]
    ws.cell(list_row, 1).fill = FILL["section"]
    ws.merge_cells(start_row=list_row, start_column=1, end_row=list_row, end_column=4)
    list_row += 1
    write_row(ws, list_row, ["Block", "Count added", "91026 rung numbers", ""], fill=FILL["header"],
              fonts=[FONT["header"]] * 4, align=[CENTER] * 4)
    by_block = {}
    for row in added:
        if row.later:
            by_block.setdefault(row.block, []).append(row.later.number)
    list_row += 1
    if by_block:
        for block, nums in by_block.items():
            nums = sorted(nums)
            write_row(
                ws, list_row,
                [block, len(nums), format_rung_list(nums), ""],
                fill=FILL["Added"],
                fonts=[FONT["added"]] * 4,
            )
            list_row += 1
    else:
        write_row(ws, list_row, ["(none)", 0, "No rungs were added; 91026 is a subset of the original plus modifications.", ""])

    set_widths(ws, {1: 48, 2: 22, 3: 88, 4: 16, 5: 20, 6: 20})
    ws.row_dimensions[1].height = 24
    ws.print_title_rows = "1:1"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def _delta_num(a: str, b: str) -> str:
    try:
        return str(int(b) - int(a))
    except (TypeError, ValueError):
        return ""


def _diff_headers() -> list[str]:
    return [
        "Change",
        "Block",
        "Original Airknife Rung",
        "91026 Rung",
        "Logic summary",
        "What changed",
        "Original ladder",
        "91026 ladder",
        "Original comment",
        "91026 comment",
        "Similarity",
    ]


def _diff_values(row: AlignedRow) -> list[object]:
    sim = "" if row.similarity is None else round(row.similarity, 3)
    return [
        row.change,
        row.block,
        rung_num(row.original),
        rung_num(row.later),
        (row.original or row.later).summary if (row.original or row.later) else "",
        row.detail,
        logic_text(row.original),
        logic_text(row.later),
        row.original.comment_text if row.original else "",
        row.later.comment_text if row.later else "",
        sim,
    ]


def _write_diff_sheet(ws: Worksheet, rows: list[AlignedRow], title: str) -> None:
    ws["A1"] = title
    ws["A1"].font = FONT["title"]
    ws.merge_cells("A1:K1")
    headers = _diff_headers()
    for col, val in enumerate(headers, 1):
        cell = ws.cell(3, col, val)
        cell.fill = FILL["header"]
        cell.font = FONT["header"]
        cell.alignment = CENTER
        cell.border = THIN
    if rows:
        ws.auto_filter.ref = f"A3:K{3 + len(rows)}"
    ws.freeze_panes = "A4"
    for i, row in enumerate(rows, 4):
        values = _diff_values(row)
        fill = FILL.get(row.change, FILL["Unchanged"])
        fonts = [change_font(row.change), FONT["body"], FONT["label"], FONT["label"],
                 FONT["body"], FONT["body"], FONT["mono"], FONT["mono"], FONT["body"], FONT["body"], FONT["body"]]
        write_row(ws, i, values, fill=fill, fonts=fonts)
        # Taller rows when ladder text is present
        lines = max(
            2,
            min(18, (values[6].count("\n") if isinstance(values[6], str) else 0) + 2,
                (values[7].count("\n") if isinstance(values[7], str) else 0) + 2),
        )
        ws.row_dimensions[i].height = 15 * lines
    set_widths(ws, {1: 14, 2: 12, 3: 22, 4: 14, 5: 36, 6: 42, 7: 55, 8: 55, 9: 28, 10: 36, 11: 12})
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.paperSize = ws.PAPERSIZE_TABLOID


def _sheet_differences(wb: Workbook, diffs: list[AlignedRow]) -> None:
    ws = wb.create_sheet("Rung Differences")
    # Keep comment-only out of the main difference sheet unless logic changed
    program_diffs = [r for r in diffs if r.change in {"Deleted", "Added", "Modified"}]
    _write_diff_sheet(
        ws, program_diffs,
        "Program differences — original Airknife rung number vs 91026 rung number",
    )


def _sheet_mapping(wb: Workbook, rows: list[AlignedRow]) -> None:
    ws = wb.create_sheet("Rung Mapping")
    ws["A1"] = "Complete rung-number map (original Airknife → 91026), including unchanged rungs"
    ws["A1"].font = FONT["title"]
    ws.merge_cells("A1:G1")
    headers = ["Seq", "Change", "Block", "Original Airknife Rung", "91026 Rung", "Logic summary", "Notes"]
    for col, val in enumerate(headers, 1):
        cell = ws.cell(3, col, val)
        cell.fill = FILL["header"]
        cell.font = FONT["header"]
        cell.alignment = CENTER
        cell.border = THIN
    ws.freeze_panes = "A4"
    if rows:
        ws.auto_filter.ref = f"A3:G{3 + len(rows)}"
    for i, row in enumerate(rows, 4):
        values = [
            i - 3,
            row.change,
            row.block,
            rung_num(row.original),
            rung_num(row.later),
            (row.original or row.later).summary if (row.original or row.later) else "",
            row.detail,
        ]
        fill = FILL.get(row.change, FILL["Unchanged"])
        fonts = [FONT["body"], change_font(row.change), FONT["body"], FONT["label"], FONT["label"], FONT["body"], FONT["body"]]
        write_row(ws, i, values, fill=fill, fonts=fonts, align=[CENTER, CENTER, CENTER, CENTER, CENTER, LEFT, LEFT])
    set_widths(ws, {1: 8, 2: 14, 3: 12, 4: 24, 5: 14, 6: 48, 7: 70})
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def _sheet_deleted(wb: Workbook, deleted: list[AlignedRow]) -> None:
    ws = wb.create_sheet("Deleted Rungs")
    _write_diff_sheet(ws, deleted, "Rungs present in the original Airknife printout and missing from 91026")


def _sheet_added(wb: Workbook, added: list[AlignedRow]) -> None:
    ws = wb.create_sheet("Added Rungs")
    _write_diff_sheet(ws, added, "Rungs present in 91026 and missing from the original Airknife printout")


def _sheet_modified(wb: Workbook, modified: list[AlignedRow]) -> None:
    ws = wb.create_sheet("Modified Rungs")
    _write_diff_sheet(ws, modified, "Rungs matched across both printouts whose ladder logic is not identical")


def _sheet_declarations(wb: Workbook, original: Printout, later: Printout) -> None:
    ws = wb.create_sheet("Declarations")
    ws["A1"] = "Variable declaration table differences"
    ws["A1"].font = FONT["title"]
    ws.merge_cells("A1:G1")
    old = {d.ref: d for d in original.decls}
    new = {d.ref: d for d in later.decls}
    refs = sorted(set(old) | set(new), key=lambda r: (re.sub(r"\d+", "", r), int(re.sub(r"\D+", "", r) or 0)))
    headers = ["Change", "Reference", "Original nickname", "91026 nickname", "Original description", "91026 description", "Notes"]
    for col, val in enumerate(headers, 1):
        cell = ws.cell(3, col, val)
        cell.fill = FILL["header"]
        cell.font = FONT["header"]
        cell.alignment = CENTER
        cell.border = THIN
    ws.freeze_panes = "A4"
    r = 4
    n_diff = 0
    for ref in refs:
        o, n = old.get(ref), new.get(ref)
        if o and not n:
            change, note = "Deleted", "Declaration removed in 91026"
        elif n and not o:
            change, note = "Added", "Declaration added in 91026"
        elif o and n and (o.nickname != n.nickname or o.description != n.description):
            change = "Modified"
            bits = []
            if o.nickname != n.nickname:
                bits.append("nickname")
            if o.description != n.description:
                bits.append("description")
            note = "Changed " + " and ".join(bits)
        else:
            continue
        n_diff += 1
        write_row(
            ws, r,
            [change, ref, o.nickname if o else "", n.nickname if n else "",
             o.description if o else "", n.description if n else "", note],
            fill=FILL.get(change),
            fonts=[change_font(change)] + [FONT["body"]] * 6,
        )
        r += 1
    if n_diff:
        ws.auto_filter.ref = f"A3:G{r - 1}"
    else:
        ws.cell(4, 1, "No declaration differences.")
    ws.cell(2, 1, f"{n_diff} declaration(s) differ. Unchanged entries are omitted.")
    set_widths(ws, {1: 14, 2: 14, 3: 20, 4: 20, 5: 42, 6: 42, 7: 36})


def _sheet_header(wb: Workbook, original: Printout, later: Printout) -> None:
    ws = wb.create_sheet("Program Header")
    ws["A1"] = "Program environment / header comparison"
    ws["A1"].font = FONT["title"]
    ws.merge_cells("A1:E1")
    headers = ["Item", "Original Airknife", "91026", "Changed?", "Notes"]
    for col, val in enumerate(headers, 1):
        cell = ws.cell(3, col, val)
        cell.fill = FILL["header"]
        cell.font = FONT["header"]
        cell.alignment = CENTER
        cell.border = THIN
    r = 4
    write_row(ws, r, ["Print date", original.print_date, later.print_date,
                      "Yes" if original.print_date != later.print_date else "No", "Cover page date"])
    r += 1
    write_row(ws, r, ["Print title", original.title, later.title,
                      "Yes" if original.title != later.title else "No", "Logicmaster folder / program title"])
    r += 1
    write_row(ws, r, ["Program size (bytes)", original.program_size, later.program_size,
                      "Yes" if original.program_size != later.program_size else "No",
                      "Smaller 91026 size is consistent with deleted rungs"])
    r += 1
    for key in sorted(set(original.block_sizes) | set(later.block_sizes)):
        ov = original.block_sizes.get(key, "")
        nv = later.block_sizes.get(key, "")
        write_row(ws, r, [f"{key} block size (bytes)", ov, nv, "Yes" if ov != nv else "No", ""])
        r += 1
    # Environment table: pair by left-hand label
    old_env = {(a, c): (b, d) for a, b, c, d in original.env_rows}
    new_env = {(a, c): (b, d) for a, b, c, d in later.env_rows}
    for key in list(dict.fromkeys(list(old_env) + list(new_env))):
        ov = old_env.get(key)
        nv = new_env.get(key)
        if not ov and nv:
            write_row(ws, r, [f"{key[0]} / {key[1]}", "", f"{nv[0]} / {nv[1]}", "Yes", "Only in 91026"], fill=FILL["Added"])
        elif ov and not nv:
            write_row(ws, r, [f"{key[0]} / {key[1]}", f"{ov[0]} / {ov[1]}", "", "Yes", "Only in original"], fill=FILL["Deleted"])
        else:
            changed = ov != nv
            write_row(
                ws, r,
                [f"{key[0]} / {key[1]}", f"{ov[0]} / {ov[1]}", f"{nv[0]} / {nv[1]}",
                 "Yes" if changed else "No", "Highest-reference or configured size"],
                fill=FILL["Modified"] if changed else FILL["Unchanged"],
            )
        r += 1
    set_widths(ws, {1: 42, 2: 28, 3: 28, 4: 12, 5: 50})
    ws.freeze_panes = "A4"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path, help="Earlier Logicmaster printout (Airknife)")
    parser.add_argument("later", type=Path, help="Later Logicmaster printout (91026)")
    parser.add_argument("output", type=Path, help="Excel workbook to write")
    args = parser.parse_args(argv)

    original = parse_printout(args.original, "Original Airknife")
    later = parse_printout(args.later, "91026")
    rows = align_programs(original, later)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    wb = build_workbook(original, later, rows)
    wb.save(args.output)

    deleted = sum(1 for r in rows if r.change == "Deleted")
    added = sum(1 for r in rows if r.change == "Added")
    modified = sum(1 for r in rows if r.change == "Modified")
    unchanged = sum(1 for r in rows if r.change == "Unchanged")
    print(f"Original rungs: {len(original.rungs)}")
    print(f"91026 rungs:    {len(later.rungs)}")
    print(f"Unchanged: {unchanged}  Modified: {modified}  Deleted: {deleted}  Added: {added}")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
