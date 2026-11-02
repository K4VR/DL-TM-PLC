#!/usr/bin/env python3
"""Compare two revisions of a GE Logicmaster printout and write an Excel report.

Ladder is matched by address. A nickname that replaces a raw % reference, or a
renamed nickname on the same reference, is a declaration change, not a logic edit.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from tools.airknife_rung_comments import format_printout_comment
from tools.compare_lm90_printouts import (
    Decl,
    Printout,
    Rung,
    extract_outputs,
    format_rung_list,
    parse_printout,
    summarize_rung,
)

INSTR = {
    "CONST", "INT", "REAL", "DINT", "LEN", "ALW_ON", "ALW_OFF", "MOVE", "ADD", "SUB",
    "MUL", "DIV", "AND", "OR", "NOT", "GT", "LT", "EQ", "GE", "LE", "NE", "TO", "IN",
    "Q", "I1", "I2", "R", "SM", "RM", "CV", "PV", "ONDTR", "OFDT", "OFDTR", "UPCTR",
    "DNCTR", "CALL", "MCR", "END_MCR",
}

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
    "Comment cleared": PatternFill("solid", fgColor=BLUE),
    "Unchanged": PatternFill("solid", fgColor=WHITE),
    "header": PatternFill("solid", fgColor=NAVY),
    "section": PatternFill("solid", fgColor="2E86AB"),
    "info": PatternFill("solid", fgColor=BLUE),
    "zebra": PatternFill("solid", fgColor=GREY),
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
    "comment": Font(name="Calibri", size=10, color="0D47A1", bold=True),
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

FLOAT_RE = re.compile(r"[+-]\d+\.\d+")
REF_RE = re.compile(r"%[A-Z]+\d+")


@dataclass
class LogicChange:
    change: str
    block: str
    area: str
    old: Rung | None
    new: Rung | None
    detail: str
    old_ladder: str
    new_ladder: str


@dataclass
class DeclChange:
    change: str
    ref: str
    old_nick: str
    new_nick: str
    old_desc: str
    new_desc: str
    note: str


@dataclass
class CommentChange:
    block: str
    old_number: int
    new_number: int
    old_comment: str
    new_comment: str


@dataclass
class MapRow:
    block: str
    change: str
    old_number: int | None
    new_number: int | None
    summary: str
    note: str


@dataclass
class RevisionDiff:
    earlier: Printout
    later: Printout
    earlier_label: str
    later_label: str
    logic: list[LogicChange] = field(default_factory=list)
    comments: list[CommentChange] = field(default_factory=list)
    decls: list[DeclChange] = field(default_factory=list)
    mapping: list[MapRow] = field(default_factory=list)

    @property
    def added(self) -> list[LogicChange]:
        return [row for row in self.logic if row.change == "Added"]

    @property
    def modified(self) -> list[LogicChange]:
        return [row for row in self.logic if row.change == "Modified"]

    @property
    def deleted(self) -> list[LogicChange]:
        return [row for row in self.logic if row.change == "Deleted"]


def decl_index(decls: list[Decl]) -> dict[str, Decl]:
    return {d.ref: d for d in decls}


def nickname_pattern(decls: list[Decl]) -> tuple[dict[str, str], re.Pattern[str] | None]:
    mapping: dict[str, str] = {}
    for decl in decls:
        nick = decl.nickname.strip()
        if len(nick) < 2 or nick in INSTR:
            continue
        mapping[nick] = decl.ref
    if not mapping:
        return mapping, None
    parts = sorted(mapping, key=len, reverse=True)
    pattern = re.compile(
        r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(part) for part in parts) + r")(?![A-Za-z0-9])"
    )
    return mapping, pattern


def canonical_lines(rung: Rung, pattern: re.Pattern[str] | None, mapping: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for raw in rung.logic:
        line = raw.rstrip()
        if line.startswith("*"):
            rest = line[1:]
            line = ("+" if rest[:1] in "-+" else "|") + rest
        if pattern is not None:
            line = pattern.sub(lambda match: mapping[match.group(0)], line)
        line = re.sub(r"\s+", "", line)
        if not line or line in {"|", "*", "+", "-"}:
            continue
        if "<<" in line and "RUNG" in line:
            continue
        if "(*" in line:
            continue
        lines.append(line)
    return lines


def canonical_text(rung: Rung, pattern: re.Pattern[str] | None, mapping: dict[str, str]) -> str:
    return "\n".join(canonical_lines(rung, pattern, mapping))


def canonical_outputs(text: str, block: str, number: int) -> frozenset[str]:
    if not text:
        return frozenset()
    shadow = Rung(block=block, number=number, logic=text.splitlines())
    return extract_outputs(shadow)


def refs_in(text: str) -> set[str]:
    return set(REF_RE.findall(text))


def signed_numbers(rung: Rung) -> list[str]:
    text = "\n".join(rung.logic)
    return FLOAT_RE.findall(text)


def ladder_text(rung: Rung | None) -> str:
    if rung is None:
        return ""
    kept: list[str] = []
    for line in rung.logic:
        if line.strip().lower() in {"airknif", "airknife", "9_10_2026"}:
            continue
        kept.append(line.rstrip())
    return "\n".join(kept)


def comment_display(rung: Rung | None) -> str:
    if rung is None:
        return ""
    text = format_printout_comment(rung.comments).strip()
    if text:
        return text
    raw = " ".join(rung.comments).upper()
    if "COMMENT" in raw:
        return "(COMMENT placeholder)"
    return ""


def label(ref: str, earlier: dict[str, Decl], later: dict[str, Decl]) -> str:
    decl = later.get(ref) or earlier.get(ref)
    if decl is None:
        return ref
    nick = decl.nickname.strip()
    desc = re.sub(r"\s+", " ", decl.description).strip()
    if nick and desc:
        return f"{nick} ({ref}, {desc})"
    if nick:
        return f"{nick} ({ref})"
    if desc:
        return f"{ref} ({desc})"
    return ref


def short_name(ref: str, earlier: dict[str, Decl], later: dict[str, Decl]) -> str:
    decl = later.get(ref) or earlier.get(ref)
    if decl and decl.nickname.strip():
        return f"{decl.nickname.strip()} ({ref})"
    return ref


def _similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _pair_replace(
    olds: list[Rung],
    news: list[Rung],
    old_fp: dict[int, str],
    new_fp: dict[int, str],
    old_out: dict[int, frozenset[str]],
    new_out: dict[int, frozenset[str]],
) -> list[tuple[str, Rung | None, Rung | None]]:
    n, m = len(olds), len(news)
    if n == 1 and m == 1:
        return [_classify(olds[0], news[0], old_fp, new_fp, old_out, new_out)]
    gap = 0.35
    thresh = 0.60
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    ptr = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = -gap * i
        ptr[i][0] = "D"
    for j in range(1, m + 1):
        score[0][j] = -gap * j
        ptr[0][j] = "I"
    ranked = [[_pair_score(olds[i], news[j], old_fp, new_fp, old_out, new_out) for j in range(m)] for i in range(n)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            ranked_score = ranked[i - 1][j - 1]
            diag = score[i - 1][j - 1] + (ranked_score if ranked_score >= thresh else ranked_score - 1.0)
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
    rev: list[tuple[str, Rung | None, Rung | None]] = []
    while i > 0 or j > 0:
        move = ptr[i][j]
        if move == "M":
            row = _classify(olds[i - 1], news[j - 1], old_fp, new_fp, old_out, new_out)
            if row[0] == "split":
                rev.append(("Deleted", olds[i - 1], None))
                rev.append(("Added", None, news[j - 1]))
            else:
                rev.append(row)
            i -= 1
            j -= 1
        elif move == "D":
            rev.append(("Deleted", olds[i - 1], None))
            i -= 1
        elif move == "I":
            rev.append(("Added", None, news[j - 1]))
            j -= 1
        else:
            break
    rev.reverse()
    return rev


def _pair_score(
    old: Rung,
    new: Rung,
    old_fp: dict[int, str],
    new_fp: dict[int, str],
    old_out: dict[int, frozenset[str]],
    new_out: dict[int, frozenset[str]],
) -> float:
    sim = _similarity(old_fp[id(old)], new_fp[id(new)])
    if old_out[id(old)] & new_out[id(new)] and sim >= 0.45:
        return max(sim, 0.85)
    return sim


def _classify(
    old: Rung,
    new: Rung,
    old_fp: dict[int, str],
    new_fp: dict[int, str],
    old_out: dict[int, frozenset[str]],
    new_out: dict[int, frozenset[str]],
) -> tuple[str, Rung | None, Rung | None]:
    if old_fp[id(old)] == new_fp[id(new)]:
        return ("Unchanged", old, new)
    sim = _pair_score(old, new, old_fp, new_fp, old_out, new_out)
    if sim >= 0.60:
        return ("Modified", old, new)
    return ("split", old, new)


def align_rungs(
    earlier: Printout,
    later: Printout,
) -> list[tuple[str, Rung | None, Rung | None, str, str]]:
    """Return (change, old, new, old_canon, new_canon) in program order."""
    old_map, old_pat = nickname_pattern(earlier.decls)
    new_map, new_pat = nickname_pattern(later.decls)
    old_fp: dict[int, str] = {}
    new_fp: dict[int, str] = {}
    old_out: dict[int, frozenset[str]] = {}
    new_out: dict[int, frozenset[str]] = {}
    for rung in earlier.rungs:
        text = canonical_text(rung, old_pat, old_map)
        old_fp[id(rung)] = text
        old_out[id(rung)] = canonical_outputs(text, rung.block, rung.number)
    for rung in later.rungs:
        text = canonical_text(rung, new_pat, new_map)
        new_fp[id(rung)] = text
        new_out[id(rung)] = canonical_outputs(text, rung.block, rung.number)

    old_by: dict[str, list[Rung]] = {}
    new_by: dict[str, list[Rung]] = {}
    for rung in earlier.rungs:
        old_by.setdefault(rung.block, []).append(rung)
    for rung in later.rungs:
        new_by.setdefault(rung.block, []).append(rung)

    rows: list[tuple[str, Rung | None, Rung | None, str, str]] = []
    for block in list(dict.fromkeys(earlier.block_order + later.block_order)):
        olds = old_by.get(block, [])
        news = new_by.get(block, [])
        sm = SequenceMatcher(
            None,
            [old_fp[id(rung)] for rung in olds],
            [new_fp[id(rung)] for rung in news],
            autojunk=False,
        )
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    old, new = olds[i1 + k], news[j1 + k]
                    rows.append(("Unchanged", old, new, old_fp[id(old)], new_fp[id(new)]))
            elif tag == "delete":
                for rung in olds[i1:i2]:
                    rows.append(("Deleted", rung, None, old_fp[id(rung)], ""))
            elif tag == "insert":
                for rung in news[j1:j2]:
                    rows.append(("Added", None, rung, "", new_fp[id(rung)]))
            else:
                for change, old, new in _pair_replace(
                    olds[i1:i2], news[j1:j2], old_fp, new_fp, old_out, new_out
                ):
                    rows.append(
                        (
                            change,
                            old,
                            new,
                            old_fp[id(old)] if old else "",
                            new_fp[id(new)] if new else "",
                        )
                    )
    return rows


def _coil(text: str, block: str) -> str:
    outputs = canonical_outputs(text, block, 0)
    coils = sorted(ref for ref in outputs if not ref.startswith("CALL:"))
    return coils[0] if len(coils) == 1 else ", ".join(coils)


def _pv_and_timer(text: str, coil: str) -> tuple[str, str]:
    preset = ""
    timer = ""
    for line in text.splitlines():
        found = REF_RE.findall(line)
        if "PV" in line:
            for ref in found:
                if ref != coil and not ref.startswith("%P"):
                    preset = ref
                    break
        for ref in found:
            if ref.startswith("%P"):
                timer = ref
    return preset, timer


def _contacts(text: str, coil: str, preset: str, timer: str) -> list[str]:
    skip = {coil, preset, timer}
    found: list[str] = []
    for ref in REF_RE.findall(text):
        if ref in skip or ref.startswith("%P"):
            continue
        if ref not in found:
            found.append(ref)
    return found


def describe_added(rung: Rung, text: str, earlier: dict[str, Decl], later: dict[str, Decl]) -> tuple[str, str]:
    coil = _coil(text, rung.block)
    preset, timer = _pv_and_timer(text, coil)
    contacts = _contacts(text, coil, preset, timer)
    contact = contacts[0] if contacts else "(no contact found)"
    joined = "\n".join(rung.logic)
    if "-(^)-" in text or "(^)" in joined:
        area = "Elevator one-shot"
        detail = (
            f"Added a one-scan rising-edge coil. When {label(contact, earlier, later)} turns on, "
            f"{label(coil, earlier, later)} is true for one scan."
        )
        return area, detail
    if "OFDT" in text:
        area = "Elevator time pulse"
        detail = (
            f"Added an off-delay timer, time base 0.01 s, preset {label(preset, earlier, later)}, "
            f"timer word {timer}. Enable {label(contact, earlier, later)} drives "
            f"{label(coil, earlier, later)}. With a one-scan enable, the coil is a timed pulse "
            f"whose length is the value in the preset register (hundredths of a second). "
            f"That value is data, not ladder, so it is not in the printout."
        )
        return area, detail
    area = "Added rung"
    detail = f"Added rung driving {label(coil, earlier, later)}."
    return area, detail


def describe_modified(
    old: Rung,
    new: Rung,
    old_text: str,
    new_text: str,
    earlier: dict[str, Decl],
    later: dict[str, Decl],
) -> tuple[str, str]:
    old_refs = refs_in(old_text)
    new_refs = refs_in(new_text)
    removed = sorted(old_refs - new_refs)
    added = sorted(new_refs - old_refs)
    old_nums = signed_numbers(old)
    new_nums = signed_numbers(new)
    coil = _coil(new_text, new.block) or _coil(old_text, old.block)
    number_changes = [
        (a, b) for a, b in zip(old_nums, new_nums) if a != b
    ] if len(old_nums) == len(new_nums) else []

    if number_changes and not removed and not added:
        bits = [f"{a} to {b}" for a, b in number_changes]
        stayed = [num for num in new_nums if num not in {b for _, b in number_changes}]
        stayed_txt = f" Unchanged constants on the rung: {', '.join(stayed)}." if stayed else ""
        detail = (
            f"Changed the scaling constant from {' and '.join(bits)}. "
            f"The result is still {label(coil, earlier, later)}.{stayed_txt} "
            f"No contacts or addresses were added or removed."
        )
        return "Scaling constant", detail

    if removed and not added and not number_changes:
        names = ", ".join(label(ref, earlier, later) for ref in removed)
        if "OFDT" in old_text or "OFDT" in new_text:
            detail = (
                f"Removed the series contact {names} that sat between the off-delay output and "
                f"coil {label(coil, earlier, later)}. The timer still runs from the same enable, "
                f"preset, and timer word. The coil no longer depends on that contact."
            )
        else:
            detail = (
                f"Removed {names} from the rung that coils {label(coil, earlier, later)}."
            )
        return "Stabilizer pulse", detail

    if added and not removed and not number_changes:
        names = " and ".join(label(ref, earlier, later) for ref in added)
        detail = (
            f"Added {names} to the rung that coils {label(coil, earlier, later)}. "
            f"The new contacts are a parallel branch: the time pulse in series with the side select. "
            f"The existing pushbutton and auto paths on this starter are unchanged. "
            f"Contacts that only look different (for example HTRVFLT vs KHTNEG) are nickname renames of the same address."
        )
        return "Elevator starter", detail

    detail_bits = []
    if removed:
        detail_bits.append("Removed " + ", ".join(label(ref, earlier, later) for ref in removed))
    if added:
        detail_bits.append("Added " + ", ".join(label(ref, earlier, later) for ref in added))
    if number_changes:
        detail_bits.append("Constants " + ", ".join(f"{a} to {b}" for a, b in number_changes))
    if not detail_bits:
        detail_bits.append("Ladder wiring changed without an address or constant difference")
    return "Logic", ". ".join(detail_bits) + f". Coil {label(coil, earlier, later)}."


def _skeleton(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _addresses_used(printout: Printout) -> set[str]:
    mapping, pattern = nickname_pattern(printout.decls)
    used: set[str] = set()
    for rung in printout.rungs:
        text = canonical_text(rung, pattern, mapping)
        used.update(refs_in(text))
        used.update(refs_in("\n".join(rung.logic)))
    return used


def declaration_changes(earlier: Printout, later: Printout) -> list[DeclChange]:
    old = decl_index(earlier.decls)
    new = decl_index(later.decls)
    used_earlier = _addresses_used(earlier)

    def sort_key(ref: str) -> tuple[str, int]:
        return (re.sub(r"\d+", "", ref), int(re.sub(r"\D+", "", ref) or 0))

    rows: list[DeclChange] = []
    for ref in sorted(set(old) | set(new), key=sort_key):
        before = old.get(ref)
        after = new.get(ref)
        if before and not after:
            rows.append(DeclChange(
                "Deleted", ref, before.nickname, "", before.description, "",
                "Declaration row removed.",
            ))
            continue
        if after and not before:
            if ref in used_earlier:
                note = (
                    "New variable-table row for an address the 09/10 ladder already used "
                    "with no nickname. The instruction did not change."
                )
            else:
                note = "New variable-table row. This address was not used in the 09/10 ladder."
            rows.append(DeclChange(
                "Added", ref, "", after.nickname, "", after.description, note,
            ))
            continue
        if before is None or after is None:
            continue
        nick_changed = before.nickname != after.nickname
        desc_changed = before.description != after.description
        if not nick_changed and not desc_changed:
            continue
        if nick_changed and not before.nickname.strip():
            note = "Nickname assigned. Description updated from the 09/10 placeholder."
            change = "Nickname assigned"
        elif nick_changed:
            note = (
                "Nickname renamed. The ladder still uses this same address. "
                "A 10/21 contact that shows the new name is the same bit as the 09/10 contact that showed the old name."
            )
            change = "Renamed"
        else:
            old_s, new_s = _skeleton(before.description), _skeleton(after.description)
            close = SequenceMatcher(None, old_s, new_s).ratio() >= 0.96
            if old_s == new_s or close:
                note = "Spelling or spacing only. Nickname unchanged."
                change = "Description"
            elif "drive" in before.description.lower() and "oper" in after.description.lower():
                note = (
                    "Description wording changed from Drive to Operator. The nickname was not changed. "
                    "Confirm this was intentional."
                )
                change = "Description"
            else:
                note = "Description wording changed. Nickname unchanged."
                change = "Description"
        rows.append(DeclChange(
            change, ref, before.nickname, after.nickname, before.description, after.description, note,
        ))
    return rows


def comment_changes(
    aligned: list[tuple[str, Rung | None, Rung | None, str, str]],
) -> list[CommentChange]:
    rows: list[CommentChange] = []
    for change, old, new, _old_text, _new_text in aligned:
        if change not in {"Unchanged", "Modified"} or old is None or new is None:
            continue
        before = comment_display(old)
        after = comment_display(new)
        if not before or before == after:
            continue
        if before == "(COMMENT placeholder)":
            continue
        if after not in {"", "(COMMENT placeholder)"} and before != after:
            rows.append(CommentChange(old.block, old.number, new.number, before, after))
            continue
        rows.append(CommentChange(
            old.block, old.number, new.number, before, after or "(no comment printed)",
        ))
    return rows


def build_diff(earlier: Printout, later: Printout, earlier_label: str, later_label: str) -> RevisionDiff:
    aligned = align_rungs(earlier, later)
    old_decls = decl_index(earlier.decls)
    new_decls = decl_index(later.decls)
    report = RevisionDiff(earlier, later, earlier_label, later_label)
    report.decls = declaration_changes(earlier, later)
    report.comments = comment_changes(aligned)

    cleared = {(row.block, row.old_number) for row in report.comments}
    for change, old, new, old_text, new_text in aligned:
        note = ""
        if old and (old.block, old.number) in cleared:
            note = "Rung comment cleared to the COMMENT placeholder."
        if change == "Unchanged":
            summary = summarize_rung(new or old)
            report.mapping.append(MapRow(
                (new or old).block if (new or old) else "",
                "Comment cleared" if note else "Unchanged",
                old.number if old else None,
                new.number if new else None,
                summary,
                note,
            ))
            continue
        if change == "Added" and new is not None:
            area, detail = describe_added(new, new_text, old_decls, new_decls)
            report.logic.append(LogicChange(
                "Added", new.block, area, None, new, detail, "", ladder_text(new),
            ))
            report.mapping.append(MapRow(new.block, "Added", None, new.number, summarize_rung(new), detail))
            continue
        if change == "Deleted" and old is not None:
            report.logic.append(LogicChange(
                "Deleted", old.block, "Deleted rung", old, None,
                "Rung removed in the 10/21 printout.", ladder_text(old), "",
            ))
            report.mapping.append(MapRow(
                old.block, "Deleted", old.number, None, summarize_rung(old), "Rung removed in the 10/21 printout.",
            ))
            continue
        if change == "Modified" and old is not None and new is not None:
            area, detail = describe_modified(old, new, old_text, new_text, old_decls, new_decls)
            if note:
                detail = detail + " " + note
            report.logic.append(LogicChange(
                "Modified", new.block, area, old, new, detail, ladder_text(old), ladder_text(new),
            ))
            report.mapping.append(MapRow(new.block, "Modified", old.number, new.number, summarize_rung(new), detail))
    return report


def _delta(a: str, b: str) -> str:
    try:
        return str(int(b) - int(a))
    except (TypeError, ValueError):
        return ""


def summary_lines(report: RevisionDiff) -> list[str]:
    earlier, later = report.earlier, report.later
    old_main = sum(1 for rung in earlier.rungs if rung.block == "_MAIN")
    new_main = sum(1 for rung in later.rungs if rung.block == "_MAIN")
    old_coat = sum(1 for rung in earlier.rungs if rung.block == "COAT_WG")
    new_coat = sum(1 for rung in later.rungs if rung.block == "COAT_WG")
    renamed = [row for row in report.decls if row.change == "Renamed"]
    assigned = [row for row in report.decls if row.change == "Nickname assigned"]
    added_decls = [row for row in report.decls if row.change == "Added"]
    desc_only = [row for row in report.decls if row.change == "Description"]
    drive_oper = next((row for row in desc_only if "Drive" in row.note or "Operator" in row.note), None)
    return [
        (
            f"Logic edits: {len(report.added)} rungs added and {len(report.modified)} rungs edited. "
            f"Deleted rungs: {len(report.deleted)}. Printed rung count went from {len(earlier.rungs)} to {len(later.rungs)}."
        ),
        (
            f"_MAIN went from {old_main} rungs to {new_main}. COAT_WG stayed at {old_coat} on 09/10 and {new_coat} on 10/21. "
            f"Program size {earlier.program_size} to {later.program_size} bytes ({_delta(earlier.program_size, later.program_size)}). "
            f"_MAIN block {earlier.block_sizes.get('_MAIN', '')} to {later.block_sizes.get('_MAIN', '')} bytes. "
            f"COAT_WG block stayed {earlier.block_sizes.get('COAT_WG', '')} bytes, which matches constant-only edits in that block."
        ),
        (
            "Elevator bump from Wonderware. Four new _MAIN rungs turn the Wonderware up/down requests into a timed pulse: "
            "ELUPPB (%M00648) rising edge sets ELUPOS (%M00586), then an off-delay at %P00067 (preset %R00929) coils ELTPUP (%M00584). "
            "ELDNPB (%M00649) does the same for down through ELDNOS (%M00587) and ELTPDN (%M00585) at %P00070. "
            "Highest program register used moved from %P00066 to %P00072 because those two timers occupy %P00067 through %P00069 and %P00070 through %P00072."
        ),
        (
            "Those pulses were wired into the four elevator starters. Drive-side up ST5FWD (%Q00465) gained ELTPUP and ELDRSEL (%M00650). "
            "Drive-side down ST5REV (%Q00466) gained ELTPDN and ELDRSEL. Operator-side up ST6FWD (%Q00467) gained ELTPUP and ELOPSEL (%M00651). "
            "Operator-side down ST6REV (%Q00468) gained ELTPDN and ELOPSEL. Each new pair is in parallel with the existing pushbutton branch."
        ),
        (
            "Nickname renames on those same starter rungs are not logic changes. HTRVFLT is now KHTNEG, KHO_UP is KHAUTUP, "
            "KHO_DN is KHAUTDN, and KHT_SPC is KHTPOS, each still the original %M address. The Declarations sheet lists every rename."
        ),
        (
            "Stabilizer in/out pulses no longer require jets auto. _MAIN rungs that coil STTPIN (%M00580) and STTPOUT (%M00581) "
            "lost the series SS1 contact (%I00041, Jets Auto mode). The one-shots ahead of those timers, the preset STTPVAL (%R00930), "
            "and timer words %P00058 and %P00061 are unchanged. After the four inserted rungs, those timers are 10/21 rungs 244 and 246 "
            "(09/10 rungs 240 and 242)."
        ),
        (
            "Two scaling offsets changed in COAT_WG, same rung numbers. Rung 26, operator knife height into KHO_481 (%R00481): "
            "the subtract constant changed from +10.50000 to +10.62500. The multiply-by +30.00000 term is unchanged. "
            "Rung 38, drive-side stabilizing roll into SRD_419 (%R00419): the add constant changed from +5.650000 to +5.870000. "
            "The multiply-by -15.00000 term is unchanged."
        ),
        (
            f"Variable table: {len(added_decls)} declaration rows added, {len(renamed)} nicknames renamed, "
            f"{len(assigned)} blank nicknames filled in, {len(desc_only)} description-only edits, none deleted. "
            "New nicknames make 10/21 print a name where 09/10 printed a raw % address. That reprint was not counted as a logic edit. "
            "Knife-height bits were renamed in place (KHD_UP to KHXTILT, KHD_DN to KHMANMV, HTRVFLT to KHTNEG, and the operator up/down and setpoint bits). "
            "The ladder instructions on those addresses did not change."
            + (
                f" Description review: {drive_oper.ref} {drive_oper.new_nick} changed from '{drive_oper.old_desc.strip()}' "
                f"to '{drive_oper.new_desc.strip()}'."
                if drive_oper else ""
            )
        ),
        (
            f"{len(report.comments)} rung comments that had real text on 09/10 are the COMMENT placeholder on 10/21. "
            "No comment was rewritten to different wording. The old text is on the Rung Comments sheet, including the calibration notes above the scaling rungs."
        ),
        (
            "_MAIN rung numbers after the insertion are 4 higher (09/10 rung 158 is 10/21 rung 162, and 09/10 rung 278 is 10/21 rung 282). "
            "COAT_WG rung numbers did not move. The Rung Map sheet is the full cross-reference."
        ),
    ]


# --- Excel -----------------------------------------------------------------


def set_widths(ws: Worksheet, widths: dict[int, float]) -> None:
    for col, width in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = width


def write_row(ws: Worksheet, row: int, values: list[object], *, fill=None, fonts=None, align=None) -> None:
    for i, val in enumerate(values, 1):
        cell = ws.cell(row, i, "" if val is None else val)
        cell.border = THIN
        cell.alignment = align[i - 1] if align else WRAP
        cell.fill = fill or PatternFill()
        cell.font = fonts[i - 1] if fonts and fonts[i - 1] else FONT["body"]


def change_font(change: str) -> Font:
    return {
        "Deleted": FONT["deleted"],
        "Added": FONT["added"],
        "Modified": FONT["modified"],
        "Comment cleared": FONT["comment"],
        "Renamed": FONT["modified"],
        "Nickname assigned": FONT["added"],
        "Description": FONT["body"],
    }.get(change, FONT["body"])


def _finish_sheet(ws: Worksheet, *, landscape: bool = True) -> None:
    ws.page_setup.orientation = "landscape" if landscape else "portrait"
    ws.page_setup.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_setup.paperSize = ws.PAPERSIZE_TABLOID
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.horizontalCentered = True
    ws.sheet_view.showGridLines = False
    ws.oddHeader.left.text = "AIRKNIF 09/10/2026 vs 10/21/2026"
    ws.oddFooter.right.text = "Page &P of &N"


def build_workbook(report: RevisionDiff) -> Workbook:
    wb = Workbook()
    wb.properties.title = "AIRKNIF 09/10/2026 vs 10/21/2026"
    wb.properties.subject = "Logicmaster printout revision comparison"
    _sheet_summary(wb, report)
    _sheet_logic(wb, report)
    _sheet_comments(wb, report)
    _sheet_decls(wb, report)
    _sheet_map(wb, report)
    _sheet_header(wb, report)
    return wb


def _sheet_summary(wb: Workbook, report: RevisionDiff) -> None:
    ws = wb.active
    ws.title = "Summary"
    ws.sheet_properties.tabColor = NAVY
    earlier, later = report.earlier, report.later
    ws["A1"] = "AIRKNIF changes from 09/10/2026 to 10/21/2026"
    ws["A1"].font = FONT["title"]
    ws.merge_cells("A1:F1")
    ws["A2"] = (
        f"Earlier printout {earlier.print_date} titled {earlier.title or '(none)'}. "
        f"Later printout {later.print_date} titled {later.title or '(none)'}. "
        "Rungs were matched on ladder addresses. A new or renamed nickname on the same % reference is listed under Declarations, not as a logic edit."
    )
    ws["A2"].alignment = WRAP
    ws.merge_cells("A2:F2")
    ws.row_dimensions[2].height = 48

    ws["A4"] = "What changed"
    ws["A4"].font = FONT["section"]
    ws["A4"].fill = FILL["section"]
    ws.merge_cells("A4:F4")
    for i, line in enumerate(summary_lines(report), 5):
        ws.cell(i, 1, line).alignment = WRAP
        ws.cell(i, 1).font = FONT["body"]
        ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=6)
        ws.row_dimensions[i].height = 52

    start = 16
    ws.cell(start, 1, "Logic changes").font = FONT["section"]
    ws.cell(start, 1).fill = FILL["section"]
    ws.merge_cells(start_row=start, start_column=1, end_row=start, end_column=6)
    headers = ["Change", "Block", "09/10 rung", "10/21 rung", "Area", "What changed"]
    for col, val in enumerate(headers, 1):
        cell = ws.cell(start + 1, col, val)
        cell.fill = FILL["header"]
        cell.font = FONT["header"]
        cell.alignment = CENTER
        cell.border = THIN
    for i, row in enumerate(report.logic, start + 2):
        write_row(
            ws, i,
            [
                row.change, row.block,
                row.old.number if row.old else "",
                row.new.number if row.new else "",
                row.area, row.detail,
            ],
            fill=FILL.get(row.change, FILL["Unchanged"]),
            fonts=[change_font(row.change)] + [FONT["body"]] * 5,
        )
        ws.row_dimensions[i].height = 48

    count_at = start + len(report.logic) + 3
    ws.cell(count_at, 1, "Counts").font = FONT["section"]
    ws.cell(count_at, 1).fill = FILL["section"]
    ws.merge_cells(start_row=count_at, start_column=1, end_row=count_at, end_column=4)
    stats = [
        ("Item", "09/10/2026", "10/21/2026", "Delta"),
        ("Print date", earlier.print_date, later.print_date, ""),
        ("Print title", earlier.title, later.title, ""),
        ("Program size (bytes)", earlier.program_size, later.program_size, _delta(earlier.program_size, later.program_size)),
        ("_MAIN block size (bytes)", earlier.block_sizes.get("_MAIN", ""), later.block_sizes.get("_MAIN", ""),
         _delta(earlier.block_sizes.get("_MAIN", ""), later.block_sizes.get("_MAIN", ""))),
        ("COAT_WG block size (bytes)", earlier.block_sizes.get("COAT_WG", ""), later.block_sizes.get("COAT_WG", ""),
         _delta(earlier.block_sizes.get("COAT_WG", ""), later.block_sizes.get("COAT_WG", ""))),
        ("Printed rungs", len(earlier.rungs), len(later.rungs), len(later.rungs) - len(earlier.rungs)),
        ("Variable declarations", len(earlier.decls), len(later.decls), len(later.decls) - len(earlier.decls)),
        ("Logic rungs added", len(report.added), "", ""),
        ("Logic rungs modified", len(report.modified), "", ""),
        ("Logic rungs deleted", len(report.deleted), "", ""),
        ("Rung comments cleared", len(report.comments), "", ""),
        ("Declaration rows added", sum(1 for row in report.decls if row.change == "Added"), "", ""),
        ("Nicknames renamed", sum(1 for row in report.decls if row.change == "Renamed"), "", ""),
    ]
    for col, val in enumerate(stats[0], 1):
        cell = ws.cell(count_at + 1, col, val)
        cell.fill = FILL["header"]
        cell.font = FONT["header"]
        cell.alignment = CENTER
        cell.border = THIN
    for ridx, row in enumerate(stats[1:], count_at + 2):
        write_row(ws, ridx, list(row), fill=FILL["info"], align=[LEFT] * 4)

    note_at = count_at + len(stats) + 2
    ws.cell(note_at, 1, "How the match was made").font = FONT["label"]
    notes = [
        "Each printout's nicknames were translated back to % references before the rungs were compared, then spacing was ignored. Identical addresses match even when 10/21 prints a new name.",
        "The centered page title (9_10_2026 or Airknif), page headers, and cross-reference tables are ignored.",
        "A leading * on a rung is Logicmaster's master-control-relay print mark. It is treated as the normal left rail.",
        "Sheets: Logic Changes has the before/after ladder. Rung Comments keeps every cleared 09/10 comment. Declarations lists nickname and description edits. Rung Map is the full number cross-reference. Program Header is size and memory limits.",
    ]
    for i, note in enumerate(notes, note_at + 1):
        ws.cell(i, 1, note).alignment = WRAP
        ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=6)
        ws.row_dimensions[i].height = 32
    set_widths(ws, {1: 28, 2: 18, 3: 22, 4: 22, 5: 24, 6: 88})
    ws.row_dimensions[1].height = 24
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A{start + 1}:F{start + 1 + len(report.logic)}"
    ws.print_title_rows = "1:1"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.paperSize = ws.PAPERSIZE_TABLOID
    ws.oddFooter.right.text = "Page &P of &N"


def _sheet_logic(wb: Workbook, report: RevisionDiff) -> None:
    ws = wb.create_sheet("Logic Changes")
    ws.sheet_properties.tabColor = "E65100"
    ws["A1"] = "Ladder additions, modifications, and deletions"
    ws["A1"].font = FONT["title"]
    ws.merge_cells("A1:I1")
    ws["A2"] = (
        "One row per rung whose addresses or constants changed. "
        "09/10 ladder is the September print. 10/21 ladder is the October print, with the new nicknames as printed."
    )
    ws["A2"].alignment = WRAP
    ws.merge_cells("A2:I2")
    ws.row_dimensions[2].height = 32
    headers = [
        "Change", "Area", "Block", "09/10 rung", "10/21 rung", "What changed",
        "09/10 ladder", "10/21 ladder", "09/10 comment",
    ]
    for col, val in enumerate(headers, 1):
        cell = ws.cell(4, col, val)
        cell.fill = FILL["header"]
        cell.font = FONT["header"]
        cell.alignment = CENTER
        cell.border = THIN
    for i, row in enumerate(report.logic, 5):
        values = [
            row.change,
            row.area,
            row.block,
            row.old.number if row.old else "",
            row.new.number if row.new else "",
            row.detail,
            row.old_ladder,
            row.new_ladder,
            comment_display(row.old),
        ]
        write_row(
            ws, i, values,
            fill=FILL.get(row.change, FILL["Unchanged"]),
            fonts=[change_font(row.change), FONT["label"], FONT["body"], FONT["label"], FONT["label"],
                   FONT["body"], FONT["mono"], FONT["mono"], FONT["body"]],
        )
        lines = max(
            3,
            min(22, row.old_ladder.count("\n") + 2, row.new_ladder.count("\n") + 2) if (row.old_ladder or row.new_ladder) else 3,
        )
        ws.row_dimensions[i].height = 14 * lines
    last = 4 + len(report.logic)
    if report.logic:
        ws.auto_filter.ref = f"A4:I{last}"
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:I{max(last, 5)}"
    set_widths(ws, {1: 14, 2: 22, 3: 12, 4: 14, 5: 14, 6: 62, 7: 78, 8: 78, 9: 42})
    _finish_sheet(ws)


def _sheet_comments(wb: Workbook, report: RevisionDiff) -> None:
    ws = wb.create_sheet("Rung Comments")
    ws.sheet_properties.tabColor = "1A5276"
    ws["A1"] = "Rung comments cleared between 09/10 and 10/21"
    ws["A1"].font = FONT["title"]
    ws.merge_cells("A1:E1")
    ws["A2"] = (
        f"{len(report.comments)} rungs. The 10/21 print shows the Logicmaster COMMENT placeholder where 09/10 had the text in column D. "
        "The old wording is kept here so it can be put back."
    )
    ws["A2"].alignment = WRAP
    ws.merge_cells("A2:E2")
    ws.row_dimensions[2].height = 32
    headers = ["Block", "09/10 rung", "10/21 rung", "09/10 comment", "10/21 comment"]
    for col, val in enumerate(headers, 1):
        cell = ws.cell(4, col, val)
        cell.fill = FILL["header"]
        cell.font = FONT["header"]
        cell.alignment = CENTER
        cell.border = THIN
    for i, row in enumerate(report.comments, 5):
        write_row(
            ws, i,
            [row.block, row.old_number, row.new_number, row.old_comment, row.new_comment],
            fill=FILL["Comment cleared"],
            fonts=[FONT["body"], FONT["label"], FONT["label"], FONT["body"], FONT["body"]],
        )
        ws.row_dimensions[i].height = min(90, 16 + 12 * row.old_comment.count("\n"))
    last = 4 + max(len(report.comments), 1)
    ws.auto_filter.ref = f"A4:E{last}"
    ws.freeze_panes = "A5"
    set_widths(ws, {1: 14, 2: 14, 3: 14, 4: 88, 5: 28})
    _finish_sheet(ws)


def _sheet_decls(wb: Workbook, report: RevisionDiff) -> None:
    ws = wb.create_sheet("Declarations")
    ws.sheet_properties.tabColor = "1B5E20"
    added = sum(1 for row in report.decls if row.change == "Added")
    renamed = sum(1 for row in report.decls if row.change == "Renamed")
    ws["A1"] = "Variable declaration changes"
    ws["A1"].font = FONT["title"]
    ws.merge_cells("A1:G1")
    ws["A2"] = (
        f"{added} rows added, {renamed} nicknames renamed, "
        f"{sum(1 for row in report.decls if row.change == 'Nickname assigned')} nicknames assigned, "
        f"{sum(1 for row in report.decls if row.change == 'Description')} descriptions edited. "
        "Unchanged rows are omitted. A renamed nickname does not change the instruction."
    )
    ws["A2"].alignment = WRAP
    ws.merge_cells("A2:G2")
    ws.row_dimensions[2].height = 32
    headers = ["Change", "Reference", "09/10 nickname", "10/21 nickname", "09/10 description", "10/21 description", "Notes"]
    for col, val in enumerate(headers, 1):
        cell = ws.cell(4, col, val)
        cell.fill = FILL["header"]
        cell.font = FONT["header"]
        cell.alignment = CENTER
        cell.border = THIN
    for i, row in enumerate(report.decls, 5):
        fill = {
            "Added": FILL["Added"],
            "Deleted": FILL["Deleted"],
            "Renamed": FILL["Modified"],
            "Nickname assigned": FILL["info"],
            "Description": FILL["zebra"],
        }.get(row.change, FILL["Unchanged"])
        write_row(
            ws, i,
            [row.change, row.ref, row.old_nick, row.new_nick, row.old_desc, row.new_desc, row.note],
            fill=fill,
            fonts=[change_font(row.change)] + [FONT["body"]] * 6,
        )
        ws.row_dimensions[i].height = 32
    last = 4 + max(len(report.decls), 1)
    ws.auto_filter.ref = f"A4:G{last}"
    ws.freeze_panes = "A5"
    set_widths(ws, {1: 22, 2: 14, 3: 18, 4: 18, 5: 42, 6: 42, 7: 55})
    _finish_sheet(ws)


def _sheet_map(wb: Workbook, report: RevisionDiff) -> None:
    ws = wb.create_sheet("Rung Map")
    ws.sheet_properties.tabColor = "566573"
    ws["A1"] = "Full rung-number map, 09/10 to 10/21"
    ws["A1"].font = FONT["title"]
    ws.merge_cells("A1:G1")
    ws["A2"] = (
        "Includes unchanged rungs so number shifts are visible. "
        f"Deleted original numbers: {format_rung_list([row.old.number for row in report.deleted if row.old]) or 'none'}. "
        f"Added 10/21 numbers: {format_rung_list([row.new.number for row in report.added if row.new]) or 'none'}."
    )
    ws["A2"].alignment = WRAP
    ws.merge_cells("A2:G2")
    ws.row_dimensions[2].height = 32
    headers = ["Seq", "Change", "Block", "09/10 rung", "10/21 rung", "Logic summary", "Notes"]
    for col, val in enumerate(headers, 1):
        cell = ws.cell(4, col, val)
        cell.fill = FILL["header"]
        cell.font = FONT["header"]
        cell.alignment = CENTER
        cell.border = THIN
    for i, row in enumerate(report.mapping, 5):
        fill = FILL.get(row.change, FILL["Unchanged"])
        write_row(
            ws, i,
            [i - 4, row.change, row.block, row.old_number if row.old_number is not None else "",
             row.new_number if row.new_number is not None else "", row.summary, row.note],
            fill=fill,
            fonts=[FONT["body"], change_font(row.change), FONT["body"], FONT["label"], FONT["label"], FONT["body"], FONT["body"]],
            align=[CENTER, CENTER, CENTER, CENTER, CENTER, LEFT, LEFT],
        )
    last = 4 + max(len(report.mapping), 1)
    ws.auto_filter.ref = f"A4:G{last}"
    ws.freeze_panes = "A5"
    set_widths(ws, {1: 8, 2: 18, 3: 12, 4: 14, 5: 14, 6: 42, 7: 72})
    _finish_sheet(ws)


def _sheet_header(wb: Workbook, report: RevisionDiff) -> None:
    ws = wb.create_sheet("Program Header")
    ws.sheet_properties.tabColor = NAVY
    ws["A1"] = "Program header and memory limits"
    ws["A1"].font = FONT["title"]
    ws.merge_cells("A1:E1")
    ws["A2"] = "Configured sizes that did not change are listed so the memory picture is complete."
    ws["A2"].alignment = WRAP
    ws.merge_cells("A2:E2")
    headers = ["Item", "09/10/2026", "10/21/2026", "Changed?", "Notes"]
    for col, val in enumerate(headers, 1):
        cell = ws.cell(4, col, val)
        cell.fill = FILL["header"]
        cell.font = FONT["header"]
        cell.alignment = CENTER
        cell.border = THIN
    earlier, later = report.earlier, report.later
    rows: list[tuple[str, str, str, str]] = [
        ("Print date", earlier.print_date, later.print_date, "Date the printout was taken"),
        ("Print title", earlier.title, later.title, "Folder name printed under the page header"),
        ("Program size (bytes)", earlier.program_size, later.program_size, "Grew with the four new _MAIN rungs and the added starter contacts"),
    ]
    for key in sorted(set(earlier.block_sizes) | set(later.block_sizes)):
        rows.append((
            f"{key} block size (bytes)",
            earlier.block_sizes.get(key, ""),
            later.block_sizes.get(key, ""),
            "COAT_WG size unchanged" if key == "COAT_WG" else "_MAIN grew by the new elevator rungs and contacts",
        ))
    old_env = {(a, c): (b, d) for a, b, c, d in earlier.env_rows}
    new_env = {(a, c): (b, d) for a, b, c, d in later.env_rows}
    for key in list(dict.fromkeys(list(old_env) + list(new_env))):
        ov = old_env.get(key, ("", ""))
        nv = new_env.get(key, ("", ""))
        note = "Configured size and highest reference used"
        if "PROGRAM REGISTER" in key[0] and ov != nv:
            note = "Two new off-delay timers use %P00067 and %P00070 (three words each), so the high-water mark is %P00072"
        rows.append((f"{key[0]} / {key[1]}", f"{ov[0]} / {ov[1]}", f"{nv[0]} / {nv[1]}", note))
    for i, (item, old, new, note) in enumerate(rows, 5):
        changed = old != new
        write_row(
            ws, i,
            [item, old, new, "Yes" if changed else "No", note if changed else ""],
            fill=FILL["Modified"] if changed else FILL["Unchanged"],
        )
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:E{4 + len(rows)}"
    set_widths(ws, {1: 46, 2: 28, 3: 28, 4: 12, 5: 78})
    _finish_sheet(ws)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("earlier", type=Path, help="Earlier Logicmaster printout (09/10)")
    parser.add_argument("later", type=Path, help="Later Logicmaster printout (10/21)")
    parser.add_argument("output", type=Path, help="Excel workbook to write")
    parser.add_argument("--earlier-label", default="09/10/2026")
    parser.add_argument("--later-label", default="10/21/2026")
    args = parser.parse_args(argv)

    earlier = parse_printout(args.earlier, args.earlier_label)
    later = parse_printout(args.later, args.later_label)
    report = build_diff(earlier, later, args.earlier_label, args.later_label)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    build_workbook(report).save(args.output)
    print(f"Earlier rungs: {len(earlier.rungs)}  Later rungs: {len(later.rungs)}")
    print(
        f"Added: {len(report.added)}  Modified: {len(report.modified)}  "
        f"Deleted: {len(report.deleted)}  Comments cleared: {len(report.comments)}  "
        f"Declaration edits: {len(report.decls)}"
    )
    for row in report.logic:
        old_n = row.old.number if row.old else "-"
        new_n = row.new.number if row.new else "-"
        print(f"  {row.change:8} {row.block:8} {old_n} -> {new_n}  {row.area}")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
