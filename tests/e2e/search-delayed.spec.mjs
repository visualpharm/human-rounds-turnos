// Delayed native search module on home, reports search view, and the
// solicitudes Derivar combo. A source scan cannot prove callers wait for
// local searchReady / combo TLA before matching.
//
// Port of tests/e2e/search-delayed.e2e.mjs (6 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app is the worker-scoped `app` fixture (same dbName, `requests: true`). Every page
//   the original opened with `browser.newPage` (one per width / per variant) is a page of its
//   own browser context (`newContext`), so routes and cookies never leak between iterations.
// - The held requests (Search, nav, i18n, `/api/services`) use `holdGate` (released on
//   cleanup too) behind a shared `holdRoute`, replacing the file-local `deferred()`. The
//   «module was not requested» guards (hand-made 20 s timers raced against the route) are
//   `expect.poll` on the URL the route saw, same messages and timeouts. The `assert.match`
//   calls that ran INSIDE route handlers (module path of Search and of nav) run in the test
//   body right after that poll.
// - The cold-nav evidence (report.json, `<width>-before/after.png`) lives in the same place
//   (`<artifacts>/human-rounds/home-cold-nav-*`, via `artifactPath`, which honours
//   TURNOS_E2E_ARTIFACTS_DIR; the original hard-coded `~/.claude/artifacts`, the same
//   default) and is written as before, including the incomplete report when a width fails.
//   Screenshots go through `saveShot` (attached to the report); `t.diagnostic` is a
//   `note`. The `astra-search-responsive` screenshots likewise.
// - `loginAdmin` stays local (the shared one waits for an exact pathname; this one keeps the
//   original regex wait and the `networkidle` settle that follows the login).
// - Waits and assertions are web-first (`toBeVisible`, `toBeAttached`, `toHaveText` ...) where
//   the original waited and compared. Everything that proves «nothing has happened yet /
//   nothing was overwritten» stays a point-in-time read at the same moment as the original:
//   the state while a module is held, the query value after the lifecycle barriers (the
//   rendered-center barrier is the original's `#centros .cl-row`, now a row OR a name on
//   the centers map, CENTER_DRAWN), the URL right after the first failed
//   request, the layout-shift attribution.
// - Locators: ids/classes/data-* stay CSS (#q, #resultados, #stznav, CENTER_DRAWN,
//   #sec-reportes, #list .sol-row, .cmb-*, aside.sol-det): they are the markup contract the
//   delayed-module guards are about.
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  test, expect, ADMIN_USER, ADMIN_PASS, saveShot, artifactPath, holdGate, note,
} from "./lib/fixtures.mjs";
import { installHomeQueryTrace } from "./lib/home-query-trace.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_search_delayed", requests: true } });

const MODULE_NAME = "platform/search.mjs";
const MODULE_PATH = /\/static\/platform\/search\.[a-f0-9]{8}\.mjs$/;
const NAV_PATH = /\/static\/platform\/nav\.[a-f0-9]{8}\.mjs$/;
const CLASSIC = /(?:^|\/)search\.js$/;
const SERVICE_ID = "electrocardiograma-hospital";
const SERVICE_NAME = "Electrocardiograma";
const DET_OPEN = "aside.sol-det[data-id]";
// A center drawn by renderCentros(): a row of the list or a name on the map.
const CENTER_DRAWN = "#centros :is(.cl-row, .cmap-name)";
const HOME_NAV_ARTIFACT_ROOT = artifactPath("human-rounds");

function observe(page, { allowFailedPaths = [] } = {}) {
  const failures = [];
  const requested = [];
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  page.on("requestfailed", (request) => {
    const path = new URL(request.url()).pathname;
    if (path === "/api/account/me" && request.failure()?.errorText === "net::ERR_ABORTED") return;
    if (allowFailedPaths.includes(path) && request.failure()?.errorText === "net::ERR_FAILED") return;
    failures.push({ path, error: request.failure()?.errorText || "request failed" });
  });
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (path === "/api/account/me" && response.status() === 401) return;
    if (response.status() >= 400) failures.push({ path, status: response.status() });
  });
  page.on("pageerror", (error) => failures.push({ pageerror: String(error) }));
  return { failures, requested };
}

