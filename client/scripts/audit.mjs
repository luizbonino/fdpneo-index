// npm audit gate: fail on high/critical advisories of production dependencies,
// except reviewed ones in audit-allowlist.json whose review date has not passed.
// npm audit itself cannot ignore a single advisory.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const LEVELS = new Set(["high", "critical"]);
const allow = JSON.parse(readFileSync(new URL("../audit-allowlist.json", import.meta.url), "utf8"));
const today = new Date().toISOString().slice(0, 10);

let raw;
try {
  raw = execFileSync("npm", ["audit", "--omit=dev", "--json"], { encoding: "utf8" });
} catch (error) {
  raw = error.stdout; // npm audit exits non-zero when it finds anything
}
const report = JSON.parse(raw);

const found = new Map();
for (const vuln of Object.values(report.vulnerabilities ?? {})) {
  for (const via of vuln.via) {
    if (typeof via !== "object" || !LEVELS.has(via.severity)) continue;
    const id = via.url?.split("/").pop() ?? String(via.source);
    found.set(id, { id, name: via.name, severity: via.severity, title: via.title });
  }
}

const problems = [];
for (const advisory of found.values()) {
  const entry = allow.advisories.find((a) => a.id === advisory.id);
  if (!entry)
    problems.push(`${advisory.severity}: ${advisory.name} ${advisory.id} — ${advisory.title}`);
  else if (entry.reviewBy < today)
    problems.push(`${advisory.id}: allowlist review date ${entry.reviewBy} has passed`);
  else console.log(`allowed until ${entry.reviewBy}: ${advisory.id} (${advisory.name})`);
}

if (problems.length) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log(`npm audit: no unreviewed high or critical advisories (${found.size} reviewed).`);
