import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import { BASELINE_ID, CONTROL_X1, FAMILY_Y_ID, RELEASE_ID } from "@/test/catalogFixture";
import { HEALTHY, apiError, mockServiceIpc, settle } from "@/test/serviceIpc";

// Framework catalog browsing over Tauri's IPC mock, through the real App.
// The real Rust/Python path is covered by `cargo test` and pytest.

beforeEach(() => {
  vi.useFakeTimers();
});

async function openCatalog(
  initial: Parameters<typeof mockServiceIpc>[0] = { state: "ready" },
  { strict = true }: { strict?: boolean } = {},
) {
  const ipc = mockServiceIpc(initial);
  const user = userEvent.setup({ delay: null });
  // StrictMode runs effects twice; tests that make the *next* call fail
  // render without it so the failure reaches the request that is kept.
  render(strict ? (
    <StrictMode>
      <App />
    </StrictMode>
  ) : (
    <App />
  ));
  await settle();
  await settle(() => ipc.health.forEach((r) => r.resolve(HEALTHY)));
  const nav = screen.getByRole("navigation", { name: "Main" });
  await user.click(within(nav).getByRole("button", { name: "Framework catalog" }));
  await settle();
  await settle();
  return { ipc, user };
}

function controlList() {
  return within(screen.getByRole("list", { name: "Controls" }));
}

function lastControlQuery(ipc: ReturnType<typeof mockServiceIpc>) {
  const calls = ipc.calls.data.filter((c) => c.cmd === "list_catalog_controls");
  return calls[calls.length - 1]?.args;
}

describe("catalog screen", () => {
  it("shows the release, its provenance and its controls", async () => {
    const { ipc } = await openCatalog();
    expect(screen.getByRole("heading", { level: 1, name: "Framework catalog" })).toHaveFocus();
    expect(screen.getByRole("heading", { level: 2, name: /Test Framework Revision 1 · Release 2\.0/ })).toBeVisible();
    expect(screen.getByText("Control text: Example Standards Body. Displayed verbatim.")).toBeVisible();
    expect(screen.getByText("a".repeat(64))).toBeInTheDocument();
    expect(screen.getByText(/Core Selection \(2 controls and enhancements\)/)).toBeInTheDocument();

    expect(ipc.calls.data.find((c) => c.cmd === "get_catalog_release")?.args).toEqual({ id: RELEASE_ID });
    expect(lastControlQuery(ipc)).toEqual({
      releaseId: RELEASE_ID,
      query: { familyId: null, baselineId: null, search: null, limit: 100, offset: 0 },
    });
    expect(screen.getByText("Showing 3 of 3")).toBeVisible();
    const items = controlList().getAllByRole("listitem");
    expect(items.map((i) => within(i).getByRole("button").textContent)).toEqual([
      expect.stringContaining("X-1 First Requirement"),
      expect.stringContaining("X-2 Retired Requirement"),
      expect.stringContaining("Y-1 Moved Requirement"),
    ]);
    expect(within(items[1]).getByText("Status in source: withdrawn")).toBeVisible();
    expect(within(items[0]).getByText("2 enhancements")).toBeVisible();
    expect(within(items[0]).getByText("Baselines: core")).toBeVisible();
  });

  it("filters by family, baseline and search text", async () => {
    const { ipc, user } = await openCatalog();

    await user.selectOptions(screen.getByRole("combobox", { name: "Family" }), FAMILY_Y_ID);
    await settle();
    expect(lastControlQuery(ipc)).toMatchObject({ query: { familyId: FAMILY_Y_ID } });
    expect(controlList().getAllByRole("listitem")).toHaveLength(1);
    // A family's own introductory text is official text from the source.
    const region = screen.getByRole("region", { name: "Official text" });
    expect(within(region).getByText("Family Y intro.")).toBeVisible();

    await user.selectOptions(screen.getByRole("combobox", { name: "Family" }), "");
    await user.selectOptions(screen.getByRole("combobox", { name: "Published baseline" }), BASELINE_ID);
    await settle();
    expect(lastControlQuery(ipc)).toMatchObject({ query: { familyId: null, baselineId: BASELINE_ID } });
    expect(screen.getByText("Showing 2 of 2")).toBeVisible();

    await user.selectOptions(screen.getByRole("combobox", { name: "Published baseline" }), "");
    const search = screen.getByRole("searchbox", { name: "Search identifiers and titles" });
    await user.type(search, "automated");
    // Typing alone does not search; submitting does.
    expect(lastControlQuery(ipc)).toMatchObject({ query: { search: null } });
    await user.click(screen.getByRole("button", { name: "Search" }));
    await settle();
    expect(lastControlQuery(ipc)).toMatchObject({ query: { search: "automated" } });
    expect(controlList().getAllByRole("listitem")).toHaveLength(1);

    await user.clear(search);
    await user.type(search, "nothing like this{Enter}");
    await settle();
    expect(screen.getByText("No controls match.")).toBeVisible();
  });

  it("explains a service that is not ready", async () => {
    await openCatalog({ state: "starting" });
    expect(screen.getByText("Waiting for the local service")).toBeVisible();
  });

  it("reports a failed load with a retry", async () => {
    const ipc = mockServiceIpc();
    ipc.failNext("get_catalog_release", { kind: "request_failed" });
    const user = userEvent.setup({ delay: null });
    render(<App />);
    await settle();
    await user.click(within(screen.getByRole("navigation", { name: "Main" })).getByRole("button", { name: "Framework catalog" }));
    await settle();
    expect(screen.getByRole("alert")).toHaveTextContent("The catalog could not be loaded");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await settle();
    expect(screen.getByText("Showing 3 of 3")).toBeVisible();
  });
});

