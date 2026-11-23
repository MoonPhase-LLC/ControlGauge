import { expect, test } from "@playwright/test";

// S6-07 browser smoke: the model catalog screen (tier sections, honest
// badges, network-capable disclosure) and the download approval flow, in
// Chromium against the mocked IPC -- the same scope as shell.spec.ts, not a
// Tauri/service integration test.

test("model catalog renders tiers and the download approval flow with mocked IPC", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") problems.push(`console: ${msg.text()}`);
  });
  page.on("pageerror", (err) => problems.push(`pageerror: ${err.message}`));

  await page.goto("/e2e/smoke.html");
  const nav = page.getByRole("navigation", { name: "Main" });
  await nav.getByRole("button", { name: "Model catalog", exact: true }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Model catalog" })).toBeFocused();
  // Network-capable labeling (ARCHITECTURE §11): browsing offline, download
  // is the one network step and asks for approval.
  await expect(
    page.getByText(/Downloading a model is the only step that uses the network/),
  ).toBeVisible();

  // Tier sections from the fixture catalog (synthetic ids only).
  for (const heading of ["Fast", "Embedding"]) {
    await expect(page.getByRole("heading", { level: 2, name: heading })).toBeVisible();
  }
  const fast = page.getByRole("region", { name: "Fast" });
  await expect(fast.getByText("Smoke Gen Small")).toBeVisible();
  await expect(fast.getByText(/1\.0 GB/)).toBeVisible();
  await expect(fast.getByText(/apache-2\.0/)).toBeVisible();
  await expect(fast.getByText("Recommended")).toBeVisible();
  await expect(page.getByRole("region", { name: "Embedding" }).getByText("Smoke Embed Default")).toBeVisible();

  // No download has been started by rendering.
  let calls = await page.evaluate(() => (window as unknown as { __e2e: { calls: string[] } }).__e2e.calls);
  expect(calls).not.toContain("start_catalog_download");

  // Approval gate: details first, nothing sent until Confirm.
  await fast.getByRole("button", { name: "Download…" }).click();
  const dialog = page.getByRole("region", { name: "Review download: Smoke Gen Small" });
  await expect(dialog.getByText("https://models.example.test/catalog-gen-small")).toBeVisible();
  await expect(dialog.getByText("a".repeat(64))).toBeVisible();
  await expect(dialog.getByText("smoke-gen-small.gguf")).toBeVisible();

  // Cancel makes no request at all.
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByText(/Review download/)).toHaveCount(0);
  calls = await page.evaluate(() => (window as unknown as { __e2e: { calls: string[] } }).__e2e.calls);
  expect(calls).not.toContain("start_catalog_download");

  // Confirm: exactly the approval POST, then live progress.
  await fast.getByRole("button", { name: "Download…" }).click();
  await page.getByRole("button", { name: "Download", exact: true }).click();
  await expect(page.getByText("Downloading Smoke Gen Small")).toBeVisible();
  await expect(page.getByText("smoke-gen-small.gguf")).toBeVisible();

  calls = await page.evaluate(() => (window as unknown as { __e2e: { calls: string[] } }).__e2e.calls);
  expect(calls.filter((c) => c === "start_catalog_download")).toHaveLength(1);
  expect(calls).toContain("get_download_status");
  expect(problems).toEqual([]);
});
