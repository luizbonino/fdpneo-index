/**
 * End-to-end smoke test against a running stack (deploy/compose.yaml):
 *
 *   FDPINDEX_E2E_URL=http://127.0.0.1:8090 FDPINDEX_E2E_TOKEN=<admin token> npm run test:e2e
 *
 * The test registers an FDP through a real ping, so the stack must be able to
 * reach FDPINDEX_E2E_FDP (an FDP's address as seen from the server container).
 */
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 60_000,
  use: {
    baseURL: process.env.FDPINDEX_E2E_URL ?? "http://127.0.0.1:8090",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
