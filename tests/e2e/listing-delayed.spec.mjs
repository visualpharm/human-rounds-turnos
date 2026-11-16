// Delayed native listing module on home and server-mounted catalogs.
// A source scan cannot prove callers wait for listingReady / type=module
// auto-mount before painting tables, logging, or the empty-state lie.
//
// Port of tests/e2e/listing-delayed.e2e.mjs (3 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app is the worker-scoped `app` fixture (`requests: true`, same dbName); the page
//   is the runner's `page` with the original 1280x900 viewport (`test.use`).
// - The held module request uses `holdGate` (released on cleanup too, so a failing test
//   never leaves the route hanging) instead of the file-local `deferred()`. The
//   «module was not requested from the manifest» guard (a hand-made 20 s timer raced
//   against the route) is `expect.poll` on the URL the route saw, with the same message
//   and 20 s. The `assert.match(pathname, MODULE_PATH)` that ran INSIDE the route handler
//   (where a throw would be an unhandled rejection) runs in the test body, right after
//   the poll and before the module is released.
// - Evidence trace: the original wrapped the home test in `context.tracing.start(...)` /
//   `stop({ path: trace.zip })`. The runner owns the context's tracing (trace on first
//   retry), and a second `tracing.start` on a context that is already being traced
//   throws. The port starts the evidence trace only when the context is not already being
//   recorded (`startEvidenceTrace`) and otherwise leaves the runner's own trace of that
//   retry, which is attached to the report, as the evidence. `input.json` is written as
//   before and also attached. The evidence directory comes from `artifactPath` (the
//   original hard-coded `os.homedir()/.claude/artifacts`, the same default).
// - Waits and assertions are web-first (`expect(locator)...` with the original explicit
//   timeouts) where the original called `waitFor` and then compared. The point-in-time
//   snapshots that ARE the claim stay `page.evaluate` snapshots: everything read while the
//   module is held (`whileDelayed`), the typeof-global checks, the overflow measurement
//   and the `seen.failures` lists.
// - The file is chosen once, as soon as `#svc-title` is visible, exactly as in the original (no re-delivery
//   helper: the wizard re-rendering its upload step while it boots used to lose a file set on the replaced
//   input, which made this test float; cabc540d fixed that in the product and
//   booking-upload-chooser.spec.mjs pins it).
// - Locators: ids/classes/data-* stay CSS (#q, #resultados, #wz-file, #prep-turnos,
//   #medicos-tabla, `a[href="/service/.../book"]`) because the checks are about the
//   markup contract and computed state; texts use `toHaveText`.
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import {
  test, expect, ROOT, holdGate, artifactPath,
} from "./lib/fixtures.mjs";
import { installHomeQueryTrace } from "./lib/home-query-trace.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_listing_delayed", requests: true },
  viewport: { width: 1280, height: 900 },
});

const MODULE_NAME = "platform/listing.mjs";
const MODULE_PATH = /\/static\/platform\/listing\.[a-f0-9]{8}\.mjs$/;
const CLASSIC = /(?:^|\/)listing\.js$/;
const SERVICE_ID = "electrocardiograma-hospital";
const SERVICE_NAME = "Electrocardiograma";

function observe(page, { expectedFailed = [] } = {}) {
  const failures = [];
  const requested = [];
  const searchLogs = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    requested.push(url.pathname);
    if (url.pathname === "/api/search-log" && request.method() === "POST") {
      try { searchLogs.push(JSON.parse(request.postData() || "{}")); }
      catch { searchLogs.push({ raw: request.postData() }); }
    }
  });
  page.on("requestfailed", (request) => {
    const path = new URL(request.url()).pathname;
    if (path === "/api/account/me" && request.failure()?.errorText === "net::ERR_ABORTED") return;
    if (expectedFailed.some((pattern) => pattern.test(path))) return;
    failures.push({ path, error: request.failure()?.errorText || "request failed" });
  });
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (path === "/api/account/me" && response.status() === 401) return;
    if (response.status() >= 400) failures.push({ path, status: response.status() });
  });
  page.on("pageerror", (error) => failures.push({ pageerror: String(error) }));
  return { failures, requested, searchLogs };
}

