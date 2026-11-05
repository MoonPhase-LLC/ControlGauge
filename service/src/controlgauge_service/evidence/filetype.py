"""Upload filtering: allowed extensions and a content sniff (`SECURITY.md`
T-04/T-05, `USER_FLOWS.md` §5).

This is the *initial* filter. It runs in the trusted service on raw bytes
and parses nothing: it only checks a file's leading bytes against its
claimed type. Whether the content is really usable is the contained
parser's call (`unsupported_format`, `DATABASE.md` §3).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from .limits import MAX_FILENAME_CHARS

Format = Literal["pdf", "docx", "txt", "csv"]

MIME_TYPES: dict[Format, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "csv": "text/csv",
}

_EXTENSIONS: dict[str, Format] = {".pdf": "pdf", ".docx": "docx", ".txt": "txt", ".csv": "csv"}

_DISALLOWED_NAME_CHARS = re.compile(r'[\x00-\x1f\x7f-\x9f<>:"/\\|?*  ]')


class UploadRejected(ValueError):
    """`code` is a fixed category for the API error contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def clean_filename(raw: str) -> str:
    """The display name stored for an artifact. Never used as a path
    (files are stored by hash, T-05): any directory part is dropped,
    characters Windows forbids in names and control characters are
    refused, and the name is NFC-normalized and length-bounded."""
    name = unicodedata.normalize("NFC", raw).strip()
    # Drop any path a client might have sent; only the last part names it.
    name = re.split(r"[\\/]", name)[-1].strip()
    if not name or name in {".", ".."}:
        raise UploadRejected("invalid_filename")
    if len(name) > MAX_FILENAME_CHARS:
        raise UploadRejected("invalid_filename")
    if _DISALLOWED_NAME_CHARS.search(name):
        raise UploadRejected("invalid_filename")
    return name


def format_for(filename: str) -> Format:
    dot = filename.rfind(".")
    ext = filename[dot:].lower() if dot > 0 else ""
    fmt = _EXTENSIONS.get(ext)
    if fmt is None:
        raise UploadRejected("unsupported_file_type")
    return fmt


def sniff_matches(fmt: Format, head: bytes) -> bool:
    """Whether the leading bytes are plausible for `fmt`.

    - PDF: `%PDF-` within the first 1024 bytes (the PDF spec's allowance).
    - DOCX: a zip local-file header. Whether it really is OOXML is decided
      by the parser.
    - TXT/CSV: no NUL byte in the sample, unless it starts with a UTF-16
      byte-order mark (UTF-16 text is full of NULs).
    """
    if fmt == "pdf":
        return b"%PDF-" in head[:1024]
    if fmt == "docx":
        return head.startswith(b"PK\x03\x04")
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    return b"\x00" not in head
