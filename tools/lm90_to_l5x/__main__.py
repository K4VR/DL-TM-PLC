from __future__ import annotations

import argparse
from pathlib import Path

from .convert_logic import build_conversion
from .emit_l5x import emit_l5x, emit_report
from .extract import extract_text
from .parse_source import parse_program


def convert(source: Path, dest: Path) -> None:
    text = extract_text(source)
    raw = parse_program(text)
    conv = build_conversion(raw)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(emit_l5x(conv), encoding="utf-8")
    report = dest.with_suffix(".report.txt")
    report.write_text(emit_report(conv), encoding="utf-8")
    print(f"Wrote {dest}")
    print(f"Wrote {report}")
    print(f"Tags {len(conv.tags)} aliases {len(conv.aliases)} mapped {conv.mapped_rungs} unmapped {conv.unmapped_rungs}")


def main() -> None:
    p = argparse.ArgumentParser(description="Convert a Logicmaster 90-30 printout to a 1756-L81E L5X")
    p.add_argument("source", type=Path)
    p.add_argument("dest", type=Path)
    args = p.parse_args()
    convert(args.source, args.dest)


if __name__ == "__main__":
    main()
