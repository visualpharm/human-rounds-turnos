// @playwright/test configuration of the e2e gate (`npm run e2e`, tests/e2e/*.spec.mjs).
//
// One spec file = one app server + one throwaway database (the worker-scoped `app`
// fixture, tests/e2e/lib/fixtures.mjs); the tests of a file run in order, exactly as
// they did under node:test. Parallelism is across FILES: `workers` files at a time,
// each with its own server, database and browser.
//
// Knobs (environment, so a measurement or a CI job can sweep them without edits):
//   PW_WORKERS          parallel workers (default: one per core)
//   PW_RETRIES          retries per failing test (default 2); a pass on a retry is
//                       reported as «flaky» and, by default, still FAILS the run
//   PW_FAIL_ON_FLAKY=0  let a flaky pass (failed once, passed on a retry) exit 0.
//                       The gate default is strict: a test that needed a retry fails
//                       the run; the failed attempt keeps its screenshot and page snapshot
//                       and the first retry carries the trace.
//   PW_JSON             also write a machine-readable report to this path
//   PW_HTML_DIR / PW_OUTPUT_DIR   where the HTML report and the per-test artifacts
//                       go (default playwright-report/ and test-results/)
import os from "node:os";
import { defineConfig } from "@playwright/test";

const reporters = [
  ["list"],
  ["html", { open: "never", outputFolder: process.env.PW_HTML_DIR || "playwright-report" }],
];
if (process.env.PW_JSON) reporters.push(["json", { outputFile: process.env.PW_JSON }]);

export default defineConfig({
  testDir: "tests/e2e",
  testMatch: "**/*.spec.mjs",
  outputDir: process.env.PW_OUTPUT_DIR || "test-results",
  fullyParallel: false,
  workers: Number(process.env.PW_WORKERS) || os.availableParallelism(),
  retries: Number(process.env.PW_RETRIES ?? 2),
  failOnFlakyTests: process.env.PW_FAIL_ON_FLAKY !== "0",
  forbidOnly: true,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: reporters,
  use: {
    // Trace only when a test failed once and is being retried: a green run pays
    // nothing; the FIRST retry carries the full DOM/network/console (a trace of the
    // retry, not of the attempt that failed first). The failed attempts themselves keep
    // their screenshot and the page as text (error-context.md).
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    // The library's own defaults, which the node:test files ran with.
    actionTimeout: 30_000,
    navigationTimeout: 30_000,
  },
});
