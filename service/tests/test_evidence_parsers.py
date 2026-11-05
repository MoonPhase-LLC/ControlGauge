"""Sprint 4: parsers, chunking and adversarial inputs (run in-process here;
`test_containment.py` runs them inside the sandbox)."""

from __future__ import annotations

import io
import zipfile

import pytest

from controlgauge_service.evidence import limits, parsers
from controlgauge_service.evidence.filetype import (
    UploadRejected,
    clean_filename,
    format_for,
    sniff_matches,
)

from .evidence_fixtures import docx_body, make_docx, make_pdf

# --- upload filter ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("policy.pdf", "policy.pdf"),
        ("  Access Policy v2.docx ", "Access Policy v2.docx"),
        ("..\\..\\windows\\evil.txt", "evil.txt"),
        ("../../etc/passwd.csv", "passwd.csv"),
        ("C:\\Users\\x\\report.pdf", "report.pdf"),
    ],
)
def test_clean_filename_keeps_only_the_last_part(raw: str, expected: str) -> None:
    assert clean_filename(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "..", "dir/", "a\x00b.txt", "bad\nname.txt", "x" * 256])
def test_clean_filename_rejects_unusable_names(raw: str) -> None:
    with pytest.raises(UploadRejected) as exc:
        clean_filename(raw)
    assert exc.value.code == "invalid_filename"


@pytest.mark.parametrize(
    ("name", "fmt"), [("a.PDF", "pdf"), ("b.docx", "docx"), ("c.Txt", "txt"), ("d.csv", "csv")]
)
def test_allowed_extensions(name: str, fmt: str) -> None:
    assert format_for(name) == fmt


@pytest.mark.parametrize("name", ["a.exe", "b.doc", "c.xlsx", "noext", ".pdf", "x.pdf.js"])
def test_other_extensions_are_refused(name: str) -> None:
    with pytest.raises(UploadRejected) as exc:
        format_for(name)
    assert exc.value.code == "unsupported_file_type"


def test_sniffing() -> None:
    assert sniff_matches("pdf", b"%PDF-1.7\n")
    assert sniff_matches("pdf", b"\n" * 100 + b"%PDF-1.4")
    assert not sniff_matches("pdf", b"PK\x03\x04")
    assert sniff_matches("docx", b"PK\x03\x04rest")
    assert not sniff_matches("docx", b"%PDF-1.4")
    assert sniff_matches("txt", b"plain text")
    assert not sniff_matches("txt", b"MZ\x90\x00\x03")  # an executable renamed .txt
    assert sniff_matches("csv", b"\xff\xfea\x00,\x00")  # UTF-16 LE with BOM


# --- chunking ------------------------------------------------------------------------


