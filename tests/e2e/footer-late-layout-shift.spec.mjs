// A <main class="wrap"> is a flex column from the first byte, not from the
// moment the parser reaches the footer. With a slow connection the page
// arrives in two pieces and the second starts at the footer; the wizard's
// breadcrumb, title and steps must not move when it lands (human-rounds.css:
// the footer stays pinned to the bottom edge, the shift is the bug).
// Deterministic: a proxy sends the page up to <footer and the rest 800 ms later.
//
// Port of tests/e2e/footer-late-layout-shift.e2e.mjs (4 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the
//   original dbName); there is no requireOk()/skip path. The delaying proxy is the `serve`
//   fixture (closed after the test) instead of a hand-made http.Server with try/finally
//   `proxy.close()`; the page and its viewport come from the runner
//   (`page.setViewportSize()` before the first goto) instead of a context per test.
// - The 800 ms delay of the page tail IS the scenario (a slow connection) and stays a timer in
//   the proxy. The test's own fixed 500 ms pause («after the footer is in, wait, then read
//   the layout-shift list») is now `holdsFor`: the list of shifts is asserted empty
//   continuously for 500 ms after the footer is attached, so a late shift fails at the moment it
//   happens instead of only if it happens to be present at the single look.
// - The wait for `footer.site` and the navigation are web-first (`toBeAttached` 10 s,
//   goto 30 s as before). The shift list read stays an evaluate snapshot.
// - Locators: `footer.site` stays CSS - the footer element is the subject of the scenario.
//
// The booking placeholder (`svc-form-host--schedule-boot`) is also measured against a LATE
// service module: the page's own CSS gives it its geometry from the first paint, so when the
// module lands (its stylesheet first, the slots a moment after) the host, the support block and
// the footer stay exactly where they were. The module and the slots response are held by gates
// (no timers): the box is read before the module, after its stylesheet is in, and for a stretch
// while the placeholder is still showing.
import { test, expect, holdsFor, holdGate, saveShot, artifactPath } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_footer_late_shift" } });

const SERVICE_ID = "medicina-general-riverside-clinic";
const VIEWPORTS = [
  { width: 360, height: 640 },
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1280, height: 900 },
];
const LATE_MS = 800;

function lateFooterProxy(upstream) {
  return async (req, res) => {
    const upstreamResponse = await fetch(upstream + req.url, { headers: { accept: req.headers.accept || "*/*" } });
    const body = Buffer.from(await upstreamResponse.arrayBuffer());
    const headers = {};
    for (const [name, value] of upstreamResponse.headers) {
      if (!["content-encoding", "content-length", "transfer-encoding"].includes(name)) headers[name] = value;
    }
    res.writeHead(upstreamResponse.status, headers);
    const html = (upstreamResponse.headers.get("content-type") || "").includes("text/html") ? body.toString("utf8") : null;
    const cut = html === null ? -1 : html.indexOf("<footer");
    if (cut < 0) {
      res.end(body);
      return;
    }
    res.write(html.slice(0, cut));
    setTimeout(() => res.end(html.slice(cut)), LATE_MS);
  };
}

for (const viewport of VIEWPORTS) {
  test(`the booking wizard does not move when the footer arrives late at ${viewport.width}px`, async ({ page, app, serve, cleanup }) => {
    const proxy = await serve(lateFooterProxy(app.baseUrl));
    // The scenario is the footer alone: the service module (it swaps the 600px placeholder for
    // the real wizard, a legitimate height change) stays held so it cannot race the 800 ms.
    const module = holdGate(cleanup);
    await page.route(/\/static\/modules\/turnos\/service\.[a-f0-9]+\.mjs/, async (route) => {
      await module.held;
      await route.continue();
    });
    await page.setViewportSize(viewport);
    await page.addInitScript(() => {
      window.__lateFooterShifts = [];
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (entry.hadRecentInput) continue;
          window.__lateFooterShifts.push({
            value: entry.value,
            moved: Array.from(entry.sources || [], (source) => ({
              node: source.node?.id ? `#${source.node.id}` : source.node?.tagName?.toLowerCase() || "?",
              dy: Math.round(source.currentRect.top - source.previousRect.top),
            })),
          });
        }
      }).observe({ type: "layout-shift", buffered: true });
    });
    await page.goto(`${proxy.url}/service/${SERVICE_ID}/book`, {
      waitUntil: "load",
      timeout: 30_000,
    });
    await expect(page.locator("footer.site")).toBeAttached({ timeout: 10_000 });
    await holdsFor(async () => {
      const shifts = await page.evaluate(() => window.__lateFooterShifts);
      expect(shifts, "the late footer moved content: " + JSON.stringify(shifts)).toEqual([]);
    }, { during: 500 });
  });
}

