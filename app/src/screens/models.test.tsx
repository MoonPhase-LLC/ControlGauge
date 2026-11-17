import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import { PROVENANCE_WARNING } from "@/screens/ModelManagementScreen";
import { HEALTHY, apiError, mockServiceIpc, settle, type ServiceIpcMock } from "@/test/serviceIpc";
import type { ModelFile } from "@/service/types";

// Model import/start/stop/health over Tauri's official IPC mock, through the
// real App (navigation, focus, service gating). The native file dialog is
// mocked (its Rust plugin is exercised on the real app, not in jsdom); DOM
// structure and content only -- the real Rust/Python path is covered by
// `cargo test` and pytest.

const openDialog = vi.hoisted(() => vi.fn<() => Promise<string | null>>());
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: openDialog }));

const MODEL_ID = "00000000-0000-4000-8000-000000000001";

beforeEach(() => {
  vi.useFakeTimers();
  openDialog.mockReset();
  // Cancelling the dialog is the default: no path, no import.
  openDialog.mockResolvedValue(null);
});

async function openModels(
  seed?: (ipc: ServiceIpcMock) => void,
  initial: Parameters<typeof mockServiceIpc>[0] = { state: "ready" },
) {
  const ipc = mockServiceIpc(initial);
  seed?.(ipc);
  const user = userEvent.setup({ delay: null });
  render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
  await settle();
  await settle(() => ipc.health.forEach((r) => r.resolve(HEALTHY)));
  const nav = screen.getByRole("navigation", { name: "Main" });
  await user.click(within(nav).getByRole("button", { name: "Models" }));
  await settle();
  return { ipc, user };
}

function seedModel(ipc: ServiceIpcMock, overrides: Partial<ModelFile> = {}): ModelFile {
  const model: ModelFile = {
    id: MODEL_ID,
    role: "generation",
    display_name: "qwen2.5-7b-instruct.gguf",
    sha256: "a".repeat(64),
    size_bytes: 3_993_201_344,
    imported_at: "2026-11-02T12:00:00Z",
    last_verified_at: null,
    embedding_dimensions: null,
    embedding_normalization: null,
    embedding_similarity_metric: null,
    ...overrides,
  };
  ipc.models.unshift(model);
  return model;
}

