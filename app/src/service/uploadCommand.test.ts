import { afterEach, describe, expect, it, vi } from "vitest";

// `uploadEvidence` sends the bytes as a raw IPC body and the display name
// percent-encoded in a header (Rust decodes and validates both). Tauri's
// IPC mock drops invoke options, so `invoke` itself is mocked here.

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...args: unknown[]) => invoke(...args) }));

afterEach(() => invoke.mockReset());

describe("uploadEvidence", () => {
  it("sends raw bytes and an encoded name header, never a path", async () => {
    const { uploadEvidence } = await import("./commands");
    invoke.mockResolvedValue({ artifact: {}, duplicate: false });
    const file = new File(["héllo"], "Política de acceso v2.txt");
    await uploadEvidence("11111111-1111-4111-8111-000000000001", file);

    const [cmd, body, options] = invoke.mock.calls[0];
    expect(cmd).toBe("upload_evidence");
    expect(body).toBeInstanceOf(Uint8Array);
    expect(new TextDecoder().decode(body as Uint8Array)).toBe("héllo");
    expect(options).toEqual({
      headers: {
        "x-assessment-id": "11111111-1111-4111-8111-000000000001",
        "x-filename": "Pol%C3%ADtica%20de%20acceso%20v2.txt",
      },
    });
  });
});
