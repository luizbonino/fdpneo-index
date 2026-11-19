/**
 * The interface languages (TASKS 13.6), against a running stack with data:
 *
 *   FDPINDEX_E2E_URL=http://127.0.0.1:8090 npx playwright test i18n
 *
 * Every language renders the main pages with no message key left raw, sets
 * <html lang>, and keeps its choice; German passes axe as English does.
 */
import { AxeBuilder } from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const LOCALES = ["en", "nl", "de", "fr", "es", "it", "pt"];
const PAGES = ["/", "/search?type=dataset", "/sparql", "/register"];
// A message key left as text: "search.fields.title", "conformance.heading" …
const RAW_KEY =
  /\b(?:app|nav|search|sparql|conformance|settings|entry|common|saved|resource)\.[a-z][A-Za-z.]+\b/;

for (const locale of LOCALES) {
  test(`${locale}: the main pages have no raw message keys`, async ({ page }) => {
    for (const path of PAGES) {
      const url = `${path}${path.includes("?") ? "&" : "?"}locale=${locale}`;
      await page.goto(url);
      await page.waitForLoadState("networkidle");
      await expect(page.locator("html")).toHaveAttribute("lang", locale);
      const text = await page.locator("body").innerText();
      expect(text.match(RAW_KEY)?.[0] ?? null, `${url}`).toBeNull();
    }
  });
}

test("the language menu switches and remembers the language", async ({ page }) => {
  await page.goto("/search?locale=en");
  await page.getByRole("combobox", { name: "Language" }).selectOption("nl");
  await expect(page.locator("html")).toHaveAttribute("lang", "nl");
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("lang", "nl");
});

test("German passes axe", async ({ page }) => {
  for (const path of ["/?locale=de", "/search?type=dataset&locale=de"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"])
      .analyze();
    expect(
      result.violations.map((v) => v.id),
      path,
    ).toEqual([]);
  }
});
