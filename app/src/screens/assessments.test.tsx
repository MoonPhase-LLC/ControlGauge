import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import { FRAMEWORK, HEALTHY, advance, apiError, mockServiceIpc, settle, type ServiceIpcMock } from "@/test/serviceIpc";

// Assessment create/list/view over Tauri's official IPC mock, through the
// real App (navigation, focus, service gating). DOM structure and content
// only; the real Rust/Python path is covered by `cargo test` and pytest.

beforeEach(() => {
  vi.useFakeTimers();
});

async function openAssessments(initial: Parameters<typeof mockServiceIpc>[0] = { state: "ready" }) {
  const ipc = mockServiceIpc(initial);
  const user = userEvent.setup({ delay: null });
  render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
  await settle();
  await settle(() => ipc.health.forEach((r) => r.resolve(HEALTHY)));
  const nav = screen.getByRole("navigation", { name: "Main" });
  await user.click(within(nav).getByRole("button", { name: "Assessments" }));
  await settle();
  return { ipc, user };
}

function seed(ipc: ServiceIpcMock, count: number) {
  for (let i = 1; i <= count; i++) {
    ipc.assessments.unshift({
      id: `11111111-1111-4111-8111-${String(i).padStart(12, "0")}`,
      name: `Seeded ${i}`,
      description: i === 1 ? "First seeded assessment" : null,
      framework: { id: FRAMEWORK.id, name: FRAMEWORK.name, version: FRAMEWORK.version },
      created_at: "2026-11-01T09:30:00Z",
    });
  }
}

