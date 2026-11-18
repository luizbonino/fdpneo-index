import { expect, test } from "@playwright/test";

const token = process.env.FDPINDEX_E2E_TOKEN ?? "";
const fdp = process.env.FDPINDEX_E2E_FDP ?? "http://host.docker.internal:8000";

test.describe.configure({ mode: "serial" });

test("an FDP that pings appears in the directory", async ({ page, request }) => {
  const ping = await request.post("/", { data: { clientUrl: fdp } });
  expect([204, 429]).toContain(ping.status());
  await expect
    .poll(async () => (await (await request.get("/index/entries/all")).json()) as unknown[], {
      timeout: 20_000,
    })
    .not.toHaveLength(0);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "FAIR Data Points" })).toBeVisible();
  const row = page.locator("table.entries tbody tr").filter({ hasText: new URL(fdp).host });
  await expect(row).toBeVisible();
  await row.getByRole("link").click();
  await expect(page.getByRole("heading", { name: "Metadata", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Activity" })).toBeVisible();
});

test("an administrator signs in and opens every admin page", async ({ page }) => {
  test.skip(!token, "set FDPINDEX_E2E_TOKEN to an admin token");
  await page.goto("/sign-in");
  await page.getByLabel("Admin token").fill(token);
  await page.getByRole("button", { name: "Sign in" }).click();
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Settings" })).toBeVisible();

  await nav.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Liveness checks" })).toBeVisible();
  await page.getByLabel("Test an address").fill("http://localhost:8080");
  await expect(page.getByRole("status").filter({ hasText: /Allowed|Refused/ })).toBeVisible();

  await nav.getByRole("link", { name: "Webhooks" }).click();
  await page.getByRole("button", { name: "Add webhook" }).click();
  await expect(page.getByLabel("Payload URL")).toBeVisible();

  await nav.getByRole("link", { name: /Approvals/ }).click();
  await expect(page.getByRole("heading", { name: "Approvals" })).toBeVisible();
});

test("an anonymous visitor is sent to sign in from admin pages", async ({ page }) => {
  await page.goto("/admin/settings");
  await expect(page).toHaveURL(/\/sign-in\?returnTo=/);
});

test("a visitor runs a ready-made SPARQL question and a query from the editor", async ({
  page,
}) => {
  await page.goto("/sparql");
  await page.getByRole("button", { name: /Datasets per FDP/ }).click();
  await page.getByRole("button", { name: "Run", exact: true }).first().click();
  await expect(page.getByText(/rows in/)).toBeVisible();
  await expect(page.locator(".results table")).toBeVisible();

  await page.getByRole("tab", { name: "Editor" }).click();
  await page.locator("#panel-editor").getByRole("button", { name: /^Run/ }).click();
  await expect(page.getByText(/rows in/)).toBeVisible();
  await page.getByRole("tab", { name: "History" }).click();
  await expect(page.locator(".history li")).toHaveCount(2);
});
