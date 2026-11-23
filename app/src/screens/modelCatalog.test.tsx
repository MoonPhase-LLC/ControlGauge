import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import {
  CATALOG,
  HEALTHY,
  RECOMMENDATIONS,
  apiError,
  advance,
  mockServiceIpc,
  settle,
  type ServiceIpcMock,
} from "@/test/serviceIpc";
import type { RecommendationReport } from "@/service/types";

// The curated catalog screen over Tauri's official IPC mock, through the real
// App (navigation, focus, service gating). Tier grouping, recommendation
// badges, and the full download approval flow are frontend behavior; the
// catalog manifest itself is validated by pytest, the transfer by the
// service's download tests.

beforeEach(() => {
  vi.useFakeTimers();
});

async function openCatalog(
  seed?: (ipc: ServiceIpcMock) => void,
  {
    strictMode = true,
    initial,
  }: { strictMode?: boolean; initial?: Parameters<typeof mockServiceIpc>[0] } = {},
) {
  const ipc = mockServiceIpc(initial);
  seed?.(ipc);
  const user = userEvent.setup({ delay: null });
  render(
    strictMode ? (
      <StrictMode>
        <App />
      </StrictMode>
    ) : (
      <App />
    ),
  );
  await settle();
  await settle(() => ipc.health.forEach((r) => r.resolve(HEALTHY)));
  const nav = screen.getByRole("navigation", { name: "Main" });
  await user.click(within(nav).getByRole("button", { name: "Model catalog" }));
  await settle();
  return { ipc, user };
}

/** Verdicts for every entry replaced by `patch`, keeping the fixture shape. */
function reportWith(patch: (report: RecommendationReport) => RecommendationReport): RecommendationReport {
  return patch({
    hardware_source: "detected",
    entries: RECOMMENDATIONS.entries.map((e) => ({ ...e, estimated: { ...e.estimated } })),
    pairs: RECOMMENDATIONS.pairs.map((p) => ({ ...p })),
  });
}

function tierSection(name: string) {
  return within(screen.getByRole("region", { name: name }));
}