describe("model management", () => {
  it("renders the import form and an empty state, calling only list_models", async () => {
    const { ipc } = await openModels();
    expect(screen.getByRole("heading", { level: 1, name: "Models" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Role" })).toHaveValue("generation");
    expect(screen.getByRole("button", { name: "Choose GGUF file…" })).toBeVisible();
    expect(screen.getByText("No models imported")).toBeVisible();
    // StrictMode double-runs the first query; nothing else is ever called.
    expect(ipc.calls.data.some((c) => c.cmd !== "list_models")).toBe(false);
  });

  it("opens the dialog and imports the picked file for the chosen role", async () => {
    const { ipc, user } = await openModels();
    await user.selectOptions(screen.getByRole("combobox", { name: "Role" }), "embedding");
    openDialog.mockResolvedValue("/home/user/models/nomic-embed.gguf");
    await user.click(screen.getByRole("button", { name: "Choose GGUF file…" }));
    await settle();
    await settle();

    expect(openDialog).toHaveBeenCalledWith({
      multiple: false,
      filters: [{ name: "GGUF model", extensions: ["gguf"] }],
    });
    expect(ipc.calls.data).toContainEqual({
      cmd: "import_model",
      args: { path: "/home/user/models/nomic-embed.gguf", role: "embedding" },
    });
    const list = screen.getByRole("list", { name: "Imported models" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    expect(within(list).getByText("nomic-embed.gguf")).toBeVisible();
  });

  it("renders the fixed provenance warning on every imported model entry", async () => {
    await openModels((mock) => {
      seedModel(mock);
      seedModel(mock, {
        id: "00000000-0000-4000-8000-000000000002",
        role: "embedding",
        display_name: "nomic-embed.gguf",
        size_bytes: 67_108_864,
        embedding_dimensions: 768,
      });
    });
    const notes = screen.getAllByRole("note");
    expect(notes).toHaveLength(2);
    for (const note of notes) {
      expect(note).toHaveTextContent(PROVENANCE_WARNING);
    }
    expect(screen.getByText(/768 dimensions/)).toBeVisible();
  });

  it("offers Start for a stopped model and Stop once it is the role's occupant", async () => {
    const { ipc, user } = await openModels((mock) => {
      seedModel(mock);
    });
    const item = within(screen.getByRole("list", { name: "Imported models" })).getByRole("listitem");

    // Not running: Start + health check, no Stop.
    expect(within(item).getByRole("button", { name: "Start" })).toBeVisible();
    expect(within(item).getByRole("button", { name: "Run health check" })).toBeVisible();
    expect(within(item).queryByRole("button", { name: "Stop" })).toBeNull();

    await user.click(within(item).getByRole("button", { name: "Start" }));
    await settle();
    await settle();

    expect(ipc.calls.data).toContainEqual({ cmd: "start_model", args: { id: MODEL_ID } });
    expect(within(item).getByRole("button", { name: "Stop" })).toBeVisible();
    expect(within(item).queryByRole("button", { name: "Start" })).toBeNull();
    expect(within(item).getByText("ready")).toBeVisible();

    await user.click(within(item).getByRole("button", { name: "Stop" }));
    await settle();
    await settle();

    expect(ipc.calls.data).toContainEqual({ cmd: "stop_model", args: { id: MODEL_ID } });
    expect(within(item).getByRole("button", { name: "Start" })).toBeVisible();
    expect(within(item).getByText("stopped")).toBeVisible();
  });

  it("shows an unhealthy health-check result with its detail code, not as an error", async () => {
    const { user } = await openModels((mock) => {
      const model = seedModel(mock);
      mock.runtime.generation = {
        state: "ready",
        detail_code: null,
        checked_at: "2026-11-02T12:05:00Z",
        model_id: model.id,
      };
      mock.setUnhealthyHealthNext("request_timeout");
    });
    const item = within(screen.getByRole("list", { name: "Imported models" })).getByRole("listitem");

    await user.click(within(item).getByRole("button", { name: "Run health check" }));
    await settle();
    await settle();

    const status = within(item).getByRole("status");
    expect(status).toHaveTextContent("Last health check: unhealthy (request_timeout)");
    // An unhealthy answer is a resolved status, not a rejection.
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("waits for the service and makes no data call before it is ready", async () => {
    const { ipc } = await openModels(undefined, { state: "awaiting_endpoint" });
    expect(screen.getByText("Waiting for the local service")).toBeVisible();
    expect(screen.getByText("Models will appear as soon as the local service has started.")).toBeVisible();
    expect(ipc.calls.data).toEqual([]);
  });

  it("shows a load error with a working retry", async () => {
    const ipc = mockServiceIpc();
    ipc.failNext("list_models", { kind: "request_failed" });
    const user = userEvent.setup({ delay: null });
    // No StrictMode here (pattern: assessments.test.tsx): the double effect
    // run would let the second list_models succeed past failNext.
    render(<App />);
    await settle();
    await settle(() => ipc.health.forEach((r) => r.resolve(HEALTHY)));
    await user.click(
      within(screen.getByRole("navigation", { name: "Main" })).getByRole("button", { name: "Models" }),
    );
    await settle();

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Models could not be loaded");
    expect(alert).toHaveTextContent("The local service stopped responding");
    expect(alert).toHaveTextContent("request_failed");

    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    await settle();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("No models imported")).toBeVisible();
    expect(ipc.models).toEqual([]);
  });

  it("surfaces a fixed import error code and returns to the import form", async () => {
    const { ipc, user } = await openModels();
    openDialog.mockResolvedValue("/home/user/models/not-a-model.gguf");
    ipc.failNext("import_model", apiError(415, "not_gguf", "The file is not a GGUF model."));

    await user.click(screen.getByRole("button", { name: "Choose GGUF file…" }));
    await settle();
    await settle();

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The model could not be imported");
    expect(alert).toHaveTextContent("not_gguf");
    // Nothing was imported and the form is usable again.
    expect(screen.getByText("No models imported")).toBeVisible();
    expect(screen.getByRole("button", { name: "Choose GGUF file…" })).toBeEnabled();
    expect(ipc.models).toEqual([]);
  });
});
