from __future__ import annotations

from pathlib import Path


def decode_printout_bytes(data: bytes) -> str:
    """Decode a Logicmaster printout. Mid-1990s LM90 files are CP437; later extracts are UTF-8."""
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp437")


def extract_text(path: str | Path) -> str:
    path = Path(path)
    if path.suffix.lower() == ".docx":
        from docx import Document

        doc = Document(str(path))
        return "\n".join(p.text.rstrip() for p in doc.paragraphs) + "\n"
    return decode_printout_bytes(path.read_bytes())
