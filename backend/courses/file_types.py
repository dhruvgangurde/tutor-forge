"""
courses/file_types.py
---------------------
Upload content checks (audit 2026-10-06 #7a).

The upload route used to trust the file name's extension, and ingestion
trusted the browser-declared MIME type; neither looks at the bytes. A renamed
executable (``notes.pdf``) or a declared type that disagrees with the
extension was accepted and only failed later inside the background job --
or, worse, was handed to a parser it was never meant for.

``content_mismatch`` checks that all three agree: the extension, the declared
MIME type, and the file's leading bytes ("magic bytes"). The allowed types are
PDF, PPTX and plain text (legacy .ppt is not accepted: python-pptx cannot read it).
"""

from __future__ import annotations

import io
import zipfile

_PDF = "application/pdf"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_TXT = "text/plain"

_ZIP_MAGIC = b"PK\x03\x04"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy Office (.ppt, .doc, .xls)

# Signatures of binary formats that must never pass as a .txt file.
_BINARY_MAGICS = (
    b"MZ",                # Windows executable
    b"\x7fELF",           # Linux executable
    b"%PDF-",
    _ZIP_MAGIC,
    _OLE2_MAGIC,
    b"\x89PNG",
    b"\xff\xd8\xff",      # JPEG
    b"GIF8",
    b"\x1f\x8b",          # gzip
    b"Rar!",
    b"7z\xbc\xaf",
)

_LABELS = {".pdf": "PDF", ".pptx": "PowerPoint (.pptx)", ".txt": "text"}
_EXPECTED_MIME = {".pdf": _PDF, ".pptx": _PPTX, ".txt": _TXT}


def _is_pdf(content: bytes) -> bool:
    # The spec allows junk before the header; readers look in the first 1 KB.
    return b"%PDF-" in content[:1024]


def _is_pptx(content: bytes) -> bool:
    if not content.startswith(_ZIP_MAGIC):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            return "ppt/presentation.xml" in zf.namelist()
    except zipfile.BadZipFile:
        return False


def _is_text(content: bytes) -> bool:
    head = content[:8192]
    if head.startswith(_BINARY_MAGICS):
        return False
    return b"\x00" not in head  # NUL bytes mean binary, whatever the name says


_CONTENT_CHECKS = {".pdf": _is_pdf, ".pptx": _is_pptx, ".txt": _is_text}


def content_mismatch(filename: str, ext: str, declared_mime: str | None, content: bytes) -> str | None:
    """
    Why this upload's type is not trustworthy, or None if it is.

    ``ext`` is the lower-cased extension, already checked against the allowed
    list. The message is shown to the teacher, so it names the file and says
    what is wrong in plain words.
    """
    label = _LABELS[ext]
    mime = (declared_mime or "").split(";", 1)[0].strip().lower()
    if mime != _EXPECTED_MIME[ext]:
        return (
            f"'{filename}' was sent as {mime or 'an unknown type'}, which does not "
            f"match its {ext} extension. Please upload a genuine {label} file."
        )
    if not _CONTENT_CHECKS[ext](content):
        return (
            f"'{filename}' does not contain {label} data, although it is named {ext}. "
            "Please check the file and upload it again."
        )
    return None
