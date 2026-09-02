from __future__ import annotations

from dataclasses import dataclass, field


BOOL_PREFIXES = ("I", "Q", "M", "T", "S", "SA", "SB", "SC", "G")
INT_PREFIXES = ("R", "AI", "AQ")

KNOWN_SYSTEM = {
    "S0001": ("FST_SCN", "First scan"),
    "S0002": ("FST_EXE", "First execution"),
    "S0003": ("T_10MS", "0.01 second clock"),
    "S0004": ("T_100MS", "0.1 second clock"),
    "S0005": ("T_SEC", "1 second clock"),
    "S0006": ("T_MIN", "1 minute clock"),
    "S0007": ("ALW_ON", "Always ON"),
    "S0008": ("ALW_OFF", "Always OFF"),
    "S0011": ("OVR_PRE", "Override present"),
    "SA009": ("CFG_MM", "Configuration mismatch"),
    "SA011": ("LOW_BAT", "Low battery"),
    "SA014": ("LOS_IOM", "Loss of I/O module"),
    "SA015": ("LOS_SIO", "Loss of serial I/O"),
    "SC009": ("ANY_FLT", "Any fault"),
    "SC013": ("IO_PRES", "I/O present / I/O fault"),
}


def ge_ref_to_tag(ref: str) -> str:
    ref = ref.strip().upper()
    if ref.startswith("%"):
        ref = ref[1:]
    return ref


def datatype_for(tag: str) -> str:
    for prefix in ("SA", "SB", "SC", "AI", "AQ"):
        if tag.startswith(prefix) and tag[len(prefix) :].isdigit():
            return "BOOL" if prefix in BOOL_PREFIXES else "INT"
    if tag[:1] in BOOL_PREFIXES and tag[1:].isdigit():
        return "BOOL"
    if tag[:1] in INT_PREFIXES and tag[1:].isdigit():
        return "INT"
    return "BOOL"


def sanitize_alias(name: str) -> str:
    out = []
    for ch in name.strip():
        if ch.isalnum() or ch == "_":
            out.append(ch)
        elif ch == "-":
            out.append("_")
        else:
            out.append("_")
    alias = "".join(out)
    if not alias:
        alias = "GE_ALIAS"
    if alias[0].isdigit():
        alias = "N_" + alias
    return alias[:40]


def collapse_desc(text: str) -> str:
    return " ".join(text.split())


@dataclass
class TagDef:
    name: str
    datatype: str
    description: str = ""
    alias_for: str | None = None


@dataclass
class LogixRung:
    ge_number: int
    comment: str
    text: str
    source: str = ""
    mapped: bool = True
    notes: list[str] = field(default_factory=list)


@dataclass
class BlockDef:
    name: str
    description: str = ""
    rungs: list[LogixRung] = field(default_factory=list)


@dataclass
class Conversion:
    tags: dict[str, TagDef] = field(default_factory=dict)
    aliases: dict[str, TagDef] = field(default_factory=dict)
    blocks: list[BlockDef] = field(default_factory=list)
    subroutines: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    mapped_rungs: int = 0
    unmapped_rungs: int = 0
