from __future__ import annotations

import argparse
from pathlib import Path

from .convert_logic import build_conversion
from .emit_l5x import emit_l5x, emit_report
from .extract import extract_text
from .parse_source import parse_program


def infer_controller_name(dest: Path) -> str:
    stem = dest.stem
    for suffix in ("_1756-L81E", "-1756-L81E", "_1756_L81E"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem or "GEPROG"


def convert(
    source: Path,
    dest: Path,
    controller: str | None = None,
    program: str | None = None,
    title: str | None = None,
) -> None:
    text = extract_text(source)
    raw = parse_program(text)
    conv = build_conversion(raw)
    controller = controller or infer_controller_name(dest)
    program = program or controller
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(emit_l5x(conv, controller=controller, program=program, title=title), encoding="utf-8")
    report = dest.with_suffix(".report.txt")
    report.write_text(emit_report(conv, controller=controller, program=program), encoding="utf-8")
    print(f"Wrote {dest}")
    print(f"Wrote {report}")
    print(f"Tags {len(conv.tags)} aliases {len(conv.aliases)} mapped {conv.mapped_rungs} unmapped {conv.unmapped_rungs}")


def main() -> None:
    p = argparse.ArgumentParser(description="Convert a Logicmaster 90-30 printout to a 1756-L81E L5X")
    p.add_argument("source", type=Path)
    p.add_argument("dest", type=Path)
    p.add_argument("--controller", default=None, help="Logix controller / program name (default: dest stem)")
    p.add_argument("--program", default=None, help="Logix program name (default: same as controller)")
    p.add_argument("--title", default=None, help="Controller description")
    args = p.parse_args()
    convert(args.source, args.dest, controller=args.controller, program=args.program, title=args.title)


if __name__ == "__main__":
    main()