describe("model catalog screen", () => {
  it("renders every tier section with sizes, license, revision, and installed state", async () => {
    await openCatalog();

    expect(screen.getByRole("heading", { level: 1, name: "Model catalog" })).toBeVisible();
    // The fixed network-capable disclosure (ARCHITECTURE §11); the sentence
    // sits inside the intro paragraph, so match its distinctive fragment.
    expect(screen.getByText(/Downloading a model is the only step that uses the network/)).toBeVisible();

    for (const heading of ["Fast", "Balanced", "Accurate", "Embedding"]) {
      expect(screen.getByRole("heading", { level: 2, name: heading })).toBeVisible();
    }
    const fast = tierSection("Fast");
    expect(fast.getByText("Test Gen Small")).toBeVisible();
    expect(fast.getByText(/1.0 GB/)).toBeVisible();
    expect(fast.getByText(/apache-2\.0/)).toBeVisible();
    // Short sha display: 12 characters plus the ellipsis.
    expect(fast.getByText(`${"a".repeat(12)}…`)).toBeVisible();

    const embedding = tierSection("Embedding");
    expect(embedding.getByText("Installed")).toBeVisible();
    expect(embedding.getByText(/768 dimensions/)).toBeVisible();

    // The accurate tier's honest evaluation-pending note (S6-03 manifest).
    const accurate = tierSection("Accurate");
    expect(
      accurate.getByText(/Quality evaluation for this model is still pending/),
    ).toBeVisible();
  });

  it("maps recommendation verdicts onto badges, including the honest unknown", async () => {
    await openCatalog((mock) => {
      mock.setRecommendations(
        reportWith((report) => ({
          ...report,
          entries: report.entries.map((e) =>
            e.model_id === "catalog-gen-small"
              ? { ...e, verdict: "feasible" as const, reasons: ["kv_cache_pressure"] }
              : e.model_id === "catalog-gen-medium"
                ? { ...e, verdict: "infeasible" as const, reasons: ["insufficient_ram"] }
                : e.model_id === "catalog-gen-large"
                  ? { ...e, verdict: "unknown" as const, reasons: ["hardware_unknown"] }
                  : e,
          ),
        })),
      );
    });

    const fast = tierSection("Fast");
    expect(fast.getByText("Feasible")).toBeVisible();
    const balanced = tierSection("Balanced");
    expect(balanced.getByText("Not feasible")).toBeVisible();
    const accurate = tierSection("Accurate");
    expect(accurate.getByText("Needs hardware info")).toBeVisible();
    // The embedding entry keeps its recommended verdict.
    expect(tierSection("Embedding").getByText("Recommended")).toBeVisible();
  });

  it("degrades to a note, not an error, when hardware has not been detected yet", async () => {
    const { ipc } = await openCatalog(
      (mock) => {
        mock.failNext("get_recommendations", apiError(503, "hardware_unavailable", "Hardware has not been detected yet."));
      },
      // Pattern: failNext + StrictMode double effect lets the second call
      // succeed past the failure.
      { strictMode: false },
    );
    expect(ipc.calls.data.filter((c) => c.cmd === "get_recommendations")).toHaveLength(1);
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent(/Recommendations are unavailable/);
    // The catalog itself still renders.
    expect(screen.getByRole("heading", { level: 2, name: "Fast" })).toBeVisible();
  });

  it("shows the approval details and does not start the download until confirmed", async () => {
    const { ipc, user } = await openCatalog();

    const fast = tierSection("Fast");
    await user.click(fast.getByRole("button", { name: "Download…" }));
    await settle();

    const dialog = screen.getByText("Review download: Test Gen Small").closest('[data-slot="card"]') as HTMLElement;
    const scoped = within(dialog);
    // The repository URL is manifest data rendered as inert text (T-21) -- never a link.
    expect(scoped.getByText(CATALOG.entries[0].repository_url).closest("a")).toBeNull();
    expect(scoped.getByText("a".repeat(64))).toBeVisible(); // full revision
    expect(scoped.getByText("catalog-gen-small-q4_k_m.gguf")).toBeVisible();
    expect(scoped.getByText("1".repeat(64))).toBeVisible(); // per-file SHA-256

    // Cancel: nothing is sent.
    await user.click(scoped.getByRole("button", { name: "Cancel" }));
    await settle();
    expect(screen.queryByText(/Review download/)).toBeNull();
    expect(ipc.calls.data.some((c) => c.cmd === "start_catalog_download")).toBe(false);

    // Confirm: exactly the approval POST, then live progress.
    await user.click(fast.getByRole("button", { name: "Download…" }));
    await settle();
    await user.click(screen.getByRole("button", { name: "Download" }));
    await settle();
    await settle();

    expect(ipc.calls.data.filter((c) => c.cmd === "start_catalog_download")).toEqual([
      { cmd: "start_catalog_download", args: { modelId: "catalog-gen-small" } },
    ]);
    expect(screen.getByText("Downloading Test Gen Small")).toBeVisible();
    // Per-file bytes appear with the first poll tick (750 ms).
    await advance(750);
    expect(screen.getByText("catalog-gen-small-q4_k_m.gguf")).toBeVisible();
  });

  it("polls the download every 750 ms and stops on the terminal state", async () => {
    const { ipc, user } = await openCatalog();

    const fast = tierSection("Fast");
    await user.click(fast.getByRole("button", { name: "Download…" }));
    await settle();
    await user.click(screen.getByRole("button", { name: "Download" }));
    await settle();
    await settle();

    const statusCalls = () => ipc.calls.data.filter((c) => c.cmd === "get_download_status").length;
    const before = statusCalls();

    await advance(750);
    const afterOne = statusCalls();
    expect(afterOne).toBeGreaterThan(before);

    await advance(750);
    expect(statusCalls()).toBeGreaterThan(afterOne);

    // Terminal: the poller stops.
    ipc.downloads[0].state = "completed";
    await advance(750);
    expect(screen.getByText("Download completed and verified.")).toBeVisible();
    const atTerminal = statusCalls();
    await advance(750 * 3);
    expect(statusCalls()).toBe(atTerminal);
  });

  it("renders a failed download as the fixed-code error with a working retry", async () => {
    const { ipc, user } = await openCatalog();

    const fast = tierSection("Fast");
    await user.click(fast.getByRole("button", { name: "Download…" }));
    await settle();
    await user.click(screen.getByRole("button", { name: "Download" }));
    await settle();
    await settle();

    ipc.downloads[0].state = "failed";
    ipc.downloads[0].error_code = "checksum_mismatch";
    await advance(750);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The download failed");
    expect(alert).toHaveTextContent("checksum_mismatch");
    // The failed download is terminal: no further polling.
    const callsAtFailure = ipc.calls.data.filter((c) => c.cmd === "get_download_status").length;
    await advance(750 * 2);
    expect(ipc.calls.data.filter((c) => c.cmd === "get_download_status").length).toBe(callsAtFailure);

    // Retry re-fires the (already approved) download.
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    await settle();
    await settle();
    expect(ipc.calls.data.filter((c) => c.cmd === "start_catalog_download")).toHaveLength(2);
    expect(screen.getByText("Downloading Test Gen Small")).toBeVisible();
  });

  it("waits for the service and makes no data call before it is ready", async () => {
    const { ipc } = await openCatalog(undefined, { initial: { state: "awaiting_endpoint" } });
    expect(screen.getByText("Waiting for the local service")).toBeVisible();
    expect(
      screen.getByText("The catalog appears as soon as the local service has started."),
    ).toBeVisible();
    expect(ipc.calls.data).toEqual([]);
  });
});
