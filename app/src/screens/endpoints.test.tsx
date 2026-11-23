import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import { ENDPOINT_SECURITY_NOTE } from "@/components/models/EndpointSection";
import { ENDPOINTS, HEALTHY, mockServiceIpc, settle, type ServiceIpcMock } from "@/test/serviceIpc";

// External endpoint registration and use (S6-07 UI over the S6-05 contract):
// loopback-only client-side validation, attach with the right role, health
// badge states, and the stop-then-attach switch semantics. The service's
// probe/validation behavior is covered by pytest (test_model_endpoints.py).

beforeEach(() => {
  vi.useFakeTimers();
});

async function openEndpoints(seed?: (ipc: ServiceIpcMock) => void) {
  const ipc = mockServiceIpc();
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

function endpointCard() {
  const title = screen.getByText("External endpoints");
  return within(title.closest('[data-slot="card"]') as HTMLElement);
}

function endpointItem(card: ReturnType<typeof endpointCard>, displayName: string) {
  return within(
    card.getByText(displayName).closest("li") as HTMLElement,
  );
}

describe("external endpoints", () => {
  it("renders the register form, security note, and existing endpoints with health badges", async () => {
    const { ipc } = await openEndpoints();

    const card = endpointCard();
    expect(card.getByText(ENDPOINT_SECURITY_NOTE)).toBeVisible();
    expect(card.getByText("Must be a 127.0.0.1/localhost address, for example http://127.0.0.1:8080.")).toBeVisible();

    const checked = endpointItem(card, "Checked endpoint");
    expect(checked.getByText("healthy")).toBeVisible();
    expect(checked.getByText("http://127.0.0.1:8081")).toBeVisible();
    expect(checked.getByRole("button", { name: "Use for generation" })).toBeVisible();
    expect(checked.getByRole("button", { name: "Use for embedding" })).toBeVisible();

    const unchecked = endpointItem(card, "Unchecked endpoint");
    expect(unchecked.getByText("Not checked")).toBeVisible();
    // An embedding-only endpoint offers only that role.
    expect(unchecked.getByRole("button", { name: "Use for embedding" })).toBeVisible();
    expect(unchecked.queryByRole("button", { name: "Use for generation" })).toBeNull();

    expect(ipc.calls.data.some((c) => c.cmd === "register_endpoint")).toBe(false);
  });

  it("registers an endpoint with the declared capabilities", async () => {
    const { ipc, user } = await openEndpoints();
    const card = endpointCard();

    await user.type(card.getByLabelText("Display name"), "Second server");
    await user.type(card.getByLabelText("Base address"), "http://127.0.0.1:9000");
    await user.click(card.getByRole("checkbox", { name: "embedding" }));
    await user.click(card.getByRole("button", { name: "Register endpoint" }));
    await settle();
    await settle();

    expect(ipc.calls.data).toContainEqual({
      cmd: "register_endpoint",
      args: {
        displayName: "Second server",
        backend: "llama-cpp",
        baseUrl: "http://127.0.0.1:9000",
        capabilities: "generation,embedding",
      },
    });
    const created = endpointItem(card, "Second server");
    expect(created.getByText("http://127.0.0.1:9000")).toBeVisible();
    expect(created.getByText("Not checked")).toBeVisible();
  });

  it("blocks a non-loopback address client-side with a fixed message and sends nothing", async () => {
    const { ipc, user } = await openEndpoints();
    const card = endpointCard();

    await user.type(card.getByLabelText("Display name"), "Remote server");
    await user.type(card.getByLabelText("Base address"), "http://10.0.0.5:8080");
    await user.click(card.getByRole("button", { name: "Register endpoint" }));
    await settle();

    expect(
      card.getByText(
        "Endpoint addresses must be local (127.0.0.1 or localhost), for example http://127.0.0.1:8080.",
      ),
    ).toBeVisible();
    expect(ipc.calls.data.some((c) => c.cmd === "register_endpoint")).toBe(false);

    // An https address is equally refused: loopback traffic is plain http.
    await user.clear(card.getByLabelText("Base address"));
    await user.type(card.getByLabelText("Base address"), "https://127.0.0.1:8080");
    await user.click(card.getByRole("button", { name: "Register endpoint" }));
    await settle();
    expect(ipc.calls.data.some((c) => c.cmd === "register_endpoint")).toBe(false);
  });

  it("refuses a blank or malformed address before any request", async () => {
    const { ipc, user } = await openEndpoints();
    const card = endpointCard();

    await user.type(card.getByLabelText("Display name"), "Broken");
    await user.type(card.getByLabelText("Base address"), "not-a-url");
    await user.click(card.getByRole("button", { name: "Register endpoint" }));
    await settle();
    expect(
      card.getByText("Endpoint addresses must be local (127.0.0.1 or localhost), for example http://127.0.0.1:8080."),
    ).toBeVisible();
    expect(ipc.calls.data.some((c) => c.cmd === "register_endpoint")).toBe(false);

    await user.clear(card.getByLabelText("Base address"));
    await user.click(card.getByRole("button", { name: "Register endpoint" }));
    await settle();
    expect(
      card.getByText("Enter the endpoint's full address, including the port."),
    ).toBeVisible();
    expect(ipc.calls.data.some((c) => c.cmd === "register_endpoint")).toBe(false);
  });

  it("attaches with the right role and shows the stop control for the occupant", async () => {
    const { ipc, user } = await openEndpoints();
    const card = endpointCard();
    const unchecked = endpointItem(card, "Unchecked endpoint");

    await user.click(unchecked.getByRole("button", { name: "Use for embedding" }));
    await settle();
    await settle();

    expect(ipc.calls.data).toContainEqual({
      cmd: "attach_endpoint",
      args: { endpointId: ENDPOINTS[1].id, role: "embedding" },
    });
    // The occupant gets the stop control (the stop-then-attach switch).
    expect(endpointItem(card, "Unchecked endpoint").getByRole("button", { name: "Stop embedding" })).toBeVisible();
    expect(endpointItem(card, "Unchecked endpoint").queryByRole("button", { name: "Use for embedding" })).toBeNull();

    await user.click(endpointItem(card, "Unchecked endpoint").getByRole("button", { name: "Stop embedding" }));
    await settle();
    await settle();
    expect(ipc.calls.data).toContainEqual({ cmd: "stop_model", args: { id: ENDPOINTS[1].id } });
    expect(endpointItem(card, "Unchecked endpoint").getByRole("button", { name: "Use for embedding" })).toBeVisible();
  });

  it("runs the health check and updates the badge", async () => {
    const { ipc, user } = await openEndpoints();
    const card = endpointCard();
    const unchecked = endpointItem(card, "Unchecked endpoint");

    await user.click(unchecked.getByRole("button", { name: "Check health" }));
    await settle();
    await settle();

    expect(ipc.calls.data).toContainEqual({
      cmd: "check_endpoint_health",
      args: { endpointId: ENDPOINTS[1].id },
    });
    expect(endpointItem(card, "Unchecked endpoint").getByText("healthy")).toBeVisible();
  });

  it("refuses to remove an endpoint that is attached to a running role (409)", async () => {
    const { user } = await openEndpoints();
    const card = endpointCard();
    const checked = endpointItem(card, "Checked endpoint");

    // Attach, then try to remove while it occupies the role.
    await user.click(checked.getByRole("button", { name: "Use for generation" }));
    await settle();
    await settle();

    await user.click(endpointItem(card, "Checked endpoint").getByRole("button", { name: "Remove" }));
    await settle();

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The endpoint action could not be completed");
    expect(alert).toHaveTextContent("endpoint_attached");
    // The row is still registered.
    expect(endpointCard().getByText("Checked endpoint")).toBeVisible();
  });
});
