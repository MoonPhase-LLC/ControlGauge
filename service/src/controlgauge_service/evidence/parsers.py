"""Text extraction and chunking for PDF, DOCX, TXT and CSV (Sprint 4).

Runs **only inside the contained parser worker** (`worker.py`) on bytes the
service passed in; it never opens a path. Every function returns a
`ParseResult` whose `status` is one of the `DATABASE.md` §3 outcomes and
whose `detail` is a fixed category -- never text from the document.

Bounds (`limits.py`): pages, rows, columns, zip entries and inflated size,
extracted characters and section count. Exceeding a content bound keeps
what was extracted so far and reports `partial`; a structural bound (a zip
bomb) refuses the file.

Libraries: `pypdf` (pure Python) for PDF; the standard library for the rest
-- DOCX is read with `zipfile` and `xml.parsers.expat` with any DTD or
entity declaration refused, so no entity expansion or external fetch can
happen (T-02).
"""

from __future__ import annotations

import codecs
import csv
import io
import re
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, NoReturn
from xml.parsers import expat

from . import limits

PARSER_VERSION = "cg-parsers/1"

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


@dataclass
class Section:
    content: str
    locator: str


@dataclass
class ParseResult:
    status: str  # parsed | partial | empty | unsupported_format | failed
    detail: str | None = None
    sections: list[Section] = field(default_factory=list)
    parser: str = PARSER_VERSION


class _Budget:
    """Tracks the extracted-text and section budgets for one file."""

    def __init__(self) -> None:
        self.chars = 0
        self.sections: list[Section] = []
        self.exhausted = False

    def add(self, content: str, locator: str) -> bool:
        """Adds one section; returns False once a budget is exhausted."""
        if self.exhausted:
            return False
        if not content.strip():
            return True
        if (
            self.chars + len(content) > limits.MAX_EXTRACTED_CHARS
            or len(self.sections) >= limits.MAX_SECTIONS
        ):
            self.exhausted = True
            return False
        self.chars += len(content)
        self.sections.append(Section(content, locator[:300]))
        return True


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL.sub("", text)


def split_text(text: str, target: int = limits.SECTION_TARGET_CHARS) -> list[str]:
    """Splits text into chunks of at most `target` characters, preferring
    paragraph, then line, then word boundaries."""
    text = text.strip()
    if len(text) <= target:
        return [text] if text else []
    chunks: list[str] = []
    rest = text
    while len(rest) > target:
        window = rest[:target]
        cut = -1
        for sep in ("\n\n", "\n", " "):
            cut = window.rfind(sep)
            if cut > target // 4:
                break
        if cut <= target // 4:
            cut = target
        piece = rest[:cut].strip()
        if piece:
            chunks.append(piece)
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


def _chunk_locators(base: str, chunks: list[str]) -> Iterator[tuple[str, str]]:
    if len(chunks) == 1:
        yield chunks[0], base
        return
    for i, chunk in enumerate(chunks, start=1):
        yield chunk, f"{base} (part {i} of {len(chunks)})"


def _finish(
    budget: _Budget, *, partial_detail: str | None, failed_all: bool = False
) -> ParseResult:
    if budget.exhausted:
        return ParseResult("partial", "text_limit", budget.sections)
    if not budget.sections:
        if failed_all:
            return ParseResult("failed", partial_detail or "extraction_error")
        return ParseResult("empty", partial_detail)
    if partial_detail is not None:
        return ParseResult("partial", partial_detail, budget.sections)
    return ParseResult("parsed", None, budget.sections)


# --- text decoding (TXT, CSV) ---------------------------------------------------


def decode_text(data: bytes) -> str | None:
    """BOM-detected UTF-8/UTF-16, else strict UTF-8, else strict
    Windows-1252. None if none fits or the result looks binary."""
    if data.startswith(codecs.BOM_UTF8):
        candidates = ["utf-8-sig"]
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        candidates = ["utf-16"]
    else:
        candidates = ["utf-8", "cp1252"]
    for encoding in candidates:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" in text:
            return None
        return text
    return None


