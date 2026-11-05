import type { ParseStatus } from "@/service/types";

// Plain-language wording for ingestion states. `parse_detail` values are
// fixed service categories; anything unknown falls back to generic text
// (it is never shown raw as a sentence).

export const STATUS_LABELS: Record<ParseStatus, string> = {
  pending: "Queued",
  parsing: "Extracting text",
  parsed: "Text extracted",
  partial: "Partly extracted",
  empty: "No text found",
  unsupported_format: "Unsupported file",
  failed: "Extraction failed",
};

export type StatusTone = "progress" | "ok" | "warning" | "error";

export function statusTone(status: ParseStatus): StatusTone {
  switch (status) {
    case "pending":
    case "parsing":
      return "progress";
    case "parsed":
      return "ok";
    case "partial":
    case "empty":
      return "warning";
    default:
      return "error";
  }
}

const DETAILS: Record<string, string> = {
  encrypted: "The file is password-protected.",
  corrupt_pdf: "The PDF is damaged or not a PDF.",
  not_docx: "The file is not a Word (.docx) document.",
  unsafe_xml: "The document contains constructs that are refused for safety.",
  malformed_xml: "The document's content is damaged.",
  unknown_encoding: "The text encoding is not recognized, or the file is not text.",
  malformed_csv: "The CSV file could not be read.",
  malformed_rows: "Some CSV rows could not be read; the rows before them were kept.",
  archive_limit: "The document expands beyond the size limit (possible decompression bomb).",
  page_limit: "Only the first 2,000 pages were extracted.",
  row_limit: "Only the first 200,000 rows were extracted.",
  column_limit: "Columns beyond the first 500 were left out.",
  text_limit: "Extraction stopped at the 10 MB text limit.",
  pages_failed: "Some pages could not be read.",
  timeout: "Extraction took longer than the time limit.",
  cpu_limit: "Extraction used more processing time than allowed.",
  memory_limit: "Extraction needed more memory than allowed.",
  output_limit: "Extraction produced more output than allowed.",
  file_too_large: "The file is larger than the upload limit.",
  containment_unavailable: "Files can only be processed on Windows, where the parser sandbox is available.",
  sandbox_launch_failed: "The parser sandbox could not start.",
  sandbox_setup_failed: "The parser sandbox could not be prepared.",
  stored_file_missing: "The stored copy of the file is missing.",
};

/** Explanation for a non-`parsed` outcome, or null when there is none. */
export function describeDetail(status: ParseStatus, detail: string | null): string | null {
  if (status === "empty" && !detail) {
    return "The file has no extractable text (for example, a scanned image without OCR).";
  }
  if (!detail) return null;
  return DETAILS[detail] ?? "The file could not be processed.";
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const trim = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1));
  if (bytes < 1024 * 1024) return `${trim(bytes / 1024)} KB`;
  return `${trim(bytes / (1024 * 1024))} MB`;
}

export const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".txt", ".csv"];

export function hasAcceptedExtension(name: string): boolean {
  const lower = name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext) && lower.length > ext.length);
}