async function holdListing(page, cleanup) {
  const gate = holdGate(cleanup);
  let moduleURL = "";
  await page.route("**/static/platform/listing.*.mjs", async (route) => {
    moduleURL = route.request().url();
    await gate.held;
    await route.continue();
  });
  return { releaseModule: gate.release, getURL: () => moduleURL };
}

// The evidence trace of the home test. The runner records its own trace of a retried
// test on the same context; starting a second one throws, and the runner's trace is then
// the evidence.
async function startEvidenceTrace(context) {
  try {
    await context.tracing.start({ screenshots: true, snapshots: true });
    return true;
  } catch {
    return false;
  }
}

test("home holds the query until native Listing arrives, never the retired global or classic script", async ({ page, app, cleanup }, testInfo) => {
  const seen = observe(page);
  const held = await holdListing(page, cleanup);
  const queryTrace = await installHomeQueryTrace(page);
  const evidence = artifactPath("human-rounds", "home-query-diagnostic", `run-${Date.now()}-${process.pid}`);
  await mkdir(evidence, { recursive: true });
  const tracing = await startEvidenceTrace(page.context());
  try {
    // First paint is usable before the deliberately held dynamic import.
    // Await the navigation boundary so teardown cannot hide a failed assertion
    // behind a dangling page.goto rejection.
    await page.goto("/", { waitUntil: "domcontentloaded", timeout: 20000 });
    await expect.poll(held.getURL, {
      message: `${MODULE_NAME} was not requested from the manifest`, timeout: 20000,
    }).not.toBe("");
    expect(new URL(held.getURL()).pathname).toMatch(MODULE_PATH);

    await expect(page.locator("#q")).toBeVisible({ timeout: 8000 });
    expect(await page.evaluate(() => document.readyState)).not.toBe("loading");
    await page.fill("#q", SERVICE_NAME);

    const whileDelayed = await page.evaluate(() => {
      const results = document.getElementById("resultados");
      const query = document.getElementById("q");
      const rect = query.getBoundingClientRect();
      const atPoint = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
      return {
        query: query.value,
        inputState: { connected: query.isConnected, disabled: query.disabled,
          readOnly: query.readOnly, type: query.type, focused: document.activeElement === query,
          activeId: document.activeElement?.id, atPointId: atPoint?.id,
          rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height } },
        resultsDisplay: results ? results.style.display : "",
        resultsText: (results && results.innerText) || "",
        empty: !!results?.querySelector(".empty"),
        legacyGlobal: typeof window.SanaListing,
        replacementGlobal: typeof window.Listing,
        manifestURL: window.SANA_STATIC?.["platform/listing.mjs"] || "",
        searchReady: typeof window.SANA_STATIC?.["platform/search.mjs"] === "string",
      };
    });
    const evidenceBody = JSON.stringify({
      whileDelayed, locatorValue: await page.locator("#q").inputValue(), trace: await queryTrace.compact(),
    }, null, 2);
    await writeFile(path.join(evidence, "input.json"), evidenceBody);
    await testInfo.attach("held-listing-query-evidence.json", { body: evidenceBody, contentType: "application/json" });
    expect(whileDelayed.query).toBe(SERVICE_NAME);
    expect(whileDelayed.empty, "home must not paint the empty-state lie while Listing is held").toBe(false);
    expect(whileDelayed.resultsText.trim()).toBe("");
    expect(whileDelayed.legacyGlobal).toBe("undefined");
    expect(whileDelayed.replacementGlobal).toBe("undefined");
    expect(whileDelayed.manifestURL).toMatch(MODULE_PATH);
    expect(whileDelayed.searchReady, "Search must remain independently importable while Listing is held").toBe(true);

    held.releaseModule();

    const nameLink = page.locator(`#resultados a[href="/service/${SERVICE_ID}/book"]`).first();
    await expect(nameLink).toBeVisible({ timeout: 15000 });
    await expect(nameLink).toHaveText(SERVICE_NAME);

    if (!seen.searchLogs.some((row) => row && row.q === SERVICE_NAME)) {
      const logged = page.waitForRequest((request) => {
        return new URL(request.url()).pathname === "/api/search-log" && request.method() === "POST";
      }, { timeout: 5000 });
      await page.locator("#q").press("Enter");
      await logged;
    }

    const after = await page.evaluate(() => ({
      query: document.getElementById("q")?.value || "",
      resultsText: document.getElementById("resultados")?.innerText || "",
      legacyGlobal: typeof window.SanaListing,
      replacementGlobal: typeof window.Listing,
      manifestURL: window.SANA_STATIC?.["platform/listing.mjs"] || "",
      rawKeys: /(?:^|\s)listing\.[a-z0-9_.]+(?:\s|$)/.test(document.body.innerText || ""),
    }));
    expect(after.query, "the query typed before release must survive").toBe(SERVICE_NAME);
    expect(after.resultsText).toMatch(/Electrocardiograma/);
    expect(after.rawKeys, "Spanish first paint must not show unresolved listing.* keys").toBe(false);
    expect(after.legacyGlobal).toBe("undefined");
    expect(after.replacementGlobal, "native Listing must remain local, not a renamed window namespace").toBe("undefined");
    expect(after.manifestURL).toMatch(MODULE_PATH);
    expect(new URL(held.getURL()).pathname).toMatch(MODULE_PATH);
    expect(new URL(held.getURL()).pathname).toBe(new URL(after.manifestURL, app.baseUrl).pathname);
    expect(seen.requested.some((path) => MODULE_PATH.test(path))).toBeTruthy();
    expect(
      seen.requested.some((path) => CLASSIC.test(path)),
      `retired classic listing script was requested: ${seen.requested.filter((path) => CLASSIC.test(path))}`,
    ).toBe(false);
    expect(
      seen.searchLogs.some((row) => row && row.q === SERVICE_NAME),
      `native Listing.logSearch must POST /api/search-log, got ${JSON.stringify(seen.searchLogs)}`,
    ).toBe(true);
    expect(seen.failures).toEqual([]);

    await page.setViewportSize({ width: 390, height: 844 });
    // This test starts desktop-sized to exercise delayed module loading, then
    // changes viewport.  The production navigation deliberately remeasures on
    // resize after its short debounce; wait for that real layout pass before
    // making the page-wide mobile assertion.
    await page.waitForFunction(() => {
      const nav = document.getElementById("stznav");
      return !nav || nav.classList.contains("stznav--compact") || nav.classList.contains("stznav--collapsed");
    }, null, { timeout: 3000 });
    await page.locator("#q").click();
    const overflow = await page.evaluate(() => {
      const viewport = document.documentElement.clientWidth;
      const elements = Array.from(document.querySelectorAll("*")).filter((el) => {
        const box = el.getBoundingClientRect();
        return box.width > 0 && box.right > viewport + 1;
      }).map((el) => `${el.tagName}.${el.className}`);
      return { page: document.documentElement.scrollWidth - viewport, elements };
    });
    expect(overflow, "listing overflow at 390px").toEqual({ page: 0, elements: [] });
    expect(seen.failures).toEqual([]);

    // A complete document reload must import the emitted native module again,
    // preserve the query URL contract, and never revive a compatibility global.
    await page.reload({ waitUntil: "load", timeout: 20000 });
    await expect(page.locator(`#resultados a[href="/service/${SERVICE_ID}/book"]`).first())
      .toBeVisible({ timeout: 15000 });
    const afterReload = await page.evaluate(() => ({
      query: document.getElementById("q")?.value || "",
      legacyGlobal: typeof window.SanaListing,
      replacementGlobal: typeof window.Listing,
    }));
    expect(afterReload.query).toBe(SERVICE_NAME);
    expect(afterReload.legacyGlobal).toBe("undefined");
    expect(afterReload.replacementGlobal).toBe("undefined");
    expect(seen.failures).toEqual([]);
  } finally {
    if (tracing) await page.context().tracing.stop({ path: path.join(evidence, "trace.zip") });
    queryTrace.dispose();
    held.releaseModule();
  }
});

