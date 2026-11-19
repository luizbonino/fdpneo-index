// Screenshots for the documentation (TASKS 14.2, 14.9):
//
//   node scripts/screenshots.mjs http://localhost:5173 ../docs/assets/screens
//
// Takes the main pages of a running index, light and dark, desktop and phone,
// in English. Run it against an index with harvested data (the demo seed,
// server/bench/seed_demo.py) and commit the pictures the guides use.
import { mkdir } from "node:fs/promises";
import { chromium } from "@playwright/test";

const [base = "http://localhost:5173", out = "screens"] = process.argv.slice(2);
await mkdir(out, { recursive: true });
const api = (path) => fetch(new URL(path, base)).then((r) => r.json());
const hit = (await api("/index/search?type=dataset&size=1&facets=none")).hits?.[0];
const pages = [
  ["directory", "/"],
  ["search", "/search?type=dataset"],
  ["sparql", "/sparql"],
  ...(hit
    ? [
        ["resource", `/search/r/${hit.id}`],
        ["fdp", `/entries/${hit.fdp.uuid}`],
      ]
    : []),
];
const browser = await chromium.launch();
for (const [label, width, scheme] of [
  ["desktop", 1280, "light"],
  ["dark", 1280, "dark"],
  ["phone", 390, "light"],
]) {
  const context = await browser.newContext({
    viewport: { width, height: 900 },
    colorScheme: scheme,
    locale: "en-GB",
  });
  const page = await context.newPage();
  for (const [name, path] of pages) {
    await page.goto(new URL(path, base).href);
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${out}/${name}-${label}.png`, fullPage: label !== "phone" });
  }
  await context.close();
}
await browser.close();
console.log(`${pages.length * 3} screenshots in ${out}`);