async function loginAdmin(page, next = "/admin/solicitudes") {
  await page.goto("/admin/login?next=" + encodeURIComponent(next), {
    waitUntil: "load", timeout: 15000,
  });
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 10000 });
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  await page.waitForURL(new RegExp(next.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), {
    timeout: 10000, waitUntil: "commit",
  });
  await page.waitForLoadState("networkidle");
}

// Hold every request matching `pattern` until `release()`; `getURL()` is the last one seen.
async function holdRoute(page, cleanup, pattern) {
  const gate = holdGate(cleanup);
  let url = "";
  await page.route(pattern, async (route) => {
    url = route.request().url();
    await gate.held;
    await route.continue();
  });
  return { release: gate.release, getURL: () => url };
}

const holdSearch = (page, cleanup) => holdRoute(page, cleanup, "**/static/platform/search.*.mjs");
const holdNav = (page, cleanup) => holdRoute(page, cleanup, "**/static/platform/nav.*.mjs");

// Resolves once the held route has been requested.
async function requestedWithin(held, message, timeout = 20000) {
  await expect.poll(held.getURL, { message, timeout }).not.toBe("");
}

async function installHomeNavShiftObserver(page) {
  await page.addInitScript(() => {
    const label = (node) => {
      if (!node) return "unknown";
      if (node.id) return `#${node.id}`;
      if (node.matches?.("main.wrap")) return "main.wrap";
      return node.tagName?.toLowerCase() || "unknown";
    };
    const rect = (box) => box ? [box.x, box.y, box.width, box.height] : null;
    const supported = Array.isArray(PerformanceObserver.supportedEntryTypes) &&
      PerformanceObserver.supportedEntryTypes.includes("layout-shift");
    window.__homeNavShiftObserverSupported = supported;
    window.__homeNavShiftObserverInstalled = false;
    window.__homeNavShifts = [];
    if (!supported) return;
    const collect = (list) => {
      for (const entry of list.getEntries()) {
        if (!entry.hadRecentInput) {
          window.__homeNavShifts.push({
            value: entry.value,
            time: entry.startTime,
            sources: Array.from(entry.sources || [], (source) => ({
              node: label(source.node),
              previous: rect(source.previousRect),
              current: rect(source.currentRect),
            })),
          });
        }
      }
    };
    const observer = new PerformanceObserver(collect);
    observer.observe({ type: "layout-shift", buffered: true });
    window.__homeNavShiftObserver = observer;
    window.__homeNavDrainShifts = () => {
      collect({ getEntries: () => observer.takeRecords() });
      return window.__homeNavShifts.slice();
    };
    // Only a successfully observed native entry type counts as installed.
    window.__homeNavShiftObserverInstalled = true;
  });
}

async function afterPaint(page) {
  await page.evaluate(() => new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }));
}

async function homeNavGeometry(page) {
  return page.evaluate(() => {
    const rect = (selector) => {
      const box = document.querySelector(selector)?.getBoundingClientRect();
      return box ? { top: box.top, height: box.height } : null;
    };
    return {
      header: rect("#stznav"),
      main: rect("main.wrap"),
      searchbarTop: getComputedStyle(document.querySelector(".searchbar")).top,
      mounted: !!document.querySelector("#stznav .stznav-inner"),
      observerSupported: window.__homeNavShiftObserverSupported === true,
      observerInstalled: window.__homeNavShiftObserverInstalled === true,
      shifts: window.__homeNavDrainShifts ? window.__homeNavDrainShifts() : [],
    };
  });
}