describe("control detail", () => {
  async function openX1() {
    const opened = await openCatalog();
    await opened.user.click(controlList().getByRole("button", { name: /X-1/ }));
    await settle();
    return opened;
  }

  it("renders official text verbatim and inert, clearly labeled", async () => {
    const { ipc } = await openX1();
    expect(ipc.calls.data.find((c) => c.cmd === "get_control")?.args).toEqual({ id: CONTROL_X1 });
    expect(screen.getByRole("heading", { level: 1, name: "First Requirement" })).toHaveFocus();
    expect(screen.getByText("X-1", { selector: "p" })).toBeVisible();

    const [region] = screen.getAllByRole("region", { name: "Official text" });
    expect(within(region).getByText("Control text: Example Standards Body. Displayed verbatim.")).toBeVisible();

    // Parameter placeholders show the parameter's own label or choices.
    const placeholders = region.querySelectorAll("[data-param]");
    expect([...placeholders].map((p) => p.getAttribute("data-param"))).toEqual(["x-1_p1", "x-1_p2", "x-1_p1"]);
    expect(placeholders[0]).toHaveTextContent("Parameter: [review period]");
    expect(placeholders[1]).toHaveTextContent("alpha | beta every Parameter: [review period]");
    // Emphasis and link markup from the source format.
    expect(within(region).getByText("approved").tagName).toBe("EM");
    expect(region).toHaveTextContent("see the guide.");
    expect(region).not.toHaveTextContent("(#res-1)");
    // Markup-like text stays text.
    expect(region.querySelector("img")).toBeNull();
    expect(region).toHaveTextContent("<img src=x onerror=alert(1)>");

    // Primary parts start open; others (e.g. assessment material) start closed.
    const sections = region.querySelectorAll("details");
    expect([...sections].map((d) => [d.querySelector("summary")?.textContent, d.open])).toEqual([
      ["Statement", true],
      ["Guidance", true],
      ["EXAMINE", false],
    ]);
  });

  it("lists parameters, links, references and enhancements", async () => {
    await openX1();
    const params = within(screen.getByRole("heading", { name: "Parameters" }).closest("section")!);
    const firstParam = params.getByText("x-1_p1").closest("dt")!;
    expect(firstParam).toHaveTextContent("x-1_p1review period");
    expect(params.getByText("A period is defined;")).toBeVisible();
    expect(params.getByText("Choices (one or more):")).toBeVisible();

    const links = within(screen.getByRole("heading", { name: "Links in the source" }).closest("section")!);
    expect(links.getByRole("heading", { name: "Related" })).toBeVisible();
    expect(links.getByRole("button", { name: "X-2 Retired Requirement" })).toBeVisible();
    expect(links.getByText("Example Guide")).toBeVisible();
    expect(links.getByText("https://example.invalid/guide")).toBeVisible();
    // A reference URL is shown as text, not a link out of the app.
    expect(links.queryByRole("link")).toBeNull();

    const enhancements = within(screen.getByRole("heading", { name: "Enhancements (2)" }).closest("section")!);
    expect(enhancements.getByRole("heading", { name: "X-1(1) Automated Review" })).toBeInTheDocument();
    expect(enhancements.getByText("Status in source: withdrawn")).toBeInTheDocument();
    expect(enhancements.getByRole("button", { name: "X-1 First Requirement, item a." })).toBeInTheDocument();
  });

  it("follows links to other controls and returns to the filtered catalog", async () => {
    const { user, ipc } = await openCatalog();
    await user.selectOptions(screen.getByRole("combobox", { name: "Published baseline" }), BASELINE_ID);
    await settle();
    await user.click(controlList().getByRole("button", { name: /X-1/ }));
    await settle();

    await user.click(screen.getByRole("button", { name: "X-2 Retired Requirement" }));
    await settle();
    expect(screen.getByRole("heading", { level: 1, name: "Retired Requirement" })).toHaveFocus();
    expect(screen.getByText("Status in source: withdrawn")).toBeVisible();
    expect(screen.getByText("The source has no text for this control.")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Moved to" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Back to catalog" }));
    await settle();
    expect(screen.getByRole("combobox", { name: "Published baseline" })).toHaveValue(BASELINE_ID);
    expect(lastControlQuery(ipc)).toMatchObject({ query: { baselineId: BASELINE_ID } });
  });

  it("handles a control that does not exist", async () => {
    const { ipc, user } = await openCatalog({ state: "ready" }, { strict: false });
    ipc.failNext("get_control", apiError(404, "control_not_found", "The control was not found."));
    await user.click(controlList().getByRole("button", { name: /Y-1/ }));
    await settle();
    expect(screen.getByRole("heading", { level: 1, name: "Control not found" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});

describe("assessments and catalog releases", () => {
  it("shows the release an assessment is pinned to", async () => {
    const ipc = mockServiceIpc();
    const user = userEvent.setup({ delay: null });
    render(<App />);
    await settle();
    await user.click(screen.getByRole("button", { name: "Go to assessments" }));
    await settle();
    await user.click(screen.getByRole("button", { name: "New assessment" }));
    await settle();
    await user.type(screen.getByRole("textbox", { name: /Name/ }), "Pinned");
    await user.click(screen.getByRole("button", { name: "Create assessment" }));
    await settle();
    await settle();
    expect(ipc.assessments[0].catalog_release?.id).toBe(RELEASE_ID);
    expect(screen.getByText("Release 2.0")).toBeVisible();
    expect(screen.getByText(/stays on this release/)).toBeVisible();
  });
});