test("native Listing rejection leaves a visible home recovery and Upload Order does not strand its real result spinner", async ({ page }) => {
  const seen = observe(page, { expectedFailed: [MODULE_PATH] });
  await page.route("**/static/platform/listing.*.mjs", (route) => route.abort("failed"));
  await page.goto(`/?q=${encodeURIComponent(SERVICE_NAME)}`, {
    waitUntil: "load", timeout: 20000,
  });
  const homeRecovery = page.locator("#resultados .empty");
  await expect(homeRecovery).toBeVisible({ timeout: 15000 });
  await expect(homeRecovery).toHaveText(/No pudimos buscar los turnos ahora\. Recargá/);
  expect(await page.evaluate(() => typeof window.SanaListing)).toBe("undefined");
  expect(seen.requested.some((pathname) => CLASSIC.test(pathname))).toBe(false);
  await page.locator("#q").fill("");
  await page.waitForFunction(() => {
    const results = document.getElementById("resultados");
    const directory = document.getElementById("directorio-host");
    const centers = document.getElementById("centros-sec");
    return results?.style.display === "none" && directory?.style.display !== "none" && centers?.style.display !== "none";
  }, null, { timeout: 5000 });

  await page.goto(`/upload-order?service=${SERVICE_ID}`, {
    waitUntil: "load", timeout: 20000,
  });
  await page.locator("#wz-file").setInputFiles(path.join(
    ROOT, "packs/example-town/ordenes-demo/bad-handwriting-cardiologia-1.jpg",
  ));
  const uploadRecovery = page.locator("#prep-turnos .prep-noreconocido");
  await expect(uploadRecovery).toBeVisible({ timeout: 20000 });
  await expect(uploadRecovery).toHaveText(/No pudimos armar la lista de turnos ahora/);
  expect(await page.locator("#prep-turnos .wz-spin").count(),
    "a rejected Listing import must settle the visible loading state").toBe(0);
  expect(await page.evaluate(() => typeof window.SanaListing)).toBe("undefined");
  expect(seen.requested.some((pathname) => CLASSIC.test(pathname))).toBe(false);
  expect(seen.failures).toEqual([]);

  // Removing the deliberate transport fault makes a genuine reload usable;
  // no rejected-module cache or root-global fallback may survive it.
  await page.unroute("**/static/platform/listing.*.mjs");
  await page.goto(`/?q=${encodeURIComponent(SERVICE_NAME)}`, {
    waitUntil: "load", timeout: 20000,
  });
  await expect(page.locator(`#resultados a[href="/service/${SERVICE_ID}/book"]`).first())
    .toBeVisible({ timeout: 15000 });
  expect(await page.evaluate(() => typeof window.SanaListing)).toBe("undefined");
  expect(seen.failures).toEqual([]);
});

