// Native /admin/cola lifecycle acceptance against the real ASGI app, templates,
// fingerprinted assets and a disposable synthetic database.  Playwright routes
// only hold/reject the already-real module or the queue API to exercise owned
// recovery paths; neither the page nor the controller is replaced.
//
// Port of tests/e2e/admin-day-queue-lifecycle.e2e.mjs (16 tests, same titles, same order).
//
// Differences from the node:test original:
// - The file is one serial describe: every test appends a measurement record (assertions,
//   page errors, nonempty screenshots, per-phase geometry) to a shared `records` array and
//   the completeness pin reads all of them. A failure skips the rest and a retry re-runs
//   the whole file on a fresh worker with fresh records.
// - The completeness check of the original's `test.after` is split in two: a last test
//   («the evidence set is complete…») carries the assertion, and an `afterAll` still
//   writes report.json (also after a failure, so the durable evidence survives). So this
//   file has one test more than the original (17 vs 16). The evidence directory is
//   reserved in `beforeAll` instead of at import time (the runner imports spec files in
//   its main process as well, which would leave an empty directory per listing).
// - The evidence root is artifactPath("human-rounds", "day-queue-native-lifecycle"), the
//   same <artifacts>/human-rounds/... tree as before (default ~/.claude/artifacts, moved
//   by TURNOS_E2E_ARTIFACTS_DIR). Screenshots are taken with saveShot(), which writes the
//   same <id><suffix>.png files there and also attaches them to the report.
// - Each session is a runner-owned context from the `newContext` fixture (baseURL preset,
//   closed after the test) instead of `state.browser.newContext()` + `t.after`; URLs are
//   relative to it. The login steps and the about:blank landing are unchanged.
// - Q04: the real module is held by a route gate until the page's own 8 s deadline has shown
//   the late copy, then released, and the late module mounts the screen and clears the copy
//   (2026-11-16: the deadline no longer abandons the load). The gate (holdGate) is also
//   released on cleanup, so a failing test never leaves the route hanging. Waiting for the
//   copy out-waits the real 8 s deadline with a 12 s timeout, no sleep.
// - Q07's held first fetch uses the same gate; Q08's `requireOk` early return is gone (the
//   `_gate` fixture fails the run on a missing prerequisite).
// - Selector waits are web-first (`expect(locator).toBeVisible()`, `toContainText` with
//   `useInnerText`, `toHaveText`); the checks that guard a moment (aria-busy right after
//   pagehide, the attendance POST counter, the late-import state) stay snapshots.
// - Locators stay CSS where markup is the subject: the queue's data-* hooks, ids
//   (#cola-live, #queue-load-status, #cola-css) and the geometry measurements.
import {
  test, expect, holdGate, saveShot, artifactPath, ADMIN_USER, ADMIN_PASS,
} from "./lib/fixtures.mjs";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { allocateEvidenceRunDir } from "./lib/hsi-evidence.mjs";
import { cumulativeLayoutShift } from "./lib/layout-shift-evidence.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_day_queue_native", queue: true } });
test.describe.configure({ mode: "serial" });