test("home reserves the real nav footprint before its fingerprinted script arrives", async ({ newContext, cleanup }, testInfo) => {
  test.setTimeout(45000);

  mkdirSync(HOME_NAV_ARTIFACT_ROOT, { recursive: true });
  const artifactDir = mkdtempSync(join(HOME_NAV_ARTIFACT_ROOT, "home-cold-nav-"));
  const reportPath = join(artifactDir, "report.json");
  note(testInfo, `Home nav evidence: ${reportPath}`);
  const report = {
    complete: false,
    expectedWidths: [360, 390, 768, 1024, 1280],
    startedAt: new Date().toISOString(),
    widths: [],
  };
  const saveReport = () => writeFileSync(reportPath, JSON.stringify(report, null, 2) + "\n");
  saveReport();

  try {
    for (const width of [360, 390, 768, 1024, 1280]) {
      const proof = {
        width, complete: false, before: null, release: null, after: null,
        request: null, fit: null, screenshots: { before: null, after: null }, error: null,
      };
      report.widths.push(proof);
      saveReport();
      const context = await newContext({ viewport: { width, height: 900 } });
      const page = await context.newPage();
      const seen = observe(page);
      const held = await holdNav(page, cleanup);
      try {
        await installHomeNavShiftObserver(page);
        // nav.js is a real deferred script. Holding its immutable response keeps
        // DOMContentLoaded pending, but the Python-rendered header and home main
        // are already parsed and measurable.
        await page.goto("/?q=Pediatría", { waitUntil: "commit", timeout: 20000 });
        await requestedWithin(held, "fingerprinted nav.js was not requested");
        expect(new URL(held.getURL()).pathname).toMatch(NAV_PATH);

        await expect(page.locator("#q")).toBeAttached({ timeout: 8000 });
        await afterPaint(page);
        const before = await homeNavGeometry(page);
        proof.before = before;
        proof.request = { url: held.getURL(), pathname: new URL(held.getURL()).pathname };
        proof.screenshots.before = join(artifactDir, `${width}-before.png`);
        await saveShot(testInfo, page, proof.screenshots.before, { fullPage: true });
        expect(before.observerSupported, "layout-shift must be supported by this browser").toBe(true);
        expect(before.observerInstalled, "the pre-navigation layout-shift observer must be live").toBe(true);
        expect(before.mounted, "held nav.js must leave the real host unmounted").toBe(false);
        expect(before.header && before.main, "rendered home shell must expose header and main geometry").toBeTruthy();

        proof.release = await page.evaluate(() => ({
          time: performance.now(),
          shiftCount: window.__homeNavDrainShifts ? window.__homeNavDrainShifts().length : 0,
        }));
        held.release();
        await page.waitForLoadState("domcontentloaded", { timeout: 15000 });
        await expect(page.locator("#stznav .stznav-inner")).toBeVisible({ timeout: 10000 });
        await afterPaint(page);
        const after = await homeNavGeometry(page);
        proof.after = after;
        proof.screenshots.after = join(artifactDir, `${width}-after.png`);
        await saveShot(testInfo, page, proof.screenshots.after, { fullPage: true });
        expect(after.mounted, "releasing the exact nav asset must mount the shared controller").toBe(true);
        expect(after.header && after.main, "mounted nav must retain measurable home geometry").toBeTruthy();
        expect(Math.abs(after.header.height - before.header.height),
          `${width}px: nav reservation changed header height ${JSON.stringify({ before, after })}`).toBeLessThanOrEqual(1);
        expect(Math.abs(after.main.top - before.main.top),
          `${width}px: nav insertion moved the home main ${JSON.stringify({ before, after })}`).toBeLessThanOrEqual(1);
        expect(Math.abs(parseFloat(after.searchbarTop) - parseFloat(before.searchbarTop)),
          `${width}px: nav insertion changed sticky search offset ${JSON.stringify({ before, after })}`).toBeLessThanOrEqual(1);
        const navWindowShifts = after.shifts.slice(proof.release.shiftCount).filter((shift) => shift.time >= proof.release.time);
        const navShiftSources = navWindowShifts.filter((shift) =>
          shift.sources.some((source) => source.node === "#stznav" || source.node === "main.wrap" || source.node === "body"));
        expect(navShiftSources,
          `${width}px: the real release-window observer attributed a shift to nav/home flow ${JSON.stringify(navWindowShifts)}`).toEqual([]);
        const fit = await page.evaluate(() => {
          const viewport = document.documentElement.clientWidth;
          const overflowers = Array.from(document.querySelectorAll("*")).filter((el) => {
            const box = el.getBoundingClientRect();
            return box.width > 0 && box.right > viewport + 1;
          }).map((el) => `${el.tagName}.${el.className}`);
          return { page: document.documentElement.scrollWidth - viewport, overflowers };
        });
        proof.fit = fit;
        expect(fit, `${width}px: mounted nav must fit the home viewport`).toEqual({ page: 0, overflowers: [] });
        expect(proof.request.pathname).toMatch(NAV_PATH);
        expect(seen.requested.some((path) => NAV_PATH.test(path)), "actual fingerprinted nav.js must be observed").toBeTruthy();
        expect(seen.failures).toEqual([]);
        proof.complete = true;
      } catch (error) {
        proof.error = String(error?.stack || error);
        throw error;
      } finally {
        held.release();
        proof.requested = seen.requested;
        proof.networkErrors = seen.failures;
        saveReport();
        await context.close();
      }
    }
    report.complete = report.widths.length === report.expectedWidths.length &&
      report.widths.every((proof, index) => proof.width === report.expectedWidths[index] && proof.complete);
  } finally {
    report.finishedAt = new Date().toISOString();
    saveReport();
  }
});

