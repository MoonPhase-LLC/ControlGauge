import { expect, test } from "@playwright/test";

// Browser smoke test: the real frontend (real `src/main.tsx`, real CSS, the
// Vite dev server's real development CSP) in Chromium, with Tauri's official
// IPC mock standing in for Rust. It does NOT launch Tauri, the Python
// service, or supervision -- see `e2e-native/` for that.

test("app shell renders with mocked service status and health", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") problems.push(`console: ${msg.text()}`);
  });
  page.on("pageerror", (err) => problems.push(`pageerror: ${err.message}`));

  await page.goto("/e2e/smoke.html");

  // Shell structure.
  await expect(page.getByRole("heading", { level: 1, name: "Welcome to ControlGauge" })).toBeVisible();
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav).toBeVisible();
  // Exact accessible name as computed by Chromium (jsdom cannot compute it).
  const settings = nav.getByRole("button", { name: "Settings Not available yet", exact: true });
  await expect(settings).toBeVisible();
  await expect(settings).toHaveAttribute("aria-disabled", "true");
  await expect(nav.getByRole("button", { name: "Home", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(page.getByText("Create and review assessments")).toBeVisible();

  // Mocked service: ready, with an authenticated health result.
  const service = page.getByRole("region", { name: "Local service" });
  await expect(service.getByText("Service ready")).toBeVisible();
  await expect(service.getByText("controlgauge-service", { exact: true })).toBeVisible();
  await expect(service.getByText("0.1.0", { exact: true })).toBeVisible();

  // The supervisor reports a failure: wording changes, health is cleared.
  await page.evaluate(() =>
    (window as unknown as { __e2e: { setStatus(s: unknown): void } }).__e2e.setStatus({
      state: "failed",
      reason: "authenticated_connection_lost",
    }),
  );
  await expect(service.getByText("Service unavailable")).toBeVisible();
  await expect(service.getByText("authenticated_connection_lost")).toBeVisible();
  await expect(service.getByText("controlgauge-service", { exact: true })).toHaveCount(0);

  // Only the two status commands were used on this screen, and the page
  // logged no errors (including CSP violations).
  const calls = await page.evaluate(() => (window as unknown as { __e2e: { calls: string[] } }).__e2e.calls);
  expect(new Set(calls)).toEqual(new Set(["get_service_status", "check_service_health"]));
  expect(problems).toEqual([]);
});

test("assessment create, view and list with mocked service commands", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") problems.push(`console: ${msg.text()}`);
  });
  page.on("pageerror", (err) => problems.push(`pageerror: ${err.message}`));

  await page.goto("/e2e/smoke.html");
  const nav = page.getByRole("navigation", { name: "Main" });
  await nav.getByRole("button", { name: "Assessments", exact: true }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Assessments" })).toBeFocused();
  await expect(page.getByText("No assessments yet")).toBeVisible();
  await page.getByRole("button", { name: "New assessment" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "New assessment" })).toBeFocused();
  await expect(page.getByRole("combobox", { name: "Framework" })).toHaveValue("fw-smoke");

  // Empty name: blocked locally, field marked and focused.
  await page.getByRole("button", { name: "Create assessment" }).click();
  // The asterisk is aria-hidden; required-ness comes from `required`.
  const name = page.getByRole("textbox", { name: "Name", exact: true });
  await expect(name).toHaveAttribute("required", "");
  await expect(name).toHaveAttribute("aria-invalid", "true");
  await expect(name).toBeFocused();

  await name.fill("Browser smoke assessment");
  await page.getByRole("textbox", { name: "Description" }).fill("Created by the browser smoke test");
  await page.getByRole("button", { name: "Create assessment" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Browser smoke assessment" })).toBeFocused();
  await expect(page.getByText("Created by the browser smoke test")).toBeVisible();

  await page.getByRole("button", { name: "Back to assessments" }).click();
  const list = page.getByRole("list", { name: "Assessment list" });
  await expect(list.getByRole("button", { name: /Browser smoke assessment/ })).toBeVisible();
  await expect(page.getByText("Showing 1 of 1")).toBeVisible();

  expect(problems).toEqual([]);
});
