// The face a page paints in before its web font arrives takes the web font's space.
//
// Each theme names a "<family> Fallback" face after its web font (static/fonts/fira.css,
// static/fonts/humanrounds.css): Arial (Arimo / Liberation Sans on Linux) with size-adjust
// and ascent/descent overrides measured against the theme's font. Before it, the stack
// fell to `system-ui`, which is a different typeface per machine (San Francisco, Segoe UI,
// DejaVu Sans) — on a wide one the queue's control row at 768 rewrapped when the font
// arrived (CLS 0.25 with a late font, 2026-11-10).
//
// For both themes, at 768: the queue with its .woff2 requests aborted paints in the
// fallback face, keeps the control row's rows and lands within 2% of the text width
// it has with the font; with the font held back and then released, the swap moves
// nothing measurable (CLS under 0.02). The same at 1280, where the table engine fits its
// columns to the painted face and refits when the font set finishes loading
// (static/platform/admin-table.mjs, "loadingdone").
//
// The tests live here and the two specs (admin-font-fallback-humanrounds.spec.mjs,
// admin-font-fallback-pinamar.spec.mjs) each run one theme on a database of their own.
import { test, expect, ADMIN_USER, ADMIN_PASS, setTheme, saveShot, artifactPath } from "./fixtures.mjs";
import { join } from "node:path";

const WIDTH = 768;
const THEMES = [
  { id: "humanrounds", family: "Manrope" },
  { id: "pinamar", family: "Fira Sans" },
];
const evidence = artifactPath("human-rounds", "font-fallback");


async function openQueue(newContext, app, fonts, width = WIDTH) {
  const context = await newContext({ viewport: { width, height: 900 } });
  await context.addInitScript(() => {
    window.__fontShifts = [];
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (entry.hadRecentInput) continue;
        window.__fontShifts.push(entry.value);
        const rect = (r) => r && [r.x, r.y, r.width, r.height].map(Math.round);
        (window.__fontShiftLog = window.__fontShiftLog || []).push({
          at: Math.round(entry.startTime), value: entry.value,
          sources: (entry.sources || []).map((s) => ({
            node: s.node && (s.node.id || s.node.className || s.node.nodeName), from: rect(s.previousRect), to: rect(s.currentRect),
          })),
        });
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
  const page = await context.newPage();
  await page.goto("/admin/login", { waitUntil: "load" });
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  await page.waitForURL((url) => url.pathname.startsWith("/admin/") && url.pathname !== "/admin/login", { waitUntil: "commit" });
  await page.goto("about:blank");
  let release = () => {};
  const held = new Promise((resolve) => { release = resolve; });
  await page.route(/\.woff2(\?|$)/, async (route) => {
    if (fonts === "blocked") return route.abort();
    if (fonts === "late") await held;
    return route.continue();
  });
  // A preloaded font held back also holds the load event: wait for the DOM only then.
  await page.goto("/admin/cola", { waitUntil: fonts === "late" ? "domcontentloaded" : "load" });
  await expect(page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 12000 });
  await expect(page.locator("#cola-live")).toHaveText(/\S/, { timeout: 12000 });
  await expect(page.locator("#sec-cola")).toContainText(app.fixture.queue_pendiente, { useInnerText: true, timeout: 12000 });
  return { page, context, release };
}

function measure(page) {
  return page.evaluate(() => {
    const controls = document.querySelector("#sec-cola .queue-controls");
    const parts = [...controls.querySelectorAll("button, a, input, select, .queue-date, span")]
      .filter((element) => element.getBoundingClientRect().width > 0);
    // A row starts where a control's top is more than 12px below the previous one.
    const tops = parts.map((element) => element.getBoundingClientRect().top).sort((a, b) => a - b);
    const rows = tops.filter((top, index) => index === 0 || top - tops[index - 1] > 12).length;
    // Text runs only: element boxes (inputs, padding) don't change with the face and would dilute it.
    let width = 0;
    const range = document.createRange();
    for (const root of [document.querySelector("#sec-cola"), document.querySelector("h1")].filter(Boolean)) {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        range.selectNodeContents(node);
        for (const rect of range.getClientRects()) width += rect.width;
      }
    }
    const faces = [...document.fonts].filter((face) => face.status === "loaded").map((face) => face.family);
    return { rows, width, faces, controlsHeight: controls.getBoundingClientRect().height };
  });
}