describe("assessment list", () => {
  it("shows an empty state with a create action", async () => {
    await openAssessments();
    expect(screen.getByText("No assessments yet")).toBeVisible();
    expect(screen.getByRole("button", { name: "New assessment" })).toBeVisible();
  });

  it("lists stored assessments newest first with framework and date", async () => {
    const ipc = mockServiceIpc();
    seed(ipc, 3);
    const user = userEvent.setup({ delay: null });
    render(<App />);
    await settle();
    await user.click(screen.getByRole("button", { name: "Go to assessments" }));
    await settle();

    const items = within(screen.getByRole("list", { name: "Assessment list" })).getAllByRole("listitem");
    expect(items.map((li) => within(li).getByRole("button").textContent)).toEqual([
      expect.stringContaining("Seeded 3"),
      expect.stringContaining("Seeded 2"),
      expect.stringContaining("Seeded 1"),
    ]);
    expect(items[2]).toHaveTextContent("Test Framework Revision 1");
    expect(items[2]).toHaveTextContent("First seeded assessment");
    expect(screen.getByText("Showing 3 of 3")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
  });

  it("pages with Show more", async () => {
    const ipc = mockServiceIpc();
    seed(ipc, 55);
    const user = userEvent.setup({ delay: null });
    render(<App />);
    await settle();
    await user.click(screen.getByRole("button", { name: "Go to assessments" }));
    await settle();

    expect(screen.getByText("Showing 50 of 55")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Show more" }));
    await settle();
    expect(screen.getByText("Showing 55 of 55")).toBeVisible();
    expect(ipc.calls.data.filter((c) => c.cmd === "list_assessments").map((c) => c.args)).toEqual([
      { limit: 50, offset: 0 },
      { limit: 50, offset: 50 },
    ]);
  });

  it("shows a load error with a working retry", async () => {
    const ipc = mockServiceIpc();
    ipc.failNext("list_assessments", { kind: "request_failed" });
    const user = userEvent.setup({ delay: null });
    render(<App />);
    await settle();
    await user.click(screen.getByRole("button", { name: "Go to assessments" }));
    await settle();

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Assessments could not be loaded");
    expect(alert).toHaveTextContent("The local service stopped responding");
    expect(alert).toHaveTextContent("request_failed");

    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    await settle();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("No assessments yet")).toBeVisible();
  });

  it("waits for the service and makes no data call before it is ready", async () => {
    const { ipc } = await openAssessments({ state: "awaiting_endpoint" });
    expect(screen.getByText("Waiting for the local service")).toBeVisible();
    expect(ipc.calls.data).toEqual([]);

    ipc.setStatus({ state: "ready" });
    await advance(250);
    await settle();
    expect(screen.getByText("No assessments yet")).toBeVisible();
  });

  it("explains a failed service instead of showing stale data", async () => {
    const { ipc } = await openAssessments();
    ipc.setStatus({ state: "failed", reason: "authenticated_connection_lost" });
    await advance(250);
    expect(screen.getByText("The local service is unavailable")).toBeVisible();
    expect(screen.queryByText("No assessments yet")).toBeNull();
  });
});

describe("creating an assessment", () => {
  it("creates, then shows the new assessment's details", async () => {
    const { ipc, user } = await openAssessments();
    await user.click(screen.getByRole("button", { name: "New assessment" }));
    await settle();
    expect(screen.getByRole("heading", { level: 1, name: "New assessment" })).toHaveFocus();

    const framework = screen.getByRole("combobox", { name: "Framework" });
    expect(framework).toHaveValue(FRAMEWORK.id);
    expect(screen.getByText(/control catalog is not loaded yet/)).toBeVisible();

    await user.type(screen.getByRole("textbox", { name: /Name/ }), "  FY26 annual  ");
    await user.type(screen.getByRole("textbox", { name: "Description" }), "HQ systems");
    await user.click(screen.getByRole("button", { name: "Create assessment" }));
    await settle();
    await settle();

    expect(ipc.calls.data.find((c) => c.cmd === "create_assessment")?.args).toEqual({
      input: { name: "FY26 annual", description: "HQ systems", frameworkId: FRAMEWORK.id },
    });
    const heading = screen.getByRole("heading", { level: 1, name: "FY26 annual" });
    expect(heading).toHaveFocus();
    expect(screen.getByText("HQ systems")).toBeVisible();
    expect(screen.getByText(ipc.assessments[0].id)).toBeVisible();
    expect(screen.getByText(/Uploading evidence and reviewing suggested control mappings are not available yet/)).toBeVisible();

    // Back to the list: the new assessment is there.
    await user.click(screen.getByRole("button", { name: "Back to assessments" }));
    await settle();
    expect(screen.getByText("Showing 1 of 1")).toBeVisible();
  });

  it("validates the name locally and focuses the field, without calling the service", async () => {
    const { ipc, user } = await openAssessments();
    await user.click(screen.getByRole("button", { name: "New assessment" }));
    await settle();

    await user.type(screen.getByRole("textbox", { name: /Name/ }), "   ");
    await user.click(screen.getByRole("button", { name: "Create assessment" }));

    const name = screen.getByRole("textbox", { name: /Name/ });
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name).toHaveFocus();
    expect(name).toHaveAccessibleDescription(/Enter a name for the assessment\./);
    expect(ipc.calls.data.some((c) => c.cmd === "create_assessment")).toBe(false);
  });

  it("shows service field errors on the matching field", async () => {
    const { ipc, user } = await openAssessments();
    await user.click(screen.getByRole("button", { name: "New assessment" }));
    await settle();
    ipc.failNext(
      "create_assessment",
      apiError(422, "validation_failed", "The request is not valid.", [
        { field: "description", code: "invalid_characters", message: "This value contains characters that are not allowed." },
      ]),
    );

    await user.type(screen.getByRole("textbox", { name: /Name/ }), "Valid name");
    await user.type(screen.getByRole("textbox", { name: "Description" }), "x");
    await user.click(screen.getByRole("button", { name: "Create assessment" }));
    await settle();

    const description = screen.getByRole("textbox", { name: "Description" });
    expect(description).toHaveAttribute("aria-invalid", "true");
    expect(description).toHaveFocus();
    expect(description).toHaveAccessibleDescription(/characters that are not allowed/);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: "New assessment" })).toBeVisible();
  });

  it("shows a non-field failure as an alert and keeps the input", async () => {
    const { ipc, user } = await openAssessments();
    await user.click(screen.getByRole("button", { name: "New assessment" }));
    await settle();
    ipc.failNext("create_assessment", { kind: "service_no_longer_alive" });

    await user.type(screen.getByRole("textbox", { name: /Name/ }), "Keep me");
    await user.click(screen.getByRole("button", { name: "Create assessment" }));
    await settle();

    expect(screen.getByRole("alert")).toHaveTextContent("The assessment was not created");
    expect(screen.getByRole("textbox", { name: /Name/ })).toHaveValue("Keep me");
    expect(ipc.assessments).toEqual([]);
  });

  it("ignores repeated submission while a create is pending", async () => {
    const { ipc, user } = await openAssessments();
    await user.click(screen.getByRole("button", { name: "New assessment" }));
    await settle();
    const gate = ipc.hold("create_assessment");

    await user.type(screen.getByRole("textbox", { name: /Name/ }), "Once");
    const submit = screen.getByRole("button", { name: "Create assessment" });
    await user.click(submit);
    expect(screen.getByRole("button", { name: "Creating…" })).toHaveAttribute("aria-disabled", "true");
    await user.click(screen.getByRole("button", { name: "Creating…" }));
    expect(ipc.calls.data.filter((c) => c.cmd === "create_assessment")).toHaveLength(1);

    await settle(() => gate.forEach((g) => g.resolve()));
    await settle();
    expect(ipc.assessments).toHaveLength(1);
  });

  it("cancel returns to the list without creating anything", async () => {
    const { ipc, user } = await openAssessments();
    await user.click(screen.getByRole("button", { name: "New assessment" }));
    await settle();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await settle();
    expect(screen.getByRole("heading", { level: 1, name: "Assessments" })).toHaveFocus();
    expect(ipc.calls.data.some((c) => c.cmd === "create_assessment")).toBe(false);
  });
});

describe("viewing an assessment", () => {
  it("opens from the list", async () => {
    const ipc = mockServiceIpc();
    seed(ipc, 1);
    const user = userEvent.setup({ delay: null });
    render(<App />);
    await settle();
    await user.click(screen.getByRole("button", { name: "Go to assessments" }));
    await settle();
    await user.click(screen.getByRole("button", { name: /Seeded 1/ }));
    await settle();

    expect(screen.getByRole("heading", { level: 1, name: "Seeded 1" })).toHaveFocus();
    expect(screen.getByText("First seeded assessment")).toBeVisible();
    expect(ipc.calls.data[ipc.calls.data.length - 1]).toEqual({ cmd: "get_assessment", args: { id: ipc.assessments[0].id } });
  });

  it("reports a missing assessment plainly", async () => {
    const ipc = mockServiceIpc();
    seed(ipc, 1);
    const user = userEvent.setup({ delay: null });
    render(<App />);
    await settle();
    await user.click(screen.getByRole("button", { name: "Go to assessments" }));
    await settle();
    ipc.failNext("get_assessment", apiError(404, "assessment_not_found", "The assessment was not found."));
    await user.click(screen.getByRole("button", { name: /Seeded 1/ }));
    await settle();

    expect(screen.getByRole("heading", { level: 1, name: "Assessment not found" })).toBeVisible();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("This assessment does not exist");
    expect(within(alert).queryByRole("button", { name: "Try again" })).toBeNull();
  });
});
