import { defineConfig, devices } from "@playwright/test";

/**
 * Cross-browser smoke config for the Phase 7 quality gate. The main
 * `playwright.config.ts` intentionally stays Chromium-only (that is what
 * `scripts/run_e2e.py` runs on every change); this file adds Firefox/WebKit
 * as a manually-triggered smoke pass over a small, representative subset of
 * specs rather than tripling the full suite's runtime on every run.
 */
export default defineConfig({
  testDir: "./e2e",
  // Stateless specs only: Firefox and WebKit projects share one backend database in
  // this config, so a stateful spec like approvals.spec.ts (which decides durable
  // gates) would have its second browser project resume past gates the first already
  // decided. That is a shared-fixture limitation of this smoke config, not something
  // this pass is meant to catch — full-suite parity across browsers is out of scope
  // for a smoke run and would need per-project database isolation to do properly.
  testMatch: ["shell.spec.ts"],
  workers: 1,
  timeout: 90_000,
  use: { baseURL: "http://127.0.0.1:3000", viewport: { width: 1440, height: 900 } },
  projects: [
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
});
