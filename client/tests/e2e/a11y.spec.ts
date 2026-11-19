/**
 * Accessibility pass (TASKS 8.9), against a running stack with some data
 * (the Compose stack with `bench/seed_demo.py`, or real FDPs):
 *
 *   FDPINDEX_E2E_URL=http://127.0.0.1:8090 npx playwright test a11y
 *
 * axe-core checks WCAG 2.2 A/AA and best practices on the public pages in
 * light and dark mode; the keyboard tests walk the search the way a keyboard
 * or screen-reader user does.
 */
import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];

async function firstDataset(page: Page): Promise<string> {
  const response = await page.request.get("/index/search?type=dataset&size=1&facets=none");
  const body = (await response.json()) as { hits: { id: string }[] };
  test.skip(!body.hits.length, "the index has no datasets");
  return body.hits[0]!.id;
}

async function audit(page: Page, path: string) {
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  const result = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const found = result.violations.map(
    (v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`,
  );
  expect(found, `${path}: ${found.join("\n")}`).toEqual([]);
}

for (const scheme of ["light", "dark"] as const) {
  test.describe(`axe, ${scheme}`, () => {
    test.use({ colorScheme: scheme });

    test("directory, search, resource, SPARQL, register and sign-in", async ({ page }) => {
      const id = await firstDataset(page);
      for (const path of [
        "/",
        "/search",
        "/search?q=data&type=dataset&highlight=true",
        `/search/r/${id}`,
        "/sparql",
        "/register",
        "/sign-in",
      ]) {
        await audit(page, path);
      }
    });
  });
}

test("the skip link leads to the main content", async ({ page }) => {
  await page.goto("/search");
  await page.waitForLoadState("networkidle");
  const skip = page.getByRole("link", { name: /skip/i });
  await skip.waitFor({ state: "attached" });
  await page.keyboard.press("Tab");
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#main$/);
});

test("the search box is a combobox driven by the keyboard", async ({ page }) => {
  await page.goto("/search");
  const box = page.getByRole("combobox", { name: /search titles/i });
  await box.focus();
  await box.pressSequentially("data", { delay: 30 });
  const list = page.getByRole("listbox");
  await expect(list).toBeVisible();
  await page.keyboard.press("ArrowDown");
  const active = await box.getAttribute("aria-activedescendant");
  expect(active).toBeTruthy();
  await expect(page.locator(`#${active}`)).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await expect(list).toBeHidden();
  await expect(box).toHaveAttribute("aria-expanded", "false");
});

test("a filter is ticked with the keyboard and the dialog closes with Escape", async ({ page }) => {
  await page.goto("/search?type=dataset");
  const first = page.locator(".facet input[type=checkbox]").first();
  await first.focus();
  await page.keyboard.press("Space");
  await expect(page).toHaveURL(/[?&](theme|subject|keyword|publisher|format)=/);
  const more = page.getByRole("button", { name: /show more/i }).first();
  if (await more.isVisible()) {
    await more.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(more).toBeFocused();
  }
});

test("paging moves focus to the results heading", async ({ page }) => {
  await page.goto("/search?type=dataset");
  await page.locator("article.result").first().waitFor();
  const next = page.getByRole("button", { name: /next/i }).first();
  test.skip(!(await next.isVisible()), "one page of results only");
  await next.click();
  await expect(page.locator(".count-line")).toBeFocused();
});

test("the dialogs pass axe while open", async ({ page }) => {
  await page.goto("/search?type=dataset");
  await page.locator("article.result").first().waitFor();
  for (const name of [/show more/i, /as a tree/i, /add a filter/i]) {
    const opener = page.getByRole("button", { name }).first();
    if (!(await opener.isVisible())) continue;
    await opener.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await page.waitForLoadState("networkidle");
    const result = await new AxeBuilder({ page }).include("dialog").withTags(TAGS).analyze();
    const found = result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join()}`);
    expect(found, `${String(name)}: ${found.join("\n")}`).toEqual([]);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
  }
});