const PLACEHOLDER_VIEWPORTS = [
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1280, height: 900 },
];
const SHOT_DIR = artifactPath("human-rounds", "placeholder-shift");

async function placeholderBox(page) {
  return page.evaluate(() => {
    const top = (element) => (element ? Math.round(element.getBoundingClientRect().top) : null);
    const host = document.getElementById("svc-form-host");
    return {
      hostTop: top(host),
      hostHeight: host ? Math.round(host.getBoundingClientRect().height) : null,
      moduleCss: !!document.getElementById("wz-style"),
    };
  });
}

for (const viewport of PLACEHOLDER_VIEWPORTS) {
  test(`the booking placeholder keeps its box when the service module lands late at ${viewport.width}px`, async ({ page, app, serve, cleanup }, testInfo) => {
    const proxy = await serve(lateFooterProxy(app.baseUrl));
    await page.setViewportSize(viewport);
    const module = holdGate(cleanup);
    const slots = holdGate(cleanup);
    await page.route(/\/static\/modules\/turnos\/service\.[a-f0-9]+\.mjs/, async (route) => {
      await module.held;
      await route.continue();
    });
    await page.route(`**/api/service-slots/${SERVICE_ID}*`, async (route) => {
      await slots.held;
      await route.continue();
    });
    await page.addInitScript(() => {
      window.__placeholderShifts = [];
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (entry.hadRecentInput) continue;
          window.__placeholderShifts.push({
            value: entry.value,
            moved: Array.from(entry.sources || [], (source) => ({
              node: source.node?.id ? `#${source.node.id}` : source.node?.className || source.node?.tagName?.toLowerCase() || "?",
              dy: Math.round(source.currentRect.top - source.previousRect.top),
            })),
          });
        }
      }).observe({ type: "layout-shift", buffered: true });
    });
    await page.goto(`${proxy.url}/service/${SERVICE_ID}/book`, { waitUntil: "domcontentloaded", timeout: 30_000 });
    const host = page.locator("#svc-form-host");
    await expect(page.locator("footer.site")).toBeAttached({ timeout: 10_000 });
    await expect(host).toHaveClass(/svc-form-host--schedule-boot/);
    const before = await placeholderBox(page);
    expect(before.moduleCss, "the module's stylesheet is not in yet").toBe(false);
    await saveShot(testInfo, page, `${SHOT_DIR}/${viewport.width}-before-module.png`, { fullPage: true });

    module.release();
    await expect(page.locator("#wz-style")).toBeAttached({ timeout: 15_000 });
    await expect(host).toHaveClass(/svc-form-host--schedule-boot/);
    await saveShot(testInfo, page, `${SHOT_DIR}/${viewport.width}-after-module.png`, { fullPage: true });
    await holdsFor(async () => {
      const after = await placeholderBox(page);
      expect(after, "the placeholder box moved when the module's stylesheet landed").toEqual({ ...before, moduleCss: true });
      const shifts = await page.evaluate(() => window.__placeholderShifts);
      expect(shifts, "the module landing moved content: " + JSON.stringify(shifts)).toEqual([]);
    }, { during: 500 });
  });
}
