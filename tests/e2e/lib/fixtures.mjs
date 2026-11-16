// tests/e2e/lib/fixtures.mjs -- the @playwright/test fixtures every tests/e2e/*.spec.mjs
// file uses. They sit ON TOP of lib/harness.mjs (throwaway Postgres database + the real
// `python3 asgi_main.py` + fixture.py seeding) and add nothing of their own to that:
// the harness stays the single place that knows how to start the app. The browser is
// the runner's (launchBrowser:false), which is what makes trace-on-first-retry,
// screenshot-on-failure and the HTML report work.
//
// ONE APP PER FILE. Every spec file starts with
//
//     test.use({ appOptions: { dbName: "sana_turnos_test_e2e_<file>", ...options } });
//
// `appOptions` is exactly what a node:test file passed to setupHarness(). It is a
// worker-scoped option, so Playwright starts a fresh worker -- and with it a fresh
// server and database -- for every distinct value: the `dbName` is therefore REQUIRED
// and must be unique per file (tests/e2e-spec-isolation.test.mjs pins both), otherwise
// two files with equal options would share one running app and its state.
//
// A file that needs no server at all says so: appOptions: { dbName, hermetic: true }.
//
// Fixtures
//   app          (worker)  the running app: { baseUrl, fixture, dbName, serverProc,
//                          getServerLog, ... }. A prerequisite that is missing FAILS
//                          the run (gate mode); only `npm run e2e:dev`
//                          (TURNOS_E2E_ALLOW_SKIP=1) turns it into a skip.
//   baseURL      (test)    points `page.goto("/x")` at that app.
//   adminPage    (test)    a page already signed in as the harness admin.
//   cleanup      (test)    cleanup(fn): async teardown that runs after the test, last in
//                          first out, even when the test fails or times out (replaces
//                          node:test's t.after).
//   launchApp    (test)    launchApp(options): a SECOND app for one test (other env,
//                          connector...); torn down after the test. Give it its own
//                          unique dbName.
//   newContext   (test)    newContext(options): another browser context of the runner
//                          (a second account, another viewport); baseURL preset and
//                          closed after the test.
//   serve        (test)    serve(handler, options): a throwaway http.Server on
//                          127.0.0.1 (a delaying proxy, a stub origin); closed after
//                          the test. Resolves { url, server, port }.
//
// Order dependence: tests of a file that hand state to each other (a record array a
// later test checks for completeness, a session built by an earlier test) declare
//     test.describe.configure({ mode: "serial" });
// so a failure skips the rest and a retry re-runs the whole file on a fresh worker.
import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { test as base, expect } from "@playwright/test";
import {
  setupHarness, teardownHarness, requireOk, ADMIN_USER, ADMIN_PASS,
} from "./harness.mjs";

export { expect, ADMIN_USER, ADMIN_PASS };
export {
  ROOT, GATE_PACK_CHAIN, totpCode, patientCookie, queryOffers, queryRequest, queryNota,
  queryUser, queryConsultation, setEnabledModules, setTheme,
} from "./harness.mjs";
export { artifactPath } from "./artifacts.mjs";

/**
 * Start an app through the harness with the runner owning the browser. Throws in gate
 * mode when a prerequisite is missing. Use `launchApp` inside a test; this is the
 * worker fixture's own entry point.
 */
export async function startApp(options) {
  if (!options || !options.dbName) {
    throw new Error("test.use({ appOptions: { dbName: 'sana_turnos_test_...' } }) is required in every spec file");
  }
  return setupHarness({ ...options, launchBrowser: false });
}