// The theme lives in the database, so each theme has a spec file (and database) of its own:
// tests of the two themes run in parallel workers and must not flip it under each other.
// Within one theme's database every test sets the same theme.
// The panel takes a changed theme a moment to paint: wait until a page shows it.
async function useTheme(app, newContext, theme) {
  await setTheme(app.dbName, theme.id);
  await expect.poll(async () => {
    const { page, context } = await openQueue(newContext, app, "loaded");
    const value = await page.evaluate(() => document.documentElement.dataset.theme);
    await context.close();
    return value;
  }, { timeout: 20_000 }).toBe(theme.id);
}

export function defineFontFallbackTests(themeId) {
  const theme = THEMES.find((entry) => entry.id === themeId);

  test(`${theme.id}: the queue at ${WIDTH} keeps its rows and width with the web font blocked`, async ({ app, newContext }, testInfo) => {
    test.setTimeout(90_000);
    await useTheme(app, newContext, theme);

    const loaded = await openQueue(newContext, app, "loaded");
    await loaded.page.evaluate(() => document.fonts.ready);
    const withFont = await measure(loaded.page);
    expect(withFont.faces).toContain(theme.family);

    const blocked = await openQueue(newContext, app, "blocked");
    await blocked.page.evaluate(() => document.fonts.ready);
    const withFallback = await measure(blocked.page);
    expect(withFallback.faces).not.toContain(theme.family);
    expect(withFallback.faces, "the metric-matched face paints while the font is missing").toContain(theme.family + " Fallback");
    expect(withFallback.rows).toBe(withFont.rows);
    expect(Math.abs(withFallback.controlsHeight - withFont.controlsHeight)).toBeLessThanOrEqual(2);
    expect(Math.abs(withFallback.width / withFont.width - 1), JSON.stringify({ withFont: withFont.width, withFallback: withFallback.width }))
      .toBeLessThan(0.02);
    await saveShot(testInfo, loaded.page, join(evidence, `${theme.id}-${WIDTH}-font.png`));
    await saveShot(testInfo, blocked.page, join(evidence, `${theme.id}-${WIDTH}-blocked.png`));
    await loaded.context.close();
    await blocked.context.close();
  });

  // 1280 too: there the table engine fits its columns to the fallback text, and a fallback
  // narrower than the font let a tight column rewrap for one frame when the font landed.
  for (const width of [WIDTH, 1280]) test(`${theme.id}: a late web font at ${width} swaps in without moving the queue`, async ({ app, newContext }) => {
    test.setTimeout(90_000);
    await useTheme(app, newContext, theme);
    const late =await openQueue(newContext, app, "late", width);
    // On a loaded machine the page is still settling for itself (late data, avatars, the table fit)
    // after the rows are in; that shift is not the font's. Hold the font back until the layout has
    // been still for 600 ms, so the measure below is the swap alone.
    await late.page.evaluate(() => new Promise((resolve) => {
      let count = window.__fontShifts.length;
      let since = performance.now();
      const giveUp = since + 20000;   // a page that never settles is measured as it is; the shift log names why
      const tick = () => {
        if (performance.now() > giveUp) return resolve();
        if (window.__fontShifts.length !== count) { count = window.__fontShifts.length; since = performance.now(); }
        if (performance.now() - since >= 600) resolve(); else requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }));
    const before = await late.page.evaluate(() => window.__fontShifts.reduce((sum, value) => sum + value, 0));
    late.release();
    expect(await late.page.evaluate(() => document.documentElement.dataset.theme), "the page paints the theme under test").toBe(theme.id);
    await expect.poll(() => late.page.evaluate((name) => [...document.fonts]
      .some((face) => face.family === name && face.status === "loaded"), theme.family), { timeout: 15_000 }).toBe(true);
    await late.page.evaluate(() => document.fonts.ready);
    await late.page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const after = await late.page.evaluate(() => window.__fontShifts.reduce((sum, value) => sum + value, 0));
    const log = await late.page.evaluate(() => JSON.stringify(window.__fontShiftLog || []));
    expect(after - before, "layout shift caused by the font swap; shifts: " + log).toBeLessThan(0.02);
    await late.context.close();
  });
}
