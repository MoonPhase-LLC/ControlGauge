"""Builders for small, deterministic evidence files used by Sprint 4
tests (no binary fixtures checked in)."""

from __future__ import annotations

import io
import zipfile


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")


def make_pdf(pages: list[str], *, extra_trailer: str = "") -> bytes:
    """A minimal valid PDF with one text line per page (Helvetica)."""
    objects: list[bytes] = []
    n_pages = len(pages)
    font_id = 3 + 2 * n_pages
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    for i, text in enumerate(pages):
        content_id = 4 + 2 * i
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
        lines = text.split("\n")
        ops = (
            "BT /F1 12 Tf 72 720 Td 14 TL "
            + " ".join(f"({_pdf_escape(line)}) Tj T*" for line in lines)
            + " ET"
        )
        stream = ops.encode("latin-1")
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R {extra_trailer}>>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
)
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def docx_body(paragraphs: list[tuple[str, str | None]]) -> str:
    parts = []
    for text, style in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        parts.append(f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>')
    return "".join(parts)


def make_docx(body_xml: str, *, prolog: str = "", extra: dict[str, bytes] | None = None) -> bytes:
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?>{prolog}'
        f'<w:document xmlns:w="{_W}"><w:body>{body_xml}</w:body></w:document>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("word/document.xml", document)
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return out.getvalue()
