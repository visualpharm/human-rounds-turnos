// ceiling.mjs - how long a node test waits before it calls something a hang (the JS twin of
// tests/_livehttp.response_ceiling, 2026-11-07).
//
// A browser test asserts what the page shows and sends; how long Chromium takes to show it is not
// part of any assertion. Fixed budgets of 3-5 s per locator and 10 s per test turned a loaded machine
// (load 90+, several builds at once) into red tests with nothing wrong in them, each green alone on
// the same commit. So every wait of this tier has ONE ceiling: `waitCeiling()` - 120 s by default,
// `TURNOS_TEST_BROWSER_CEILING` (seconds) overrides it - and only a page that never gets there
// reaches it. A test as a whole gets `testCeiling()` (three waits' worth): it is a backstop for a
// test that hangs between waits, not a speed check.
//
// Reaching a ceiling fails with an AssertionError that says what was awaited, the ceiling, the
// seconds actually waited and the machine's load average, instead of a bare «Timeout 5000ms
// exceeded» that reads like a slow product.
//
//   import { test, contextOptions, openPage, within } from "./helpers/ceiling.mjs";
//   test("name", async (t) => { ... });                     // node:test's test, under the test ceiling
//   const page = await openPage(browser, { viewport });     // a fresh context, its waits under the ceiling
//   await within(page.waitForRequest(isX), "the X request"); // any promise under the ceiling
//
// A negative window («nothing is sent for 300 ms») is not a wait: keep it short and explicit
// (`stays` in wait.mjs, or Playwright's own timeout on a wait that must NOT happen).
import assert from "node:assert/strict";
import nodeTest from "node:test";
import os from "node:os";

export const CEILING_ENV = "TURNOS_TEST_BROWSER_CEILING";
export const DEFAULT_CEILING_S = 120;
const TESTS_PER_CEILING = 3;
const GRACE_MS = 5000;

/** Milliseconds one wait may take before it is a hang: the env value in seconds when positive, else 120 s. */
export function waitCeiling(env = process.env) {
  const raw = String(env[CEILING_ENV] ?? "").trim();
  const seconds = raw ? Number(raw) : DEFAULT_CEILING_S;
  return (Number.isFinite(seconds) && seconds > 0 ? seconds : DEFAULT_CEILING_S) * 1000;
}

/** Milliseconds a whole test may take: a backstop for a hang between waits. */
export function testCeiling(env = process.env) {
  return TESTS_PER_CEILING * waitCeiling(env);
}

export function loadAverage() {
  const [one] = os.loadavg();
  return Number.isFinite(one) ? one.toFixed(1) : "unknown";
}

/** The failure every ceiling of this tier raises: what, the ceiling, the time waited, the load. */
export function hang(what, ceilingMs, waitedMs, cause) {
  const error = new assert.AssertionError({
    message: `no ${what} within the ${ceilingMs / 1000} s ceiling (waited ${(waitedMs / 1000).toFixed(1)} s, `
      + `load average ${loadAverage()}); the ceiling already covers a loaded machine, so this is a hang. `
      + `${CEILING_ENV} raises the ceiling.`,
  });
  if (cause) error.cause = cause;
  return error;
}

/** `promise`, or a `hang` failure once the ceiling passes first. */
export function within(promise, what, { ceiling = waitCeiling() } = {}) {
  const started = performance.now();
  let timer;
  const limit = new Promise((_, reject) => {
    timer = setTimeout(() => reject(hang(what, ceiling, performance.now() - started)), ceiling);
  });
  return Promise.race([promise, limit]).finally(() => clearTimeout(timer));
}

// Playwright's own «Timeout 120000ms exceeded» gets the same words: what, how long, under what load.
// Its message keeps the call log (which locator, which request), the cause keeps its stack.
export function explained(error, started, ceiling = waitCeiling()) {
  if (error?.name !== "TimeoutError") return error;
  const wrapped = new assert.AssertionError({
    message: `${error.message}\n-- a wait of this test reached the ${ceiling / 1000} s ceiling `
      + `(test running ${((performance.now() - started) / 1000).toFixed(1)} s, load average ${loadAverage()}); `
      + `the ceiling already covers a loaded machine, so this is a hang. ${CEILING_ENV} raises the ceiling.`,
  });
  wrapped.cause = error;
  return wrapped;
}

/** Every wait of this context's (or page's) pages - actions, locators, navigation - under the ceiling. */
export function ceil(contextOrPage, ceiling = waitCeiling()) {
  contextOrPage.setDefaultTimeout(ceiling);
  contextOrPage.setDefaultNavigationTimeout(ceiling);
  return contextOrPage;
}

/** A page in a fresh context of the file's one browser; close it with `page.context().close()`. */
export async function openPage(browser, contextOptions = {}) {
  const context = ceil(await browser.newContext(contextOptions));
  return context.newPage();
}

/** Options for a `before`/`after` hook that launches or closes Chromium. */
export function hookOptions() {
  return { timeout: testCeiling() + GRACE_MS };
}

/**
 * node:test's `test`, with the tier's ceiling: the body fails with `hang` past `testCeiling()`, a
 * Playwright TimeoutError inside it is reported with the time waited and the load. A `timeout`
 * passed in options is raised to the ceiling, never lowered by it.
 */
export function test(name, options, fn) {
  if (typeof options === "function") [fn, options] = [options, {}];
  const limit = testCeiling();
  const guarded = fn && (async (t) => {
    const started = performance.now();
    try {
      return await within(Promise.resolve().then(() => fn(t)), `end of the test «${name}»`, { ceiling: limit });
    } catch (error) {
      throw explained(error, started);
    }
  });
  return nodeTest(name, { ...options, timeout: Math.max(options?.timeout ?? 0, limit + GRACE_MS) }, guarded);
}
for (const key of ["before", "after", "beforeEach", "afterEach", "describe", "it", "suite", "mock", "skip", "todo", "only"]) {
  if (key in nodeTest) test[key] = nodeTest[key];
}
