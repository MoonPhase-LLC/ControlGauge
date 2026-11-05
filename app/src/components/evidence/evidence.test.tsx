import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { clearMocks, mockIPC } from "@tauri-apps/api/mocks";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Artifact, ParseStatus } from "@/service/types";
import { EvidencePanel } from "./EvidencePanel";
import { describeDetail, hasAcceptedExtension } from "./evidenceText";

// Sprint 4 evidence panel over Tauri's IPC mock. The real Rust/Python path
// (including the parser sandbox) is covered by `cargo test` and pytest.

const ASSESSMENT = "11111111-1111-4111-8111-000000000001";
const LIMITS = {
  max_file_bytes: 50 * 1024 * 1024,
  max_files_per_batch: 50,
  max_artifacts_per_assessment: 1000,
  max_assessment_bytes: 2 * 1024 * 1024 * 1024,
};

function artifact(n: number, filename: string, status: ParseStatus, extra: Partial<Artifact> = {}): Artifact {
  return {
    id: `22222222-2222-4222-8222-${String(n).padStart(12, "0")}`,
    assessment_id: ASSESSMENT,
    filename,
    sha256: "0".repeat(64),
    size_bytes: 1234,
    format: filename.split(".").pop() ?? "txt",
    mime_type: "text/plain",
    parse_status: status,
    parse_detail: null,
    parser_version: status === "pending" ? null : "cg-parsers/1",
    section_count: status === "parsed" ? 2 : 0,
    uploaded_at: "2026-11-05T10:00:00Z",
    parsed_at: null,
    ...extra,
  };
}

function mockEvidence(initial: Artifact[] = []) {
  const artifacts = [...initial];
  const calls: { cmd: string; args: unknown }[] = [];
  const uploads: Uint8Array[] = [];
  let duplicateNext = false;
  mockIPC((cmd, args) => {
    calls.push({ cmd, args });
    if (cmd === "list_artifacts") {
      return {
        artifacts,
        total: artifacts.length,
        total_bytes: artifacts.reduce((n, a) => n + a.size_bytes, 0),
        limit: 200,
        offset: 0,
        limits: LIMITS,
      };
    }
    if (cmd === "upload_evidence") {
      uploads.push(args as unknown as Uint8Array);
      if (duplicateNext) {
        duplicateNext = false;
        return { artifact: artifacts[0], duplicate: true };
      }
      const created = artifact(artifacts.length + 1, `upload-${artifacts.length + 1}.txt`, "pending");
      artifacts.unshift(created);
      return { artifact: created, duplicate: false };
    }
    if (cmd === "list_artifact_sections") {
      return {
        artifact_id: (args as { id: string }).id,
        sections: [
          { id: "s1", sequence_index: 0, locator: "page 1", content: "<img src=x onerror=alert(1)> policy text" },
          { id: "s2", sequence_index: 1, locator: "page 2", content: "Second page" },
        ],
        total: 2,
        limit: 20,
        offset: 0,
      };
    }
    throw new Error(`unexpected command ${cmd}`);
  });
  return {
    artifacts,
    calls,
    uploads,
    duplicateNext() {
      duplicateNext = true;
    },
  };
}

afterEach(() => {
  clearMocks();
  vi.useRealTimers();
});

function file(name: string, content = "text", size?: number) {
  const f = new File([content], name, { type: "text/plain" });
  if (size !== undefined) Object.defineProperty(f, "size", { value: size });
  return f;
}

