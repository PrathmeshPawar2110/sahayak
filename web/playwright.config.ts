import { defineConfig, devices } from "@playwright/test";

const PORT = 4173;

/**
 * M6-T09's smoke test. Unlike `npm test` (Vitest + MSW, no backend needed, runs in CI), this
 * drives a real browser against a real `sahayak` API, so it needs the stack up (`just up`) with
 * provider keys configured — the same reason the voice/ASR evals (D-045) run on demand, not in
 * CI (see D-0xx). Run it with `npm run e2e` after `npm run build`.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: "list",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run preview",
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
  },
});