def test_split_text_respects_target_and_prefers_paragraphs() -> None:
    text = ("word " * 300 + "\n\n") * 10
    chunks = parsers.split_text(text, target=1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "".join(chunks).replace(" ", "").replace("\n", "") == text.replace(" ", "").replace(
        "\n", ""
    )


def test_split_text_hard_splits_unbroken_text() -> None:
    chunks = parsers.split_text("x" * 2500, target=1000)
    assert [len(c) for c in chunks] == [1000, 1000, 500]


def test_clean_text_removes_control_characters() -> None:
    assert parsers.clean_text("a\x00b\x07c\r\nd\te") == "abc\nd\te"


# --- TXT ------------------------------------------------------------------------------


def test_txt_sections_with_line_locators() -> None:
    result = parsers.parse_txt(b"first line\nsecond line\n")
    assert result.status == "parsed"
    assert [(s.locator, s.content) for s in result.sections] == [
        ("lines 1-3", "first line\nsecond line")
    ]


def test_txt_long_file_is_chunked() -> None:
    data = ("Account review procedure step.\n" * 1000).encode()
    result = parsers.parse_txt(data)
    assert result.status == "parsed"
    assert len(result.sections) > 1
    assert all(len(s.content) <= limits.SECTION_TARGET_CHARS for s in result.sections)


@pytest.mark.parametrize(
    "data",
    [
        "café résumé".encode(),
        "\ufeffBOM text".encode(),
        "utf16 text".encode("utf-16"),
        "naïve".encode("cp1252"),
    ],
)
def test_txt_encodings(data: bytes) -> None:
    result = parsers.parse_txt(data)
    assert result.status == "parsed"
    assert result.sections[0].content.strip()


def test_txt_empty_and_whitespace() -> None:
    assert parsers.parse_txt(b"").status == "empty"
    assert parsers.parse_txt(b"   \n\n\t").status == "empty"


def test_txt_binary_is_unsupported() -> None:
    result = parsers.parse_txt(b"\x81\x8d\x8f\x90\x9d")  # undefined in cp1252, invalid UTF-8
    assert (result.status, result.detail) == ("unsupported_format", "unknown_encoding")


def test_text_limit_gives_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_EXTRACTED_CHARS", 5000)
    result = parsers.parse_txt(("abc " * 10_000).encode())
    assert (result.status, result.detail) == ("partial", "text_limit")
    assert sum(len(s.content) for s in result.sections) <= 5000


# --- CSV -------------------------------------------------------------------------------


def test_csv_rows_carry_header_and_row_numbers() -> None:
    result = parsers.parse_csv(b"user,role,mfa\nalice,admin,yes\nbob,user,no\n")
    assert result.status == "parsed"
    section = result.sections[0]
    assert section.locator == "rows 2-3"
    assert section.content.splitlines() == [
        "Columns: user | role | mfa",
        "Row 2: alice | admin | yes",
        "Row 3: bob | user | no",
    ]


def test_csv_header_only_still_has_text() -> None:
    result = parsers.parse_csv(b"user,role\n")
    assert result.status == "parsed"
    assert result.sections[0].locator == "row 1"


def test_csv_row_and_column_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_CSV_ROWS", 10)
    rows = b"a,b\n" + b"1,2\n" * 50
    result = parsers.parse_csv(rows)
    assert (result.status, result.detail) == ("partial", "row_limit")
    assert result.sections[-1].locator.endswith("-11")

    monkeypatch.setattr(limits, "MAX_CSV_COLUMNS", 3)
    result = parsers.parse_csv(b"a,b,c,d,e\n1,2,3,4,5\n")
    assert (result.status, result.detail) == ("partial", "column_limit")
    assert "4" not in result.sections[0].content.split("Row 2: ")[1]


def test_csv_huge_field_is_bounded() -> None:
    # A single quoted field larger than the csv module's default limit.
    data = b'a\n"' + b"x" * 200_000 + b'"\n'
    result = parsers.parse_csv(data)
    assert result.status in {"parsed", "partial"}


def test_csv_malformed() -> None:
    result = parsers.parse_csv(b'a,b\n1,"unterminated\x00')
    assert result.status in {"unsupported_format", "partial"}


# --- DOCX ------------------------------------------------------------------------------


def test_docx_headings_paragraphs_and_tables() -> None:
    table = (
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Account</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>Owner</w:t></w:r></w:p></w:tc></w:tr>"
        "<w:tr><w:tc><w:p><w:r><w:t>svc-backup</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>IT Ops</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    )
    body = (
        docx_body(
            [
                ("Access Control Policy", "Heading1"),
                ("Accounts are reviewed quarterly.", None),
                ("Scope", "Heading2"),
            ]
        )
        + table
    )
    result = parsers.parse_docx(make_docx(body))
    assert result.status == "parsed"
    assert result.sections[0].content == "Access Control Policy\nAccounts are reviewed quarterly."
    assert result.sections[0].locator == "Access Control Policy - paragraphs 1-2"
    assert result.sections[1].locator.startswith("Access Control Policy > Scope")
    assert "Account | Owner" in result.sections[1].content
    assert "svc-backup | IT Ops" in result.sections[1].content


def test_docx_empty_document() -> None:
    assert parsers.parse_docx(make_docx("")).status == "empty"


def test_docx_not_a_zip_or_not_word() -> None:
    assert parsers.parse_docx(b"PK\x03\x04garbage").detail == "not_docx"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("hello.txt", "hi")
    result = parsers.parse_docx(out.getvalue())
    assert (result.status, result.detail) == ("unsupported_format", "not_docx")


def test_docx_entity_expansion_is_refused() -> None:
    # "Billion laughs": refused at the DTD, before any entity can expand.
    prolog = (
        '<!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
    )
    result = parsers.parse_docx(make_docx(docx_body([("&lol2;", None)]), prolog=prolog))
    assert (result.status, result.detail) == ("unsupported_format", "unsafe_xml")


def test_docx_external_entity_is_refused() -> None:
    prolog = '<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///c:/windows/win.ini">]>'
    result = parsers.parse_docx(make_docx(docx_body([("&xxe;", None)]), prolog=prolog))
    assert (result.status, result.detail) == ("unsupported_format", "unsafe_xml")


def test_docx_malformed_xml() -> None:
    result = parsers.parse_docx(make_docx("<w:p><w:r>"))
    assert (result.status, result.detail) == ("unsupported_format", "malformed_xml")


def _zip_with_document(document: bytes, *, extra_entries: int = 0) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", document)
        for i in range(extra_entries):
            zf.writestr(f"x/{i}", "")
    return out.getvalue()


def test_docx_decompression_bomb_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # 20 MB of zeros compresses to ~20 KB: ratio ~1000:1.
    bomb = _zip_with_document(b"<a>" + b"0" * (20 * 1024 * 1024) + b"</a>")
    assert len(bomb) < 100_000
    result = parsers.parse_docx(bomb)
    assert (result.status, result.detail) == ("failed", "archive_limit")


def test_docx_declared_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_ZIP_TOTAL_UNCOMPRESSED", 1000)
    result = parsers.parse_docx(make_docx(docx_body([("x" * 5000, None)])))
    assert (result.status, result.detail) == ("failed", "archive_limit")


def test_docx_entry_count_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_ZIP_ENTRIES", 5)
    result = parsers.parse_docx(_zip_with_document(b"<a/>", extra_entries=10))
    assert (result.status, result.detail) == ("failed", "archive_limit")


def test_docx_lying_directory_sizes_are_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The zip directory says the document is tiny; inflating it says
    otherwise. The bound is enforced on bytes actually produced."""
    data = bytearray(_zip_with_document(b"<a>" + b"0" * (5 * 1024 * 1024) + b"</a>"))
    real_size = (5 * 1024 * 1024 + 7).to_bytes(4, "little")
    # Patch every occurrence of the real size (local header + central directory).
    data = bytearray(bytes(data).replace(real_size, (10).to_bytes(4, "little")))
    result = parsers.parse_docx(bytes(data))
    assert result.status in {"failed", "unsupported_format"}
    assert result.detail in {"archive_limit", "not_docx"}


# --- PDF --------------------------------------------------------------------------------


def test_pdf_pages_become_sections() -> None:
    result = parsers.parse_pdf(make_pdf(["Access control policy", "Second page"]))
    assert result.status == "parsed"
    assert [s.locator for s in result.sections] == ["page 1", "page 2"]
    assert "Access control policy" in result.sections[0].content


def test_pdf_without_text_is_empty() -> None:
    result = parsers.parse_pdf(make_pdf([""]))
    assert result.status == "empty"


def test_pdf_garbage_is_unsupported() -> None:
    result = parsers.parse_pdf(b"%PDF-1.4\nnot really a pdf")
    assert (result.status, result.detail) == ("unsupported_format", "corrupt_pdf")


def test_pdf_page_limit_gives_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_PDF_PAGES", 2)
    result = parsers.parse_pdf(make_pdf(["one", "two", "three"]))
    assert (result.status, result.detail) == ("partial", "page_limit")
    assert [s.locator for s in result.sections] == ["page 1", "page 2"]


def test_pdf_encrypted_is_unsupported() -> None:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(make_pdf(["secret"]))).pages:
        writer.add_page(page)
    writer.encrypt(user_password="pw", owner_password="owner", algorithm="RC4-128")
    out = io.BytesIO()
    writer.write(out)
    result = parsers.parse_pdf(out.getvalue())
    assert (result.status, result.detail) == ("unsupported_format", "encrypted")


def test_parse_dispatch_unknown_format() -> None:
    assert parsers.parse("exe", b"").status == "unsupported_format"