describe("evidence panel", () => {
  it("shows an empty state and the limits", async () => {
    mockEvidence();
    render(<EvidencePanel assessmentId={ASSESSMENT} />);
    expect(await screen.findByText("No evidence has been uploaded to this assessment yet.")).toBeVisible();
    expect(screen.getByText(/up to 50 MB each and 50 files at a time/)).toBeVisible();
  });

  it("lists every ingestion outcome with plain-language status", async () => {
    mockEvidence([
      artifact(1, "policy.pdf", "parsed"),
      artifact(2, "scan.pdf", "empty"),
      artifact(3, "locked.pdf", "unsupported_format", { parse_detail: "encrypted" }),
      artifact(4, "bomb.docx", "failed", { parse_detail: "archive_limit" }),
      artifact(5, "big.csv", "partial", { parse_detail: "row_limit", section_count: 9 }),
    ]);
    render(<EvidencePanel assessmentId={ASSESSMENT} />);
    const rows = await screen.findAllByTestId("artifact-row");
    expect(rows).toHaveLength(5);
    expect(within(rows[0]).getByText("Text extracted")).toBeVisible();
    expect(within(rows[1]).getByText("No text found")).toBeVisible();
    expect(within(rows[1]).getByText(/scanned image/)).toBeVisible();
    expect(within(rows[2]).getByText("Unsupported file")).toBeVisible();
    expect(within(rows[2]).getByText("The file is password-protected.")).toBeVisible();
    expect(within(rows[3]).getByText("Extraction failed")).toBeVisible();
    expect(within(rows[3]).getByText(/decompression bomb/)).toBeVisible();
    expect(within(rows[4]).getByText("Partly extracted")).toBeVisible();
  });

  it("uploads selected files one by one and flags a duplicate", async () => {
    const ipc = mockEvidence();
    render(<EvidencePanel assessmentId={ASSESSMENT} />);
    await screen.findByText(/No evidence/);
    const input = screen.getByTestId("evidence-input");
    fireEvent.change(input, { target: { files: [file("a.txt", "alpha"), file("b.txt", "beta")] } });
    await waitFor(() => expect(ipc.uploads).toHaveLength(2));
    expect(new TextDecoder().decode(ipc.uploads[0])).toBe("alpha");
    expect(await screen.findAllByText("Uploaded")).toHaveLength(2);
    expect(await screen.findAllByTestId("artifact-row")).toHaveLength(2);

    ipc.duplicateNext();
    fireEvent.change(input, { target: { files: [file("copy.txt", "alpha")] } });
    expect(await screen.findByText(/Already uploaded/)).toBeVisible();
    expect(screen.getByText(/Not processed again/)).toBeVisible();
  });

  it("refuses oversized, unsupported and too-many files before uploading", async () => {
    const ipc = mockEvidence();
    render(<EvidencePanel assessmentId={ASSESSMENT} />);
    await screen.findByText(/No evidence/);
    const input = screen.getByTestId("evidence-input");

    const many = Array.from({ length: 51 }, (_, i) => file(`${i}.txt`));
    fireEvent.change(input, { target: { files: many } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Select at most 50 files at a time. 51 were selected.");

    fireEvent.change(input, {
      target: { files: [file("huge.pdf", "x", 50 * 1024 * 1024 + 1), file("tool.exe", "MZ")] },
    });
    expect(await screen.findByText(/Larger than the 50 MB limit/)).toBeVisible();
    expect(screen.getByText(/Only PDF, DOCX, TXT and CSV/)).toBeVisible();
    expect(ipc.uploads).toHaveLength(0);
  });

  it("shows an upload error from the service plainly", async () => {
    mockEvidence();
    mockIPC((cmd) => {
      if (cmd === "list_artifacts") {
        return { artifacts: [], total: 0, total_bytes: 0, limit: 200, offset: 0, limits: LIMITS };
      }
      if (cmd === "upload_evidence") {
        throw {
          kind: "api_error",
          status: 415,
          code: "file_type_mismatch",
          message: "The file content does not match its file type.",
          fields: [],
        };
      }
      throw new Error(cmd);
    });
    render(<EvidencePanel assessmentId={ASSESSMENT} />);
    await screen.findByText(/No evidence/);
    fireEvent.change(screen.getByTestId("evidence-input"), { target: { files: [file("fake.pdf")] } });
    expect(await screen.findByText(/does not match its file type/)).toBeVisible();
  });

  it("polls while files are being processed and stops after", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const ipc = mockEvidence([artifact(1, "policy.pdf", "parsing")]);
    render(<EvidencePanel assessmentId={ASSESSMENT} />);
    expect(await screen.findByText("Extracting text")).toBeVisible();
    ipc.artifacts[0] = { ...ipc.artifacts[0], parse_status: "parsed", section_count: 2 };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    expect(await screen.findByText("Text extracted")).toBeVisible();
    const listCalls = ipc.calls.filter((c) => c.cmd === "list_artifacts").length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(ipc.calls.filter((c) => c.cmd === "list_artifacts").length).toBe(listCalls);
  });

  it("renders extracted text as inert text", async () => {
    mockEvidence([artifact(1, "<b>policy</b>.pdf", "parsed")]);
    const user = userEvent.setup();
    const { container } = render(<EvidencePanel assessmentId={ASSESSMENT} />);
    expect(await screen.findByText("<b>policy</b>.pdf")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "View text" }));
    expect(await screen.findByText("<img src=x onerror=alert(1)> policy text")).toBeVisible();
    expect(screen.getByText("page 1")).toBeVisible();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });
});

describe("evidence wording", () => {
  it("never shows an unknown detail code raw", () => {
    expect(describeDetail("failed", "some_new_code")).toBe("The file could not be processed.");
    expect(describeDetail("parsed", null)).toBeNull();
  });

  it("accepts only the supported extensions", () => {
    expect(hasAcceptedExtension("Policy.PDF")).toBe(true);
    expect(hasAcceptedExtension(".pdf")).toBe(false);
    expect(hasAcceptedExtension("x.pdf.exe")).toBe(false);
  });
});
