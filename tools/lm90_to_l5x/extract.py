from __future__ import annotations

from pathlib import Path


def extract_text(path: str | Path) -> str:
    path = Path(path)
    if path.suffix.lower() == ".txt":
        return path.read_text(encoding="utf-8", errors="replace")
    from docx import Document

    doc = Document(str(path))
    return "\n".join(p.text.rstrip() for p in doc.paragraphs) + "\n"