def parse_txt(data: bytes) -> ParseResult:
    text = decode_text(data)
    if text is None:
        return ParseResult("unsupported_format", "unknown_encoding")
    text = clean_text(text)
    budget = _Budget()
    # Group paragraphs into sections, tracking source line numbers.
    line_no = 1
    buf: list[str] = []
    buf_start = 1
    buf_len = 0

    def flush(end_line: int) -> bool:
        nonlocal buf, buf_len
        if not buf:
            return True
        block = "\n".join(buf)
        buf, buf_len = [], 0
        for chunk, loc in _chunk_locators(f"lines {buf_start}-{end_line}", split_text(block)):
            if not budget.add(chunk, loc):
                return False
        return True

    for line in text.split("\n"):
        if not buf:
            buf_start = line_no
        if buf_len + len(line) > limits.SECTION_TARGET_CHARS and buf:
            if not flush(line_no - 1):
                break
            buf_start = line_no
        buf.append(line)
        buf_len += len(line) + 1
        line_no += 1
    else:
        flush(line_no - 1)
    return _finish(budget, partial_detail=None)


def parse_csv(data: bytes) -> ParseResult:
    text = decode_text(data)
    if text is None:
        return ParseResult("unsupported_format", "unknown_encoding")
    csv.field_size_limit(limits.MAX_EXTRACTED_CHARS)
    reader = csv.reader(io.StringIO(text, newline=""))
    budget = _Budget()
    detail: str | None = None
    header: list[str] | None = None
    lines: list[str] = []
    first_row = 0
    size = 0
    row_no = 0
    last_kept = 0

    def flush(last_row: int) -> bool:
        nonlocal lines, size
        if not lines:
            return True
        head = "Columns: " + " | ".join(header or [])
        content = head + "\n" + "\n".join(lines)
        lines, size = [], 0
        return budget.add(content, f"rows {first_row}-{last_row}")

    try:
        for row in reader:
            row_no += 1
            if len(row) > limits.MAX_CSV_COLUMNS:
                row = row[: limits.MAX_CSV_COLUMNS]
                detail = "column_limit"
            cells = [clean_text(c).replace("\n", " ").strip() for c in row]
            if header is None:
                header = cells
                continue
            if row_no - 1 > limits.MAX_CSV_ROWS:
                detail = "row_limit"
                break
            if not any(cells):
                continue
            line = f"Row {row_no}: " + " | ".join(cells)
            if size + len(line) > limits.SECTION_TARGET_CHARS and lines:
                if not flush(row_no - 1):
                    break
            if not lines:
                first_row = row_no
            lines.append(line)
            size += len(line) + 1
            last_kept = row_no
    except csv.Error:
        if header is None:
            return ParseResult("unsupported_format", "malformed_csv")
        detail = "malformed_rows"
    flush(last_kept)
    if header is not None and not budget.sections and not budget.exhausted:
        # A header with no data rows still carries text.
        budget.add("Columns: " + " | ".join(header), "row 1")
    return _finish(budget, partial_detail=detail)


# --- DOCX ------------------------------------------------------------------------

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_HEADING_STYLE = re.compile(r"(?i)^(?:heading\s*([1-9])|title)$")


class _UnsafeXml(Exception):
    pass


class _ZipLimit(Exception):
    pass


def _bounded_read(zf: zipfile.ZipFile, info: zipfile.ZipInfo, budget: list[int]) -> Iterator[bytes]:
    """Inflates one entry in chunks, enforcing the remaining total budget
    and the compression ratio as bytes actually come out (the directory's
    sizes are not trusted)."""
    produced = 0
    allowed_by_ratio = max(info.compress_size, 1) * limits.MAX_ZIP_RATIO + 1024 * 1024
    with zf.open(info) as stream:
        while True:
            chunk = stream.read(256 * 1024)
            if not chunk:
                return
            produced += len(chunk)
            budget[0] -= len(chunk)
            if budget[0] < 0 or produced > allowed_by_ratio:
                raise _ZipLimit()
            yield chunk