export const test = base.extend({
  appOptions: [{}, { option: true, scope: "worker" }],

  // Playwright Test defaults a context to `locale: "en-US"`, which makes Chromium send
  // `Accept-Language: en-US`; the library's browser.newPage() sent no such header. On an
  // app with several languages and no explicit LANG_DEFAULT the server lets that header
  // decide a newcomer's language, so every multi-language page would silently turn
  // English. No emulated locale = the header the node:test files effectively had (none).
  locale: [({ contextOptions }, use) => use(contextOptions.locale), { option: true, box: true }],

  app: [async ({ appOptions }, use) => {
    // `hermetic: true`: the file drives its own pages (Storybook, a stub origin) and
    // needs no server or database -- nothing is started.
    if (appOptions.hermetic) {
      await use({ ok: true, hermetic: true });
      return;
    }
    const state = await startApp(appOptions);
    try {
      await use(state);
    } finally {
      await teardownHarness(state);
    }
  }, { scope: "worker", timeout: 120_000 }],

  // The gate-vs-convenience branch the node:test files called as requireOk(state, t):
  // gate mode throws (the run FAILS), `npm run e2e:dev` skips with the reason.
  _gate: [async ({ app }, use) => {
    requireOk(app, { skip: (reason) => base.skip(true, reason) });
    await use();
  }, { auto: true }],

  baseURL: async ({ app }, use) => { await use(app.baseUrl); },

  cleanup: async ({}, use) => {
    const fns = [];
    await use((fn) => { fns.push(fn); });
    for (const fn of fns.reverse()) await fn();
  },

  launchApp: async ({ cleanup }, use) => {
    await use(async (options) => {
      const state = await startApp(options);
      cleanup(() => teardownHarness(state));
      requireOk(state, { skip: (reason) => base.skip(true, reason) });
      return state;
    });
  },

  newContext: async ({ browser, baseURL, cleanup }, use) => {
    await use(async (options = {}) => {
      const context = await browser.newContext({ baseURL, ...options });
      cleanup(() => context.close());
      return context;
    });
  },

  serve: async ({ cleanup }, use) => {
    await use(async (handler, { host = "127.0.0.1", port = 0 } = {}) => {
      const server = http.createServer(handler);
      await new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(port, host, resolve);
      });
      cleanup(() => new Promise((resolve) => {
        server.closeAllConnections?.();
        server.close(() => resolve());
      }));
      const address = server.address();
      return { server, port: address.port, url: `http://${host}:${address.port}` };
    });
  },

  adminPage: async ({ page, app }, use) => {
    await loginAdmin(page, app.baseUrl);
    await use(page);
  },
});

export async function loginAdmin(page, baseUrl, user = ADMIN_USER, pass = ADMIN_PASS, next = "/admin/account") {
  await page.goto(baseUrl + "/admin/login?next=" + next);
  await page.fill("#gate-user", user);
  await page.fill("#gate-pass", pass);
  await page.click("#gate-btn");
  await page.waitForURL((url) => url.pathname === next);
}

/**
 * Run `action` and resolve with the response of the first non-GET request to
 * `endpoint`; fails the test with the server's own error text unless it is a 200.
 */
export async function write(page, endpoint, action, wantBody = false) {
  const response = page.waitForResponse(
    (r) => new URL(r.url()).pathname === endpoint && !["GET", "HEAD"].includes(r.request().method()),
  ).then(async (result) => {
    if (result.status() !== 200) {
      const body = await result.json().catch(() => ({}));
      expect(result.status(), endpoint + ": " + (body.error || "")).toBe(200);
    }
    return wantBody ? result.json() : null;
  });
  await action();
  return response;
}

/**
 * A gate for a held route: `arrived` resolves when the route has the real response,
 * `held` stays pending until `release()` (also called on cleanup, so a failing test
 * never leaves a route hanging).
 */
export function holdGate(cleanup) {
  let release, arrive;
  const held = new Promise((resolve) => { release = resolve; });
  const arrived = new Promise((resolve) => { arrive = resolve; });
  cleanup(() => release());
  return { held, arrived, release, arrive };
}

/**
 * Evidence screenshot: written to `file` (kept under the artifacts tree exactly as the
 * node:test files did; CI collects that tree) and, by default, attached to the HTML report
 * (`attach: false` for a file that writes hundreds of frames per run). Resolves { path, bytes }.
 */
export async function saveShot(testInfo, target, file, { attach = true, ...options } = {}) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  const body = await target.screenshot({ ...options, path: file });
  if (attach) await testInfo.attach(path.basename(file), { body, contentType: "image/png" });
  return { path: file, bytes: (await fs.stat(file)).size };
}

/**
 * «Nothing happens for `during` ms» as a continuous invariant instead of a sleep followed
 * by one look: `check` (an async function that asserts) runs every `every` ms for the
 * whole window and the first violation fails. Use it only where the absence over time IS
 * the claim (a late request must not revive a screen, a click must not double-submit);
 * for everything that should become true, wait with a retrying expect.
 */
export async function holdsFor(check, { during = 500, every = 50 } = {}) {
  const end = Date.now() + during;
  do {
    await check();
    await sleep(every); // the interval between two looks at the invariant, not a pause in the test
  } while (Date.now() < end);
  await check();
}

/** Diagnostic line in the report (replaces node:test's t.diagnostic). */
export function note(testInfo, description) {
  testInfo.annotations.push({ type: "diagnostic", description });
}