const WIDTHS = [360, 390, 768, 1024, 1280];
const ENTRY = /\/static\/platform\/admin-queue\.[a-f0-9]+\.mjs(?=$|[?'\"])|\/static\/platform\/admin-queue\.[a-f0-9]+\.mjs\?/;
const IDS = ["Q01", ...WIDTHS.map((width) => `Q02-${width}`), ...WIDTHS.map((width) => `Q03-${width}`), "Q04", "Q05", "Q06", "Q07", "Q08"];
const REQUIRED_PHASES = Object.fromEntries([
  ["Q01", ["hydrated"]],
  ...WIDTHS.map((width) => [`Q02-${width}`, ["normal"]]),
  ...WIDTHS.map((width) => [`Q03-${width}`, ["recovery", "recovered"]]),
  ["Q04", ["recovery"]],
  ["Q05", ["remounted"]],
  ["Q06", ["failure", "recovered"]],
  ["Q07", ["takeover"]],
  ["Q08", ["calendar", "navigated"]],
]);
const records = [];
let evidence;

test.beforeAll(() => {
  evidence = allocateEvidenceRunDir({
    root: artifactPath("human-rounds", "day-queue-native-lifecycle"), label: "run",
  });
});
test.afterAll(async () => {
  await writeFile(join(evidence, "report.json"), JSON.stringify({
    expectedIDs: IDS,
    complete: complete(records),
    records,
    boundary: "Real local ASGI server, rendered templates, fingerprinted assets and synthetic queue fixture. Module/API routes only create failure and timeout conditions.",
    actualBFCache: "Unproved: synthetic persisted pagehide/pageshow events prove bootstrap ownership and a fresh fetch, not browser BFCache admission.",
  }, null, 2));
});

function complete(rows) {
  return rows.length === IDS.length && new Set(rows.map((row) => row.id)).size === IDS.length
    && IDS.every((id) => rows.some((row) => row.id === id))
    && new Set(rows.flatMap((row) => row.assets.map((asset) => asset.path))).size === rows.flatMap((row) => row.assets).length
    && rows.every((row) => row.assertionComplete && row.errors.length === 0 && row.assets.length > 0
      && row.assets.every((asset) => asset.bytes > 0)
      && REQUIRED_PHASES[row.id].every((phase) => row.geometry?.[phase]));
}
async function session({ newContext }, id, width = 390) {
  const context = await newContext({ viewport: { width, height: 900 } });
  await context.addInitScript(() => {
    window.__queueShifts = [];
    window.__queueShiftRecord = (entry) => ({ value: entry.value, startTime: entry.startTime,
      sources: (entry.sources || []).map((source) => ({ node: source.node?.id || source.node?.className || source.node?.nodeName })) });
    window.__queueObserver = new PerformanceObserver((list) => {
      window.__queueShifts.push(...list.getEntries().filter((entry) => !entry.hadRecentInput).map(window.__queueShiftRecord));
    });
    window.__queueObserver.observe({ type: "layout-shift", buffered: true });
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.goto("/admin/login", { waitUntil: "load", timeout: 15000 });
  await expect(page.locator("#gate-form")).toBeVisible();
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  await page.waitForURL((url) => url.pathname.startsWith("/admin/") && url.pathname !== "/admin/login", { waitUntil: "commit" });
  // End the login landing document before installing case-specific fault routes.
  // Its asynchronous queue import must not consume the next page's one-shot fault.
  await page.goto("about:blank");
  const record = { id, errors, assets: [], assertionComplete: false };
  records.push(record);
  return { page, context, record };
}
// A fresh /admin/cola document paints the SSR queue first; once the event
// stream sends "ready" the controller reconciles that day with one JSON load
// and repaints the whole queue (admin-queue.mjs iniciarEventos -> cargar ->
// render). A measurement taken before that reconciliation reads the day the
// server painted, not the one the controller settles on, and the shifts of
// the repaint land after it. The applied load announces its day in #cola-live —
// render() recreates that region empty and only a day load fills it — so
// wait for that before opening a group and measuring.
async function waitForQueueReconciled(page) {
  await expect(page.locator("#cola-live")).toHaveText(/\S/, { timeout: 12000 });
}
// The day is one list (2026-11-08): the unassigned rows are there with
// «Sin médico asignado» in the doctor column, nothing to open first.
async function openUnassignedOnCurrentPage(page, pendingName) {
  await expect(page.locator('#sec-cola button.queue-doctor-link[data-cola-doctor="_sin_medico"]').first()).toBeVisible({ timeout: 12000 });
  await expect(page.locator("#sec-cola")).toContainText(pendingName, { useInnerText: true, timeout: 12000 });
}
async function openQueue(page, pendingName) {
  await page.goto("/admin/cola", { waitUntil: "load", timeout: 15000 });
  await expect(page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 12000 });
  await waitForQueueReconciled(page);
  await openUnassignedOnCurrentPage(page, pendingName);
}
async function capture(testInfo, page, record, suffix = "") {
  await page.evaluate(() => scrollTo(0, 0));
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  record.assets.push(await saveShot(testInfo, page, join(evidence, record.id + suffix + ".png"), { fullPage: true }));
}
function pass(record) {
  expect(record.errors).toEqual([]);
  record.assertionComplete = true;
}
async function geometry(page, width) {
  return page.evaluate(() => {
    const viewport = document.documentElement.clientWidth;
    const hosts = [document.getElementById("queue-load-status"), document.getElementById("sec-cola")].filter(Boolean);
    const visible = (element) => element.getBoundingClientRect().width > 0 && getComputedStyle(element).visibility !== "hidden";
    const scoped = hosts.flatMap((host) => [host, ...host.querySelectorAll("*")]);
    const escaped = scoped.filter(visible).flatMap((element) => {
      const box = element.getBoundingClientRect();
      return box.left < -1 || box.right > viewport + 1 ? [{ tag: element.tagName, cls: String(element.className || ""), left: box.left, right: box.right }] : [];
    });
    // A table wrapper with overflow-x auto/scroll/hidden/clip cuts its contents
    // inside the pane, and containment keeps documentElement.scrollWidth equal
    // to the viewport — so the page-level checks above stay green while headers
    // and cells are cut at the pane boundary. Measure inside each such wrapper:
    // every rendered th/td (dropped optional columns are display:none and stay
    // exempt) must fit its host, including the plain SSR recovery table.
    const containment = hosts.flatMap((host) => [...host.querySelectorAll(".cnt-table-wrap,.queue-server-initial")])
      .filter(visible)
      .map((wrap) => {
        const box = wrap.getBoundingClientRect();
        const cells = [...wrap.querySelectorAll("th,td")].filter((cell) => {
          const style = getComputedStyle(cell);
          if (style.display === "none" || style.visibility === "hidden") return false;
          const rect = cell.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        });
        const clipped = cells.filter((cell) => {
          const rect = cell.getBoundingClientRect();
          return rect.right > box.right + 1 || rect.left < box.left - 1;
        }).map((cell) => {
          const rect = cell.getBoundingClientRect();
          return {
            tag: cell.tagName,
            col: cell.getAttribute("data-col") || cell.getAttribute("data-label") || cell.textContent.trim().slice(0, 24),
            left: Math.round(rect.left), right: Math.round(rect.right),
          };
        });
        // A cell can fit while its own nowrap/ellipsis still hides the text.
        // Measure the doctor names' actual text fragments, not just cell boxes.
        const textClipped = [];
        for (const cell of cells.filter((cell) => cell.matches('td[data-col="medico"]'))) {
          const bounds = cell.getBoundingClientRect();
          const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
          while (walker.nextNode()) {
            if (!walker.currentNode.textContent.trim()) continue;
            const range = document.createRange();
            range.selectNodeContents(walker.currentNode);
            for (const rect of range.getClientRects()) {
              if (rect.width > 0 && (rect.left < bounds.left - 1 || rect.right > bounds.right + 1)) {
                textClipped.push({ col: cell.getAttribute("data-label"), left: rect.left, right: rect.right });
              }
            }
          }
        }
        return {
          table: String(wrap.querySelector("table")?.className || "").trim() || "table",
          paneWidth: Math.round(box.width), clientWidth: wrap.clientWidth, scrollWidth: wrap.scrollWidth,
          visibleCells: cells.length, clipped, textClipped,
        };
      });
    const retry = document.querySelector("#queue-load-status [data-retry]");
    const title = document.getElementById("adm-h1");
    const retryBox = retry?.getBoundingClientRect();
    const titleBox = title?.getBoundingClientRect();
    const shifts = [...window.__queueShifts, ...window.__queueObserver.takeRecords()
      .filter((entry) => !entry.hadRecentInput).map(window.__queueShiftRecord)];
    return {
      width: viewport,
      overflow: document.documentElement.scrollWidth - viewport,
      escaped,
      containment,
      shifts,
      retry: retryBox && titleBox ? {
        width: retryBox.width, height: retryBox.height, top: retryBox.top, bottom: retryBox.bottom,
        firstViewport: retryBox.top >= 0 && retryBox.bottom <= innerHeight,
        belowTitle: retryBox.top >= titleBox.bottom,
      } : null,
    };
  });
}
async function recordGeometry(page, record, phase, width, { recovery = false } = {}) {
  const measured = await geometry(page, width);
  measured.cls = cumulativeLayoutShift(measured.shifts);
  record.geometry ??= {};
  record.geometry[phase] = measured;
  expect(measured.width).toBe(width);
  // scrollWidth is an integer rounded up from the real fractional layout, so
  // a content-dependent 0.x px lands as 1 (the same 1px tolerance the rest of
  // the browser gate uses). A genuine overflow is tens of pixels, and the
  // per-pane containment check below still runs with no tolerance at all.
  expect(measured.overflow, `${phase} queue overflows by ${measured.overflow}px at ${width}px`).toBeLessThanOrEqual(1);
  expect(measured.escaped).toEqual([]);
  expect(measured.containment.some((pane) => pane.visibleCells > 0), "measure real visible table cells, not an empty pane list").toBe(true);
  const clippedPanes = measured.containment.filter((pane) => pane.clipped.length > 0 || pane.textClipped.length > 0);
  expect(clippedPanes,
    `${phase} queue table content is clipped inside its own scroll container at ${width}px `
    + `(documentElement overflow stays ${measured.overflow}px while the pane cuts visible cells): `
    + JSON.stringify(clippedPanes)).toEqual([]);
  expect(measured.cls, `observed CLS ${measured.cls} during ${phase}`).toBeLessThanOrEqual(0.1);
  if (recovery) {
    expect(measured.retry, "recovery must expose its retry action inside the measured content").toBeTruthy();
    expect(measured.retry.firstViewport, "recovery retry must be in the first viewport").toBe(true);
    expect(measured.retry.belowTitle, "recovery retry must remain below the page title").toBe(true);
    expect(measured.retry.width >= 44 && measured.retry.height >= 44,
      `recovery retry target is ${measured.retry.width}x${measured.retry.height}, below 44px`).toBe(true);
  }
}

test("Q01 server SSR has no classic entry and the native fingerprinted bytes hydrate it", async ({ app, newContext }, testInfo) => {
  const { page, context, record } = await session({ newContext }, "Q01");
  const response = await context.request.get("/admin/cola");
  const html = await response.text();
  expect(response.status()).toBe(200);
  expect(html).toMatch(ENTRY);
  expect(html).not.toMatch(/<script[^>]+admin-queue\.js/);
  expect(html).toMatch(/data-queue-server-rendered="true"/);
  await openQueue(page, app.fixture.queue_pendiente);
  expect(await page.evaluate(() => performance.getEntriesByType("resource").some((entry) => /\/static\/platform\/admin-queue\.[a-f0-9]+\.mjs/.test(entry.name)))).toBe(true);
  // Capture before the geometry assertions: a RED fit failure still leaves the
  // offending rendered state in the durable evidence set.
  await capture(testInfo, page, record);
  await recordGeometry(page, record, "hydrated", 390);
  pass(record);
});

for (const width of WIDTHS) {
  test(`Q02-${width} normal native hydration keeps the real queue usable at ${width}px`, async ({ app, newContext }, testInfo) => {
    const { page, record } = await session({ newContext }, `Q02-${width}`, width);
    await openQueue(page, app.fixture.queue_pendiente);
    await capture(testInfo, page, record);
    await recordGeometry(page, record, "normal", width);
    if (width > 1024) {
      // A repaint is fitted in the task that draws it (2026-11-10): left to the next frame,
      // a repaint landing late in a frame was painted once at the table's natural width and
      // jumped when the fit arrived — the doctor column moved 296 px at 1280, once for the
      // first paint and once for the day's reconciliation (CLS 0.053 each, over the limit
      // together). Read in the same task as the redraw, so no frame timing decides it. The
      // redraw is the search field's own (an input event with the text it already has): it
      // changes nothing the later tests of this file would find saved.
      const layout = await page.evaluate(() => {
        document.querySelector("#sec-cola [data-cola-buscar]").dispatchEvent(new Event("input", { bubbles: true }));
        return document.querySelector("#sec-cola table.queue-table").style.tableLayout;
      });
      expect(layout, "a redrawn queue table has its column widths before it is painted").toBe("fixed");
    }
    pass(record);
  });
  test(`Q03-${width} rejected native import preserves SSR and the real reload recovers at ${width}px`, async ({ app, newContext }, testInfo) => {
    const { page, record } = await session({ newContext }, `Q03-${width}`, width);
    let rejected = false;
    await page.route(ENTRY, (route) => {
      if (!rejected) { rejected = true; return route.abort(); }
      return route.continue();
    });
    await page.goto("/admin/cola", { waitUntil: "load", timeout: 15000 });
    await expect(page.locator("#queue-load-status [data-retry]")).toBeVisible({ timeout: 12000 });
    expect(await page.locator("#sec-cola").innerText()).toMatch(new RegExp(app.fixture.queue_pendiente));
    await capture(testInfo, page, record, "-recovery");
    await recordGeometry(page, record, "recovery", width, { recovery: true });
    await page.locator("#queue-load-status [data-retry]").click();
    await expect(page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 12000 });
    expect(rejected).toBe(true);
    expect(await page.locator("#queue-load-status [data-retry]").count(), "reload must clear recovery status").toBe(0);
    // The reloaded document is a fresh SSR page with its own reconciliation.
    await waitForQueueReconciled(page);
    await openUnassignedOnCurrentPage(page, app.fixture.queue_pendiente);
    await capture(testInfo, page, record, "-recovered");
    await recordGeometry(page, record, "recovered", width);
    pass(record);
  });
}

test("Q04 a real module held past the deadline shows the late copy, then mounts and clears it", async ({ app, newContext, cleanup }, testInfo) => {
  test.setTimeout(30000);
  const { page, record } = await session({ newContext }, "Q04");
  // `held` keeps the real module back until release(): the page's own 8 s deadline shows
  // the late copy in the status line while the server-rendered queue stays readable; the
  // late module then mounts the screen and the copy goes (2026-11-16, shared loader).
  const gate = holdGate(cleanup);
  await page.route(ENTRY, async (route) => {
    await gate.held;
    await route.continue();
    gate.arrive();
  });
  await page.goto("/admin/cola", { waitUntil: "commit", timeout: 15000 });
  await expect(page.locator("#queue-load-status [data-retry]")).toBeVisible({ timeout: 12000 });
  expect(await page.locator("#sec-cola").innerText()).toMatch(new RegExp(app.fixture.queue_pendiente));
  expect(await page.locator("#sec-cola .queue-controls").count(), "nothing mounted before the release").toBe(0);
  await recordGeometry(page, record, "recovery", 390, { recovery: true });
  gate.release();
  await gate.arrived;
  await expect(page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 12000 });
  await expect(page.locator("#queue-load-status [data-retry]"), "the late answer clears the copy").toHaveCount(0);
  await capture(testInfo, page, record); pass(record);
});

test("Q05 persisted page lifecycle disposes the owner and pageshow refetches current queue data", async ({ app, newContext }, testInfo) => {
  const { page, record } = await session({ newContext }, "Q05");
  await openQueue(page, app.fixture.queue_pendiente);
  let attendancePosts = 0;
  await page.route(/\/api\/admin\/requests\/[^/]+\/attendance$/, (route) => {
    attendancePosts++;
    return route.continue();
  });
  // Attendance is marked in the appointment card (2026-11-09): the open card belongs to the owner.
  const pendingRow = page.locator("tr.cnt-tr", { hasText: app.fixture.queue_pendiente }).first();
  await pendingRow.locator(".queue-pat-link").click();
  await expect(page.locator(".queue-card [data-asis]").first()).toBeVisible();
  await page.evaluate(() => dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  expect(await page.locator("#sec-cola").getAttribute("aria-busy"), "pagehide stop releases bootstrap busy ownership").toBeNull();
  await expect(page.locator(".queue-card"), "pagehide releases the open card with its owner").toHaveCount(0);
  await pendingRow.locator(".queue-pat-link").click();
  await page.evaluate(() => new Promise((resolve) => queueMicrotask(() => requestAnimationFrame(resolve))));
  await expect(page.locator(".queue-card"), "old DOM opens no card after pagehide").toHaveCount(0);
  expect(attendancePosts, "old DOM attendance action must have no owner after pagehide").toBe(0);
  const fresh = page.waitForResponse((response) => /\/api\/admin\/day-queue(?:\?|$)/.test(response.url()), { timeout: 12000 });
  await page.evaluate(() => dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  expect((await fresh).status()).toBe(200);
  await expect(page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 12000 });
  await openUnassignedOnCurrentPage(page, app.fixture.queue_pendiente);
  const posted = page.waitForResponse((response) => /\/api\/admin\/requests\/[^/]+\/attendance$/.test(response.url()), { timeout: 12000 });
  await page.locator("tr.cnt-tr", { hasText: app.fixture.queue_pendiente }).first().locator(".queue-pat-link").click();
  await page.locator('.queue-card [data-asis][data-valor="presente"]').click();
  expect((await posted).status()).toBe(200);
  expect(attendancePosts, "fresh owner must submit exactly one attendance action").toBe(1);
  await page.locator(".queue-card .solmod-x").click();
  await capture(testInfo, page, record);
  await recordGeometry(page, record, "remounted", 390);
  pass(record);
});

test("Q06 first client fetch failure rejects readiness, preserves SSR, and recovers through real reload", async ({ app, newContext }, testInfo) => {
  const { page, record } = await session({ newContext }, "Q06");
  await openQueue(page, app.fixture.queue_pendiente);
  // Recovery placement is measured from the normal top-of-page entry point,
  // before injecting the failure rather than after it has changed the DOM.
  await page.evaluate(() => new Promise((resolve) => {
    scrollTo(0, 0);
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }));
  let failed = false;
  await page.route(/\/api\/admin\/day-queue(?:\?|$)/, (route) => {
    if (!failed) { failed = true; return route.fulfill({ status: 503, body: "synthetic queue unavailable" }); }
    return route.continue();
  });
  await page.evaluate(() => dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  await page.evaluate(() => dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await expect(page.locator("#queue-load-status [data-retry]")).toBeVisible({ timeout: 12000 });
  expect(failed).toBe(true);
  expect(await page.locator("#cola-css").count(), "document-owned queue stylesheet remains after failed persisted restart").toBe(1);
  expect(await page.locator("#cola-css").getAttribute("href")).toBeTruthy();
  expect(await page.locator("#cola-css").evaluate((link) => !!link.sheet), "queue stylesheet remains applied during recovery").toBe(true);
  expect(await page.locator("#sec-cola").innerText()).toMatch(new RegExp(app.fixture.queue_pendiente));
  await capture(testInfo, page, record, "-failure");
  await recordGeometry(page, record, "failure", 390, { recovery: true });
  await page.locator("#queue-load-status [data-retry]").click();
  await expect(page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 12000 });
  expect(await page.locator("#queue-load-status [data-retry]").count(), "reload must clear first-fetch recovery").toBe(0);
  await openUnassignedOnCurrentPage(page, app.fixture.queue_pendiente);
  await capture(testInfo, page, record, "-recovered");
  await recordGeometry(page, record, "recovered", 390);
  pass(record);
});

test("Q07 a persisted restart survives an aborted first fetch when a typed day takes over", async ({ app, newContext, cleanup }, testInfo) => {
  const { page, record } = await session({ newContext }, "Q07");
  await openQueue(page, app.fixture.queue_pendiente);
  const nextDay = await page.evaluate(() => {
    const initial = JSON.parse(document.getElementById("queue-initial-data").textContent);
    const date = new Date(`${initial.dia}T12:00:00`);
    // Next WEEKDAY: the fixture agendas run Monday–Friday, so on a Saturday
    // "tomorrow" is a Sunday with no agendas and no «vacías» action to open
    // (failed on Sat 2026-10-17, independent of the code under test).
    do { date.setDate(date.getDate() + 1); } while (date.getDay() === 0 || date.getDay() === 6);
    return date.toISOString().slice(0, 10);
  });
  // The first day-queue request is held until released; `arrived` fires when it is seen.
  const gate = holdGate(cleanup);
  let firstUrl;
  await page.route(/\/api\/admin\/day-queue(?:\?|$)/, async (route) => {
    if (!firstUrl) {
      firstUrl = route.request().url();
      gate.arrive();
      await gate.held;
      try { await route.continue(); } catch { /* aborting this held request is the asserted behavior below */ }
      return;
    }
    await route.continue();
  });
  await page.evaluate(() => dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  await page.evaluate(() => dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await gate.arrived;
  const firstAborted = page.waitForEvent("requestfailed", {
    predicate: (request) => request.url() === firstUrl && /abort/i.test(request.failure()?.errorText || ""),
    timeout: 12000,
  });
  const nextResponse = page.waitForResponse((response) => response.url().includes(`dia=${nextDay}`), { timeout: 12000 });
  await page.locator("[data-cola-buscar]").fill(nextDay);
  await firstAborted;
  gate.release();
  expect((await nextResponse).status(), "typed current day must receive a real successful response").toBe(200);
  await expect(page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 12000 });
  expect(await page.locator("#queue-load-status [data-retry]").count(), "superseded AbortError must not enter recovery").toBe(0);
  expect(await page.locator("#sec-cola").getAttribute("aria-busy"), "current queue must become usable after takeover").toBeNull();
  // Tomorrow has unbooked agendas only: free hours are hidden until «Mostrar
  // horarios libres» (2026-10-27), then collapsed by the actual queue policy.
  // Open them through the visible actions before measuring real table content.
  // The box waits behind the funnel (2026-11-09).
  const libres = page.locator("[data-cola-libres]");
  const funnel = page.locator("#sec-cola [data-filter-toggle]");
  if (!(await libres.isVisible()) && await funnel.isVisible()) await funnel.click();
  await libres.check();
  await page.locator('[data-cola-vacias="1"]').click();
  await expect(page.locator("#sec-cola table.queue-table")).toBeVisible();
  await capture(testInfo, page, record);
  await recordGeometry(page, record, "takeover", 390);
  // The box is a saved choice: leave it as it was for the next sessions.
  const saved = page.waitForResponse((res) => res.url().includes("/api/admin/preferences") && res.request().method() !== "GET");
  await libres.uncheck();
  await saved;
  pass(record);
});


test("Q08 occupied dates are highlighted and arrows jump through the real permission-scoped calendar", async ({ app, newContext }, testInfo) => {
  const { page, record } = await session({ newContext }, "Q08", 390);
  await openQueue(page, app.fixture.queue_pendiente);
  const today = (await (await page.request.get("/api/admin/day-queue")).json()).dia;
  const services = (await (await page.request.get("/api/admin/content/services")).json()).items;
  const service = services.find((row) => !row._borrado && ["solicitud", "solicitud_con_orden"].includes(row.modo));
  expect(service, "synthetic catalog must have one scheduled service").toBeTruthy();
  function shift(day, count) {
    const value = new Date(day + "T12:00:00Z"); value.setUTCDate(value.getUTCDate() + count);
    return value.toISOString().slice(0, 10);
  }
  const futureDays = [shift(today, 2), shift(today, 5)];
  for (const day of futureDays) {
    const response = await page.request.post("/api/admin/appointments", {
      data: { nota: "Calendar acceptance synthetic patient", servicio_id: service.id, dia: day, hora: "09:11" },
    });
    expect(response.status()).toBe(200);
    expect((await response.json()).ok).toBe(true);
  }
  // Reconcile the same real document against the just-created approved rows.
  await openQueue(page, app.fixture.queue_pendiente);
  const first = today.slice(0, 7) + "-01";
  const last = shift(shift(first, 32).slice(0, 7) + "-01", -1);
  const calendarResponse = await page.request.get("/api/admin/day-queue/calendar?" +
    new URLSearchParams({ desde: first, hasta: last, dia: today }));
  expect(calendarResponse.status()).toBe(200);
  const calendar = await calendarResponse.json();
  expect(calendar.siguiente, "fixture creates real future occupied days").toBeTruthy();
  await page.locator("[data-cola-calendar-toggle]").click();
  await expect(page.locator(".queue-month-calendar")).toBeVisible();
  for (const day of calendar.dias) {
    expect((await page.locator('[data-cola-calendar-date="' + day + '"]').getAttribute("class")).includes("queue-calendar-occupied")).toBe(true);
  }
  await capture(testInfo, page, record, "-calendar");
  await recordGeometry(page, record, "calendar", 390);
  const next = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/admin/day-queue" &&
    new URL(response.url()).searchParams.get("dia") === calendar.siguiente);
  await page.locator('[data-cola-nav="1"]').click();
  expect((await next).status()).toBe(200);
  // The next rendered date must match the server response, including gaps.
  // The label is the short date; «Hoy» lives on its own button (2026-11-08).
  const expected = await page.evaluate(async (day) => {
    const dates = await import(window.SANA_STATIC["platform/dates.mjs"]);
    return (dates.cortaFija ? dates.cortaFija(day) : dates.corta(day, "")) || day;
  }, calendar.siguiente);
  // Slice 6 design: the toggle reads «Ver mes»/«Ocultar mes»; the date has its own label.
  await expect(page.locator(".queue-date-label").first()).toHaveText(expected);
  await capture(testInfo, page, record, "-navigated");
  await recordGeometry(page, record, "navigated", 390);
  pass(record);
});

test("the evidence set is complete: every unique measurement has assertions, no browser errors and a nonempty durable capture", async () => {
  expect(complete(records), "each unique measurement must have assertions, no browser errors, and a nonempty durable capture").toBe(true);
});