test("server-mounted /doctors auto-loads native Listing as type=module, never the classic script", async ({ page }) => {
  const seen = observe(page);
  await page.goto("/doctors", { waitUntil: "load", timeout: 20000 });
  await expect(page.locator("#medicos-tabla table").first()).toBeAttached({ timeout: 15000 });

  const mounted = await page.evaluate(() => {
    const scripts = [...document.querySelectorAll('script[type="module"]')]
      .map((el) => el.getAttribute("src") || "");
    return {
      listingSrc: scripts.find((src) => /\/static\/platform\/listing\.[a-f0-9]{8}\.mjs$/.test(src)) || "",
      classic: scripts.some((src) => /(?:^|\/)listing\.js(?:\?|$)/.test(src)),
      legacyGlobal: typeof window.SanaListing,
      replacementGlobal: typeof window.Listing,
      rows: document.querySelectorAll("#medicos-tabla table tbody tr").length,
      rawKeys: /(?:^|\s)listing\.[a-z0-9_.]+(?:\s|$)/.test(document.body.innerText || ""),
    };
  });
  expect(mounted.listingSrc).toMatch(MODULE_PATH);
  expect(mounted.classic).toBe(false);
  expect(mounted.legacyGlobal).toBe("undefined");
  expect(mounted.replacementGlobal).toBe("undefined");
  expect(mounted.rows, "server-mounted listing must draw rows after the native module evaluates").toBeGreaterThanOrEqual(1);
  expect(mounted.rawKeys).toBe(false);
  expect(seen.requested.some((path) => MODULE_PATH.test(path))).toBeTruthy();
  expect(seen.requested.some((path) => CLASSIC.test(path))).toBe(false);
  expect(seen.failures).toEqual([]);
});