class _DocxHandler:
    def __init__(self) -> None:
        self.blocks: list[tuple[str, int | None]] = []  # (text, heading level)
        self._para: list[str] | None = None
        self._style_level: int | None = None
        self._in_text = False
        self._table_depth = 0
        self._cell: list[str] = []
        self._row: list[str] = []

    def start(self, name: str, attrs: dict[str, str]) -> None:
        if not name.startswith(_W):
            return
        local = name[len(_W) + 1 :]
        if local == "p":
            self._para = []
            self._style_level = None
        elif local == "pStyle" and self._para is not None:
            match = _HEADING_STYLE.match(attrs.get(f"{_W} val", ""))
            if match:
                self._style_level = int(match.group(1)) if match.group(1) else 1
        elif local == "outlineLvl" and self._para is not None:
            val = attrs.get(f"{_W} val", "")
            if val.isdigit() and int(val) < 9:
                self._style_level = int(val) + 1
        elif local == "t":
            self._in_text = True
        elif local == "tab" and self._para is not None:
            self._para.append("\t")
        elif local in ("br", "cr") and self._para is not None:
            self._para.append("\n")
        elif local == "tbl":
            self._table_depth += 1
            if self._table_depth == 1:
                self._row = []
        elif local == "tr" and self._table_depth == 1:
            self._row = []
        elif local == "tc" and self._table_depth == 1:
            self._cell = []

    def end(self, name: str) -> None:
        if not name.startswith(_W):
            return
        local = name[len(_W) + 1 :]
        if local == "t":
            self._in_text = False
        elif local == "p" and self._para is not None:
            text = clean_text("".join(self._para)).strip()
            if self._table_depth:
                if text:
                    self._cell.append(text)
            elif text:
                self.blocks.append((text, self._style_level))
            self._para = None
        elif local == "tc" and self._table_depth == 1:
            self._row.append(" ".join(self._cell))
            self._cell = []
        elif local == "tr" and self._table_depth == 1:
            if any(c.strip() for c in self._row):
                self.blocks.append((" | ".join(self._row), None))
            self._row = []
        elif local == "tbl":
            self._table_depth = max(0, self._table_depth - 1)

    def chars(self, data: str) -> None:
        if self._in_text and self._para is not None:
            self._para.append(data)


def _refuse(*_args: Any) -> NoReturn:
    raise _UnsafeXml()


def _parse_docx_xml(chunks: Iterable[bytes]) -> _DocxHandler:
    handler = _DocxHandler()
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    parser.UnparsedEntityDeclHandler = _refuse
    parser.ExternalEntityRefHandler = _refuse
    parser.StartElementHandler = handler.start
    parser.EndElementHandler = handler.end
    parser.CharacterDataHandler = handler.chars
    parser.buffer_text = True
    for chunk in chunks:
        parser.Parse(chunk, False)
    parser.Parse(b"", True)
    return handler