test("home holds the query until native Search arrives, never the retired global or classic script", async ({ page, app, cleanup }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const seen = observe(page);
  const queryTrace = await installHomeQueryTrace(page);
  const held = await holdSearch(page, cleanup);
  try {
    // The deliberately held dynamic import must not leave an unobserved
    // navigation promise that rejects during teardown and masks an assertion.
    await page.goto("/", { waitUntil: "domcontentloaded", timeout: 20000 });
    await requestedWithin(held, `${MODULE_NAME} was not requested from the manifest`);
    expect(new URL(held.getURL()).pathname).toMatch(MODULE_PATH);

    await expect(page.locator("#q")).toBeVisible({ timeout: 8000 });
    expect(await page.evaluate(() => document.readyState)).not.toBe("loading");
    await page.fill("#q", SERVICE_NAME);
    const immediatePostFill = await page.locator("#q").inputValue();

    const whileDelayed = await page.evaluate(() => {
      const results = document.getElementById("resultados");
      return {
        query: document.getElementById("q")?.value || "",
        resultsDisplay: results ? results.style.display : "",
        resultsText: (results && results.innerText) || "",
        empty: !!results?.querySelector(".empty"),
        legacyGlobal: typeof window.SanaSearch,
        manifestURL: window.SANA_STATIC?.["platform/search.mjs"] || "",
      };
    });
    if (whileDelayed.query !== SERVICE_NAME) {
      expect(whileDelayed.query, JSON.stringify({
        immediatePostFill,
        trace: await queryTrace.compact(),
      })).toBe(SERVICE_NAME);
    }
    expect(whileDelayed.empty, "home must not paint the empty-state lie while Search is held").toBe(false);
    expect(whileDelayed.resultsText.trim()).toBe("");
    expect(whileDelayed.legacyGlobal).toBe("undefined");
    expect(whileDelayed.manifestURL).toMatch(MODULE_PATH);

    held.release();

    const nameLink = page.locator(`#resultados a[href="/service/${SERVICE_ID}/book"]`).first();
    await expect(nameLink).toBeVisible({ timeout: 15000 });
    await expect(nameLink).toHaveText(SERVICE_NAME);

    const after = await page.evaluate(() => ({
      query: document.getElementById("q")?.value || "",
      resultsText: document.getElementById("resultados")?.innerText || "",
      legacyGlobal: typeof window.SanaSearch,
      replacementGlobal: typeof window.Search,
      manifestURL: window.SANA_STATIC?.["platform/search.mjs"] || "",
    }));
    expect(after.query, "the query typed before release must survive").toBe(SERVICE_NAME);
    expect(after.resultsText).toMatch(/Electrocardiograma/);
    expect(after.legacyGlobal).toBe("undefined");
    expect(after.replacementGlobal, "native Search must remain local, not a renamed window namespace").toBe("undefined");
    expect(after.manifestURL).toMatch(MODULE_PATH);
    expect(new URL(held.getURL()).pathname).toMatch(MODULE_PATH);
    expect(new URL(held.getURL()).pathname).toBe(new URL(after.manifestURL, app.baseUrl).pathname);
    expect(seen.requested.some((path) => MODULE_PATH.test(path))).toBeTruthy();
    expect(
      seen.requested.some((path) => CLASSIC.test(path)),
      `retired classic search script was requested: ${seen.requested.filter((path) => CLASSIC.test(path))}`,
    ).toBe(false);
    expect(seen.failures).toEqual([]);
    const evidence = artifactPath("human-rounds", "astra-search-responsive");
    mkdirSync(evidence, { recursive: true });
    for (const width of [360, 390, 768, 834, 1024, 1280, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(nameLink).toBeVisible({ timeout: 15000 });
      expect(await page.locator("#q").inputValue()).toBe(SERVICE_NAME);
      const overflow = await page.evaluate(() => {
        const viewport = document.documentElement.clientWidth;
        const elements = Array.from(document.querySelectorAll("*")).filter((el) => {
          const box = el.getBoundingClientRect();
          return box.width > 0 && box.right > viewport + 1;
        }).map((el) => `${el.tagName}.${el.className}`);
        return { page: document.documentElement.scrollWidth - viewport, elements };
      });
      expect(overflow, `search overflow at ${width}px`).toEqual({ page: 0, elements: [] });
      if (width === 390 || width === 1280) {
        await saveShot(testInfo, page, join(evidence, `search-${width}.png`), { fullPage: true });
      }
    }
    expect(seen.failures).toEqual([]);
  } finally {
    held.release();
    queryTrace.dispose();
  }
});

test("a URL fallback never overwrites a query typed or cleared before delayed i18n boot", async ({ newContext, cleanup }) => {
  test.setTimeout(45000);

  for (const [name, typed, expected] of [
    ["replacement", SERVICE_NAME, SERVICE_NAME],
    ["explicit clear", "", ""],
  ]) {
    const context = await newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    const seen = observe(page);
    const heldSearch = await holdSearch(page, cleanup);
    const heldI18n = await holdRoute(page, cleanup, "**/static/platform/i18n.*.mjs");
    const heldServices = await holdRoute(page, cleanup, "**/api/services");
    await page.goto("/?q=seed", { waitUntil: "domcontentloaded", timeout: 20000 });
    await requestedWithin(heldSearch, `${MODULE_NAME} was not requested from the manifest`);
    await requestedWithin(heldI18n, "platform/i18n.mjs was not requested");

    const query = page.locator("#q");
    await query.fill(SERVICE_NAME);
    if (!typed) await query.fill("");
    expect(await query.inputValue(), `${name}: user intent was recorded before i18n`).toBe(expected);

    // The services request is dispatched only after i18n continuation has
    // run through its URL fallback assignment. It is a lifecycle barrier,
    // not a timing sleep.
    heldI18n.release();
    await requestedWithin(heldServices, "/api/services was not requested after i18n");
    heldServices.release();
    // A rendered center is produced by renderCentros(), which is called
    // before the later catalog fallback/replay block. This proves the full
    // Promise.all continuation ran; a response alone would not.
    await expect(page.locator(CENTER_DRAWN).first()).toBeVisible({ timeout: 15000 });
    expect(await query.inputValue(), `${name}: delayed i18n/catalog did not restore seed`).toBe(expected);
    expect(await page.locator("#resultados").innerText()).toBe("");

    heldSearch.release();
    if (typed) {
      await expect(page.locator(`#resultados a[href="/service/${SERVICE_ID}/book"]`).first())
        .toBeVisible({ timeout: 15000 });
    }
    expect(await query.inputValue(), `${name}: native Search retained intent`).toBe(expected);
    expect(new URL(page.url()).searchParams.get("q") || "",
      `${name}: URL matches visible query once the deferred modules are ready`).toBe(expected);
    await page.reload({ waitUntil: "domcontentloaded", timeout: 20000 });
    if (typed) {
      await expect(page.locator(`#resultados a[href="/service/${SERVICE_ID}/book"]`).first())
        .toBeVisible({ timeout: 15000 });
    } else {
      await expect(page.locator(CENTER_DRAWN).first()).toBeVisible({ timeout: 15000 });
    }
    expect(await page.locator("#q").inputValue(),
      `${name}: reload does not resurrect the original URL fallback`).toBe(expected);
    expect(new URL(page.url()).searchParams.get("q") || "",
      `${name}: reload URL still matches visible intent`).toBe(expected);
    expect(seen.failures).toEqual([]);
    await context.close();
  }
});

test("early home-query intent updates the URL even when either catalog dependency fails", async ({ newContext, cleanup }) => {
  test.setTimeout(45000);

  const assertOtherUrlState = (page) => {
    expect(new URL(page.url()).searchParams.get("keep")).toBe("1");
    expect(new URL(page.url()).hash).toBe("#stay");
  };

  for (const failedPath of ["/api/services", "/api/centers"]) {
    for (const [name, expected] of [
      ["replacement", SERVICE_NAME],
      ["explicit clear", ""],
    ]) {
      const context = await newContext({ viewport: { width: 1280, height: 900 } });
      const page = await context.newPage();
      const seen = observe(page, { allowFailedPaths: [failedPath] });
      const heldI18n = await holdRoute(page, cleanup, "**/static/platform/i18n.*.mjs");
      await page.route(`**${failedPath}`, async (route) => route.abort("failed"));
      await page.goto("/?q=seed&keep=1#stay", { waitUntil: "domcontentloaded", timeout: 20000 });
      await requestedWithin(heldI18n, "platform/i18n.mjs was not requested");

      const query = page.locator("#q");
      await query.fill(SERVICE_NAME);
      if (!expected) await query.fill("");
      expect(await query.inputValue(), `${failedPath}/${name}: early edit is visible`).toBe(expected);

      // The rejected request is the real Promise.all dependency, not a mocked
      // end state. It proves the normal post-catalog replay never succeeds.
      const firstFailure = page.waitForEvent("requestfailed", (request) =>
        new URL(request.url()).pathname === failedPath);
      heldI18n.release();
      await firstFailure;
      expect(new URL(page.url()).searchParams.get("q") || "",
        `${failedPath}/${name}: URL records intent before catalog success`).toBe(expected);
      assertOtherUrlState(page);

      // A second rejected dependency after reload proves an old ?q=seed cannot
      // repopulate the field when the catalogs still never reach replayQuery.
      const reloadFailure = page.waitForEvent("requestfailed", (request) =>
        new URL(request.url()).pathname === failedPath);
      await page.reload({ waitUntil: "domcontentloaded", timeout: 20000 });
      await reloadFailure;
      expect(await page.locator("#q").inputValue(),
        `${failedPath}/${name}: reload preserves visible intent without catalog success`).toBe(expected);
      expect(new URL(page.url()).searchParams.get("q") || "",
        `${failedPath}/${name}: reload URL preserves intent`).toBe(expected);
      assertOtherUrlState(page);
      expect(seen.failures).toEqual([]);
      await context.close();
    }
  }
});

test("reports wait for native Search with charts, then cluster the search view", async ({ page, app, cleanup }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await loginAdmin(page, "/admin/reportes");
  const held = await holdSearch(page, cleanup);
  try {
    const seen = observe(page);
    await page.goto("/admin/reportes", { waitUntil: "domcontentloaded", timeout: 20000 });
    await requestedWithin(held, `${MODULE_NAME} was not requested from the manifest`);
    expect(new URL(held.getURL()).pathname).toMatch(MODULE_PATH);

    await expect(page.locator("#sec-reportes")).toBeAttached({ timeout: 5000 });
    const whileDelayed = await page.evaluate(() => ({
      cards: document.querySelectorAll("#sec-reportes .rep-sumcard").length,
      chartsGlobal: typeof window.SanaCharts,
      searchGlobal: typeof window.SanaSearch,
      chartsURL: window.SANA_STATIC?.["platform/admin-charts.mjs"] || "",
      searchURL: window.SANA_STATIC?.["platform/search.mjs"] || "",
    }));
    expect(whileDelayed.cards, "report cards must not paint before searchReady resolves").toBe(0);
    expect(whileDelayed.chartsGlobal).toBe("undefined");
    expect(whileDelayed.searchGlobal).toBe("undefined");
    expect(whileDelayed.chartsURL).toMatch(/\/static\/platform\/admin-charts\.[a-f0-9]{8}\.mjs$/);
    expect(whileDelayed.searchURL).toMatch(MODULE_PATH);

    // Seed through the actual HTTP endpoint outside the observed browser
    // navigation. A 204 telemetry fetch in the page can be cancelled by the
    // next document even after its headers resolve; that is fixture setup,
    // not report loading. No application request or page error is filtered.
    const seeded = await page.request.post(app.baseUrl + "/api/search-log", {
      data: { q: "psiquiatra", resultados: 4, origen: "e2e-search-delayed" },
    });
    expect(seeded.status()).toBe(204);

    held.release();
    await expect(page.locator("#sec-reportes .rep-sumcard").first()).toBeVisible({ timeout: 15000 });
    await page.waitForLoadState("networkidle");

    await page.goto("/admin/reportes?view=busquedas", {
      waitUntil: "load", timeout: 20000,
    });
    await page.waitForFunction(() => /view=busquedas/.test(location.search), null, { timeout: 5000 });
    // #rep-body says «Cargando…» while the first answer is in flight; wait
    // for the search view's own render to replace it.
    await expect(page.locator("#rep-body")).toBeVisible({ timeout: 15000 });
    await expect(page.locator("#rep-body")).not.toContainText("Cargando…", { timeout: 15000 });
    const busquedas = await page.evaluate(() => {
      const body = document.getElementById("rep-body");
      const text = (body && body.innerText) || "";
      return {
        text,
        hasSvg: !!body?.querySelector("svg"),
        searchGlobal: typeof window.SanaSearch,
        searchURL: window.SANA_STATIC?.["platform/search.mjs"] || "",
      };
    });
    expect(busquedas.text).toMatch(/Psiquiatría/);
    expect(busquedas.text).toMatch(/psiquiatra/i);
    expect(/Sin datos todavía/.test(busquedas.text),
      "search view must cluster the logged query, not pass on an empty-state or SVG").toBe(false);
    expect(busquedas.searchGlobal).toBe("undefined");
    expect(busquedas.searchURL).toMatch(MODULE_PATH);
    expect(seen.requested.some((path) => MODULE_PATH.test(path))).toBeTruthy();
    expect(seen.requested.some((path) => CLASSIC.test(path))).toBe(false);
    expect(seen.failures).toEqual([]);
  } finally {
    held.release();
  }
});

test("Derivar combo waits for native Search ranking and does not substring-degrade while held", async ({ page, cleanup }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await loginAdmin(page, "/admin/solicitudes");
  const seen = observe(page);
  const held = await holdSearch(page, cleanup);
  try {
    await page.goto("/admin/solicitudes", { waitUntil: "domcontentloaded", timeout: 20000 });
    await requestedWithin(held, `${MODULE_NAME} was not requested from the manifest`);
    expect(new URL(held.getURL()).pathname).toMatch(MODULE_PATH);

    const whileDelayed = await page.evaluate(() => ({
      rows: document.querySelectorAll("#list .sol-row").length,
      combos: document.querySelectorAll(".cmb-in").length,
      searchGlobal: typeof window.SanaSearch,
      searchURL: window.SANA_STATIC?.["platform/search.mjs"] || "",
    }));
    expect(whileDelayed.rows, "queue must not paint before combo's Search TLA resolves").toBe(0);
    expect(whileDelayed.combos, "combo must not substring-degrade while Search is held").toBe(0);
    expect(whileDelayed.searchGlobal).toBe("undefined");
    expect(whileDelayed.searchURL).toMatch(MODULE_PATH);

    held.release();
    await expect.poll(() => page.locator("#list .sol-row").count(), { timeout: 15000 }).toBeGreaterThanOrEqual(1);

    await page.locator("#list .sol-row").first().click();
    await expect(page.locator(DET_OPEN)).toBeVisible({ timeout: 8000 });
    const edit = page.locator(`${DET_OPEN} .svc-edit`);
    if (await edit.isHidden()) {
      await page.locator(`${DET_OPEN} [data-toggle-service]`).click();
    }
    const combo = page.locator(`${DET_OPEN} .svc-edit .cmb-in`);
    await expect(combo).toBeVisible({ timeout: 5000 });
    await combo.fill("psiquiatra");
    const choice = page.locator(`${DET_OPEN} .cmb-it`, { hasText: "Psiquiatría" }).first();
    await expect(choice).toBeVisible({ timeout: 8000 });
    const labels = await page.locator(`${DET_OPEN} .cmb-list .cmb-it`).allTextContents();
    expect(labels.some((label) => /Psiquiatría/i.test(label)),
      `expected Psiquiatría from native stemming, got ${JSON.stringify(labels)}`).toBeTruthy();
    expect(labels.some((label) => /Psicología/i.test(label)),
      "psiquiatra must not substring-match Psicología").toBe(false);
  } finally {
    held.release();
  }
  expect(seen.requested.some((path) => MODULE_PATH.test(path))).toBeTruthy();
  expect(seen.requested.some((path) => CLASSIC.test(path))).toBe(false);
  expect(seen.failures).toEqual([]);
});