def parse_docx(data: bytes) -> ParseResult:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError):
        return ParseResult("unsupported_format", "not_docx")
    with zf:
        try:
            infos = zf.infolist()
        except (zipfile.BadZipFile, ValueError):
            return ParseResult("unsupported_format", "not_docx")
        if len(infos) > limits.MAX_ZIP_ENTRIES:
            return ParseResult("failed", "archive_limit")
        declared = sum(i.file_size for i in infos)
        if declared > limits.MAX_ZIP_TOTAL_UNCOMPRESSED:
            return ParseResult("failed", "archive_limit")
        names = {i.filename: i for i in infos}
        doc = names.get("word/document.xml")
        if "[Content_Types].xml" not in names or doc is None:
            return ParseResult("unsupported_format", "not_docx")
        if doc.flag_bits & 0x1:
            return ParseResult("unsupported_format", "encrypted")
        budget_left = [limits.MAX_ZIP_TOTAL_UNCOMPRESSED]
        try:
            handler = _parse_docx_xml(_bounded_read(zf, doc, budget_left))
        except _ZipLimit:
            return ParseResult("failed", "archive_limit")
        except _UnsafeXml:
            return ParseResult("unsupported_format", "unsafe_xml")
        except expat.ExpatError:
            return ParseResult("unsupported_format", "malformed_xml")
        except (zipfile.BadZipFile, NotImplementedError, OSError, EOFError):
            return ParseResult("unsupported_format", "not_docx")

    budget = _Budget()
    headings: list[str] = []
    buf: list[str] = []
    para_start = 1

    def locator(first: int, last: int) -> str:
        span = f"paragraph {first}" if first == last else f"paragraphs {first}-{last}"
        return f"{' > '.join(headings)} - {span}" if headings else span

    def flush(last: int) -> bool:
        nonlocal buf
        if not buf:
            return True
        block = "\n".join(buf)
        buf = []
        for chunk, loc in _chunk_locators(locator(para_start, last), split_text(block)):
            if not budget.add(chunk, loc):
                return False
        return True

    size = 0
    for index, (text, level) in enumerate(handler.blocks, start=1):
        if level is not None:
            if not flush(index - 1):
                break
            del headings[level - 1 :]
            headings.extend([""] * (level - 1 - len(headings)))
            headings.append(text[:80])
            headings[:] = [h for h in headings if h]
            size = 0
        if not buf:
            para_start = index
        if size + len(text) > limits.SECTION_TARGET_CHARS and buf:
            if not flush(index - 1):
                break
            para_start = index
            size = 0
        buf.append(text)
        size += len(text) + 1
    else:
        flush(len(handler.blocks))
    return _finish(budget, partial_detail=None)


# --- PDF --------------------------------------------------------------------------


def parse_pdf(data: bytes) -> ParseResult:
    import logging

    from pypdf import PdfReader
    from pypdf.errors import DependencyError, PdfReadError, PyPdfError

    # pypdf reports recoverable problems through logging; nothing from the
    # document may reach a log, and the worker's stderr is discarded anyway.
    logging.getLogger("pypdf").disabled = True

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    return ParseResult("unsupported_format", "encrypted")
            except (DependencyError, NotImplementedError, PyPdfError):
                return ParseResult("unsupported_format", "encrypted")
        page_count = len(reader.pages)
    except (PdfReadError, PyPdfError, ValueError, KeyError, TypeError, OSError, EOFError):
        return ParseResult("unsupported_format", "corrupt_pdf")

    detail: str | None = None
    if page_count > limits.MAX_PDF_PAGES:
        detail = "page_limit"
    budget = _Budget()
    failed = 0
    attempted = min(page_count, limits.MAX_PDF_PAGES)
    for index in range(attempted):
        try:
            text = reader.pages[index].extract_text() or ""
        except MemoryError:
            raise
        except Exception:  # noqa: BLE001 - one bad page must not sink the file
            failed += 1
            continue
        text = clean_text(text)
        stop = False
        for chunk, loc in _chunk_locators(f"page {index + 1}", split_text(text)):
            if not budget.add(chunk, loc):
                stop = True
                break
        if stop:
            break
    if failed and detail is None:
        detail = "pages_failed"
    return _finish(
        budget,
        partial_detail=detail,
        failed_all=attempted > 0 and failed == attempted,
    )


PARSERS = {"pdf": parse_pdf, "docx": parse_docx, "txt": parse_txt, "csv": parse_csv}


def parse(fmt: str, data: bytes) -> ParseResult:
    parser = PARSERS.get(fmt)
    if parser is None:
        return ParseResult("unsupported_format", "unknown_format")
    return parser(data)
