// Real-browser lifecycle acceptance for the native /admin/plan screen
// (admin_panel/admin_plan_screen.py + templates/admin/plan.html + the real
// controller static/platform/admin-roadmap.mjs). Everything below drives the
// authenticated app served from THIS checkout against a throwaway Postgres
// database via tests/e2e/lib/harness.mjs — the app HTML, the bootstrap script,
// the controller module and the i18n module are the real ones; only network
// module imports are held/rejected through Playwright routes to exercise the
// failure paths, and the theme is flipped through theme.save() (fixture.py
// set-theme) because the plan route only exists under the pinamar theme.
//
// HONESTY LABELS (do not drop):
// * The synthetic PageTransitionEvent pagehide/pageshow dispatches below
//   prove the bootstrap/controller REMOUNT OWNERSHIP contract on a live page.
//   They are NOT proof that a real browser admitted the page to the actual
//   back/forward cache — no real BFCache navigation is exercised here.
// * The launch-progress copy the screen renders ("50%", "53%", "7 de 14",
//   "8 de 15") is STATIC CONTENT shipped by the roadmap screen. Asserting it
//   verifies rendering only; it makes no claim that those percentages measure
//   this refactor's completion, and nothing here edits them.
//
// Two harness instances are used one after the other: `off` is the default
// no-connector server (SANA_EMR_SYNC_ENABLED=false) and `on` opts into the HSI
// connector the safe local way (CONNECTOR_EMR_SYNC=hsi via the harness flag, with
// TURNOS_SKIP_HSI/HSI_SYNC_OFF/HSI_RESYNC_OFF still set, so no network sync ever
// runs — only the plan screen's flag and copy change).
//
// Port of tests/e2e/admin-plan-lifecycle.e2e.mjs (22 tests, same titles, same order).
//
// Differences from the node:test original:
// - The tests hand state to each other (the theme flipped to pinamar by the first one, the `report`
//   notes and evidence files written after the file, the `on` server started by the transition test),
//   so the file is `serial`: a failure skips the rest and a retry re-runs the whole file on a fresh
//   worker with a fresh record.
// - `off` is the file's `app` fixture (test.use({ appOptions })). `on` is started by the transition test
//   with `startApp(...)` (a `launchApp` server would be torn down when that test ends, and three later
//   tests need it) and torn down in `test.afterAll`. The original retired `off` before starting `on`;
//   `off` now stays up (idle) until the worker ends. The two servers never serve the same test.
// - `test.before` (languages es,en through the settings API, evidence directory) is `test.beforeAll`;
//   `test.after` (best-effort theme restore on both databases, report-<stamp>.md, teardown of `on`) is
//   `test.afterAll`, so the pinamar theme is always put back to humanrounds, also after a failed test.
// - Every `sleep(THEME_TTL_MS)` after `setTheme` (core/theme.py caches theme.current() ~3 s) is
//   `expect.poll` on the route's status code (200 once pinamar is live, 404 once humanrounds is back).
// - The other `sleep(...)` calls are gone: «nothing renders / nothing changes while the import is held
//   or after it is released» is a `holdsFor` window of the same length (400/200/600/200/800 ms), and the
//   300 ms head start before `pagehide` is the wait for the held i18n request (`gate.arrived`).
// - The report text names this spec and `playwright test` instead of `node --test`.
// - Sessions use the `newContext` fixture (baseURL preset; `on` passes its own) with the same history
//   wrapper init script, language cookie and login sequence; `finally { context.close() }` blocks and
//   `deferred()` + `finally { held.resolve() }` are the fixture's closing and `holdGate(cleanup)`.
// - Screenshots go through `saveShot` to the same files as before (artifactPath("human-rounds",
//   "plan-lifecycle-audit", "browser-<timestamp>")) and are attached to the report. `page.waitForNavigation`
//   (deprecated) is `page.waitForEvent("load")`, armed before the Reload click.
// - Waits are web-first (`toBeVisible`, `toContainText({ useInnerText })`, `toHaveURL`, `toHaveText`,
//   `toHaveCount`) instead of `waitForFunction` on innerText/location; the history-call lists, status
//   codes and fit probes stay point-in-time reads.
// - Locators that stay CSS: the plan screen's own markup (`#sec-plan`, `.rm-tabs`, `.rm-flow`, `[data-retry]`),
//   `#adm-nav a[data-nav=...]`, `h1#adm-h1`, `.adm5-menu > summary` and `.adm-subtitle` are the contract that
//   is driven (history ownership, tab links) and measured (overflow probes); alerts are `getByRole("alert")`
//   inside `#sec-plan`.
import fs from "node:fs/promises";
import path from "node:path";
import {
  test, expect, startApp, saveShot, holdGate, holdsFor, setTheme, artifactPath, ADMIN_USER, ADMIN_PASS,
} from "./lib/fixtures.mjs";
import { teardownHarness } from "./lib/harness.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_plan_lifecycle" } });
test.describe.configure({ mode: "serial" });

const STAMP = new Date().toISOString().replaceAll(":", "-");
const evidence = artifactPath("human-rounds", "plan-lifecycle-audit", `browser-${STAMP}`);
const WIDTHS = [360, 390, 768, 1024, 1280];
const report = [];
const note = (line) => report.push(line);

let on = null;

test.beforeAll(async ({ app, browser }) => {
  await fs.mkdir(evidence, { recursive: true });
  // Enable the second catalog once for the whole file (the same real settings
  // API the Settings screen uses). Default stays es, so every cookie-less
  // test below keeps rendering Spanish; English tests set the `lang` cookie.
  const context = await browser.newContext({ baseURL: app.baseUrl });
  try {
    const page = await context.newPage();
    await login(page);
    const settings = await page.request.post("/api/admin/settings", { data: {
      grupo: "languages", valores: { LANGUAGES: "es,en", LANG_DEFAULT: "es", LANG_SWITCHER_MODE: "secret" },
    } });
    expect(settings.ok(), "synthetic language setup through the real settings API").toBe(true);
    note("languages es,en enabled through the real settings API (default stays es)");
  } finally { await context.close(); }
});

test.afterAll(async ({ app }) => {
  // Best-effort theme restore even if a test failed mid-flip; every database
  // below is dropped by teardownHarness anyway.
  await setTheme(app.dbName, "humanrounds").catch(() => {});
  if (on?.ok) await setTheme(on.dbName, "humanrounds").catch(() => {});
  let files = [];
  try { files = await fs.readdir(evidence); } catch { /* nothing was captured */ }
  const body = [
    "# /admin/plan native lifecycle — real-browser audit (run " + STAMP + ")",
    "",
    "Executed file: tests/e2e/admin-plan-lifecycle.spec.mjs (this run's `playwright test`",
    "exit status is the pass/fail verdict; the notes below say what was proven).",
    "",
    "## Setup",
    "- Two throwaway databases/servers, used one after the other: connector OFF (default)",
    "  and connector ON (CONNECTOR_EMR_SYNC=hsi with every HSI sync daemon still",
    "  disabled — a local fixture configuration, no network sync, no PHI).",
    "- Theme flipped to pinamar via theme.save() (fixture set-theme), the ~3s TTL",
    "  waited out by polling the route; restored to humanrounds at the end.",
    "",
    "## Honesty labels",
    "- Synthetic PageTransitionEvent pagehide/pageshow dispatches prove the",
    "  controller's remount-ownership contract only. They are NOT proof of real",
    "  BFCache admission — no actual back/forward-cache navigation is exercised.",
    "- The launch percentages (50%, 53%, \"7 de 14\", \"8 de 15\") are STATIC COPY",
    "  of the roadmap screen, asserted as rendered content only. They are not a",
    "  measurement of this refactor's completion and were not altered here.",
    "",
    "## Test notes",
    ...report,
    "",
    "## Evidence files",
    ...files.map((f) => "- " + f),
    "",
    "## Verification boundary",
    "- This report is produced by the executed browser suite. Consult its complete run log for pass/fail; notes and screenshots alone are not acceptance.",
    "- Full combined CI and production verification are separate gates.",
  ].join("\n");
  try {
    await fs.writeFile(path.join(evidence, `report-${STAMP}.md`), body, "utf8");
  } finally {
    if (on) await teardownHarness(on);
  }
});

// ---- shared helpers --------------------------------------------------------

async function login(page) {
  await page.goto("/admin/login", { waitUntil: "load", timeout: 15000 });
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 8000 });
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  await page.waitForFunction(
    () => location.pathname.startsWith("/admin/") && location.pathname !== "/admin/login",
    null, { timeout: 10000 },
  );
}

// Fresh authenticated page of `state`'s server. The init script wraps history from document
// start, so every pushState/replaceState the real controller makes (including
// the canonicalization replaceState that fires during first render) is counted.
async function session(newContext, state, { width = 1280, lang } = {}) {
  const context = await newContext({ baseURL: state.baseUrl, viewport: { width, height: 900 } });
  await context.addInitScript(() => {
    window.__planHistory = [];
    for (const name of ["pushState", "replaceState"]) {
      const original = history[name].bind(history);
      history[name] = function (state2, title, url) {
        window.__planHistory.push({ method: name, url: String(url) });
        return original(state2, title, url);
      };
    }
  });
  if (lang) await context.addCookies([{ name: "lang", value: lang, url: state.baseUrl }]);
  const page = await context.newPage();
  await login(page);
  return page;
}

async function planReady(page) {
  await expect(page.locator("#sec-plan .rm-tabs")).toBeVisible({ timeout: 12000 });
}

const planText = (page) => page.locator("#sec-plan").innerText();
const historyCalls = (page) => page.evaluate(() => window.__planHistory || []);
const planShows = (page, marker, timeout = 12000) =>
  expect(page.locator("#sec-plan")).toContainText(marker, { timeout, useInnerText: true });

// Same fit contract as the sibling screens: no page overflow at the five
// widths, and no element escaping the viewport unless a local scroller owns it
// (the roadmap flow row scrolls horizontally by design above 1100px).
async function fits(page, label) {
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    const probe = await page.evaluate(() => {
      const vw = document.documentElement.clientWidth;
      const offenders = [];
      document.querySelectorAll("#sec-plan *").forEach((el) => {
        const r = el.getBoundingClientRect();
        let local = false;
        for (let p = el.parentElement; p; p = p.parentElement) {
          const css = getComputedStyle(p);
          if ((css.overflowX === "auto" || css.overflowX === "scroll")
            && p.getBoundingClientRect().left >= -1
            && p.getBoundingClientRect().right <= vw + 1) { local = true; break; }
        }
        if (r.width > 0 && !local && (r.right > vw + 1 || r.left < -1)) {
          offenders.push(el.className || el.tagName);
        }
      });
      return {
        page: document.documentElement.scrollWidth - vw,
        offenders: offenders.slice(0, 8),
      };
    });
    expect(probe.page <= 1 && probe.offenders.length === 0,
      `${label}: overflow at ${width}px: ${JSON.stringify(probe)}`).toBe(true);
    note(`geometry ${label} ${width}px: ${JSON.stringify(probe)}`);
  }
  await page.setViewportSize({ width: 1280, height: 900 });
}

async function shot(testInfo, page, name) {
  await saveShot(testInfo, page, path.join(evidence, name + ".png"), { fullPage: true });
  note("evidence: " + name + ".png");
}

// SYNTHETIC restore — see the honesty labels at the top of this file: this is
// the controller's remount contract, not proof of real BFCache admission.
function restore(page) {
  return page.evaluate(() => {
    for (const type of ["pagehide", "pageshow"]) {
      dispatchEvent(new PageTransitionEvent(type, { persisted: true }));
    }
  });
}

// The route serves from the first request after theme.save() once core/theme.py's ~3 s cache expires.
const planStatus = (page) => async () => (await page.request.get("/admin/plan")).status();

const ROADMAP_MODULE = "**/static/platform/admin-roadmap.*.mjs";
const I18N_MODULE = "**/static/platform/i18n.*.mjs";

// ---- theme gate + Spanish baseline (connector OFF harness) -----------------

test("plan route 404s outside the pinamar theme, then exists once the live theme flips", async ({ newContext, app }) => {
  const page = await session(newContext, app);
  const gate = await page.request.get("/admin/plan");
  expect(gate.status(), "the route itself does not exist outside pinamar").toBe(404);
  await setTheme(app.dbName, "pinamar");
  await expect.poll(planStatus(page), { message: "after theme.save() + TTL the route serves", timeout: 15000 }).toBe(200);
  note("theme gate404 to200 proven on the local test server");
});

test("cold load renders the real controller's Spanish content on the canonical bare URL", async ({ newContext, app }) => {
  const page = await session(newContext, app);
  await page.goto("/admin/plan", { waitUntil: "load", timeout: 15000 });
  await planReady(page);
  expect(new URL(page.url()).pathname + new URL(page.url()).search + new URL(page.url()).hash).toBe("/admin/plan");
  expect(await page.evaluate(() => window.SANA_ADMIN_SCREEN)).toBe("plan");
  await expect(page.locator('#adm-nav a[data-nav="plan"]')).toHaveAttribute("href", "/admin/plan");
  await expect(page.locator('#adm-nav a[data-nav="plan"]')).toHaveAttribute("class", /\bon\b/);
  await expect(page.locator("h1#adm-h1")).toHaveText(/Hoja de ruta/);
  await expect(page.locator("html")).toHaveAttribute("lang", "es");
  const text = await planText(page);
  expect(text, "the real simple body, not a stub").toMatch(/Qué ya funciona/);
  expect(text, "the shared flow diagram renders").toMatch(/El sistema ya está listo/);
  expect(text).not.toMatch(/Cargando/i);
  expect(await historyCalls(page), "canonical bare URL performs no history mutation").toEqual([]);
});

for (const [view, suffix, marker] of [
  ["simple", "", /Qué ya funciona/],
  ["technical", "?view=technical", /Ya construido/],
]) {
  test(`Spanish ${view} view fits 360-1280px with no escaping element`, async ({ newContext, app }, testInfo) => {
    const page = await session(newContext, app);
    await page.goto("/admin/plan" + suffix, { waitUntil: "load", timeout: 15000 });
    await planShows(page, marker);
    await fits(page, `/admin/plan ${view} (es)`);
    await page.setViewportSize({ width: 390, height: 900 });
    await shot(testInfo, page, `plan-es-${view}-390`);
    await page.setViewportSize({ width: 1280, height: 900 });
    await shot(testInfo, page, `plan-es-${view}-1280`);
  });
}

test("the direct technical URL renders the technical tab without touching history, reload keeps it", async ({ newContext, app }) => {
  const page = await session(newContext, app);
  await page.goto("/admin/plan?view=technical", { waitUntil: "load", timeout: 15000 });
  await planShows(page, /Ya construido/);
  await expect(page.locator('.rm-tabs a[aria-selected="true"]')).toHaveAttribute("href", "/admin/plan?view=technical");
  expect(await historyCalls(page), "already-canonical URL writes no history entry").toEqual([]);
  await page.reload({ waitUntil: "load", timeout: 15000 });
  await expect(page).toHaveURL((url) => url.search === "?view=technical", { timeout: 12000 });
  await planShows(page, /Ya construido/);
});

test("a tab click performs exactly one pushState and real Back/Forward re-route both ways", async ({ newContext, app }) => {
  const page = await session(newContext, app);
  await page.goto("/admin/plan", { waitUntil: "load", timeout: 15000 });
  await planReady(page);
  await page.click('.rm-tabs a[href="/admin/plan?view=technical"]');
  await expect(page).toHaveURL((url) => url.search === "?view=technical", { timeout: 8000 });
  await planShows(page, /Ya construido/, 8000);
  expect(await historyCalls(page),
    "one click = exactly one owned history mutation").toEqual([{ method: "pushState", url: "/admin/plan?view=technical" }]);
  await page.goBack();
  await expect(page).toHaveURL((url) => url.search === "", { timeout: 8000 });
  await planShows(page, /Qué ya funciona/, 8000);
  await page.goForward();
  await expect(page).toHaveURL((url) => url.search === "?view=technical", { timeout: 8000 });
  await planShows(page, /Ya construido/, 8000);
  expect(await historyCalls(page),
    "popstate routing re-renders without ever writing history").toEqual([{ method: "pushState", url: "/admin/plan?view=technical" }]);
});

test("non-canonical queries rewrite to the canonical URL via exactly one replaceState", async ({ newContext, app }) => {
  const page = await session(newContext, app);
  await page.goto("/admin/plan?view=TECHNICAL&x=1", { waitUntil: "load", timeout: 15000 });
  await expect(page).toHaveURL((url) => url.search === "", { timeout: 12000 });
  await planShows(page, /Qué ya funciona/);
  expect(await historyCalls(page),
    "junk canonicalizes in place — no new history entry").toEqual([{ method: "replaceState", url: "/admin/plan" }]);
  await page.goto("/admin/plan?view=technical&x=1", { waitUntil: "load", timeout: 15000 });
  await expect(page).toHaveURL((url) => url.search === "?view=technical", { timeout: 12000 });
  await planShows(page, /Ya construido/);
  expect(await historyCalls(page),
    "extra params around a valid view keep the technical canonical URL").toEqual([{ method: "replaceState", url: "/admin/plan?view=technical" }]);
});

test("modified clicks reach the browser default unchanged and write no owned history", async ({ newContext, app }) => {
  const page = await session(newContext, app);
  await page.goto("/admin/plan", { waitUntil: "load", timeout: 15000 });
  await planReady(page);
  // Observe AFTER the controller's document listener. Cancel only in this
  // final test observer, after recording whether the app prevented default.
  // Headless popup creation is not this controller contract; this does NOT
  // claim a real OS/browser tab was created (the first run preserved that
  // unsupported expectation as a failing artifact).
  await page.evaluate(() => {
    window.__planNativeClicks = [];
    window.addEventListener("click", (event) => {
      if (!event.target.closest(".rm-tabs a")) return;
      window.__planNativeClicks.push({ prevented: event.defaultPrevented,
        ctrl: event.ctrlKey, meta: event.metaKey });
      event.preventDefault();
    });
  });
  for (const modifier of ["Control", "Meta"]) {
    const link = page.locator('.rm-tabs a[href="/admin/plan?view=technical"]');
    if (process.platform === "darwin" && modifier === "Control") {
      // macOS maps a physical Control-click to contextmenu, not click.
      // Exercise that non-native click branch with a labeled synthetic
      // DOM event; Command/Meta below remains a real pointer interaction.
      await link.dispatchEvent("click", { button: 0, ctrlKey: true, bubbles: true, cancelable: true });
    } else {
      await link.click({ modifiers: [modifier] });
    }
  }
  expect(await page.evaluate(() => window.__planNativeClicks)).toEqual([
    { prevented: false, ctrl: true, meta: false },
    { prevented: false, ctrl: false, meta: true },
  ]);
  expect(new URL(page.url()).search, "the opener keeps the simple view").toBe("");
  expect(await historyCalls(page), "a deferred-to-native click performs no owned mutation").toEqual([]);
  note("modified-click ownership: real platform pointer input; macOS Control branch is a synthetic click because physical Control opens contextmenu; no OS popup claim");
});

test("English page renders the translated main body (not only the tabs) and keeps Spanish out", async ({ newContext, app }) => {
  const page = await session(newContext, app, { lang: "en" });
  await page.goto("/admin/plan", { waitUntil: "load", timeout: 15000 });
  await planShows(page, /What already works/);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  const text = await planText(page);
  expect(text, "flow node translated").toMatch(/The system is already ready/);
  expect(text, "section heading translated").toMatch(/What already works/);
  expect(text, "static progress copy rendered in English").toMatch(/7 of 14 launch conditions/);
  expect(text).toMatch(/In plain terms/);
  expect(text, "no untranslated Spanish fallback ships on the English page").not.toMatch(/Qué ya funciona/);
  await page.click('.rm-tabs a[href="/admin/plan?view=technical"]');
  await planShows(page, /Already built/, 8000);
  const tecText = await planText(page);
  expect(tecText).toMatch(/Technical detail/);
  expect(tecText).not.toMatch(/Ya construido/);
});

test("English simple and technical views fit 360-1280px", async ({ newContext, app }, testInfo) => {
  const page = await session(newContext, app, { lang: "en" });
  await page.goto("/admin/plan", { waitUntil: "load", timeout: 15000 });
  await planShows(page, /What already works/);
  await fits(page, "/admin/plan simple (en)");
  await page.setViewportSize({ width: 390, height: 900 });
  await shot(testInfo, page, "plan-en-simple-390");
  await page.setViewportSize({ width: 1280, height: 900 });
  await shot(testInfo, page, "plan-en-simple-1280");
  await page.goto("/admin/plan?view=technical", { waitUntil: "load", timeout: 15000 });
  await planShows(page, /Already built/);
  await fits(page, "/admin/plan technical (en)");
  await page.setViewportSize({ width: 390, height: 900 });
  await shot(testInfo, page, "plan-en-technical-390");
  await page.setViewportSize({ width: 1280, height: 900 });
  await shot(testInfo, page, "plan-en-technical-1280");
});

test("duplicate synthetic pagehide/pageshow restores keep exactly one owned mutation per click (synthetic, not BFCache proof)", async ({ newContext, app }) => {
  const page = await session(newContext, app);
  await page.goto("/admin/plan", { waitUntil: "load", timeout: 15000 });
  await planReady(page);
  // Twice in a row: every persisted-pageshow remount must dispose the
  // previous owner first, so a zombie click listener can never double-write.
  await restore(page);
  await restore(page);
  await planShows(page, /Qué ya funciona/, 8000);
  await page.click('.rm-tabs a[href="/admin/plan?view=technical"]');
  await expect(page).toHaveURL((url) => url.search === "?view=technical", { timeout: 8000 });
  let calls = await historyCalls(page);
  expect(calls.filter((c) => c.method === "pushState").length,
    "after two synthetic restores, one real click still produces exactly one pushState").toBe(1);
  await restore(page);
  await planShows(page, /Ya construido/, 8000);
  await page.click('.rm-tabs a[href="/admin/plan"]');
  await expect(page).toHaveURL((url) => url.search === "", { timeout: 8000 });
  calls = await historyCalls(page);
  expect(calls.filter((c) => c.method === "pushState").length,
    "the second restored owner also owns exactly one mutation").toBe(2);
  note("synthetic remount ownership proven (NOT a real BFCache admission)");
});

test("a held i18n import paints nothing, mutates no history, and popstate cannot bypass readiness", async ({ newContext, app, cleanup }) => {
  // English page: the dangerous premature render would be the Spanish
  // fallback, so this is exactly where the "no premature translated-content
  // claim" contract is visible.
  const page = await session(newContext, app, { lang: "en" });
  const gate = holdGate(cleanup);
  await page.route(I18N_MODULE, async (route) => { await gate.held; await route.continue(); });
  await page.goto("/admin/plan?view=TECHNICAL&x=1", { waitUntil: "domcontentloaded" });
  await holdsFor(async () => {
    expect((await planText(page)).trim(), "nothing renders before the dependency settles").toBe("");
    expect(await planText(page), "no premature translated-content claim while the dependency is pending")
      .not.toMatch(/Qué ya funciona|What already works/);
    expect(await historyCalls(page), "a pending mount owns no URL mutation").toEqual([]);
  }, { during: 400 });
  await page.evaluate(() => dispatchEvent(new PopStateEvent("popstate", { state: {} })));
  await holdsFor(async () => {
    expect(new URL(page.url()).search, "popstate cannot canonicalize early").toBe("?view=TECHNICAL&x=1");
    expect((await planText(page)).trim(), "popstate cannot render early").toBe("");
  }, { during: 200 });
  gate.release();
  await planShows(page, /What already works/);
  expect(new URL(page.url()).search, "the junk query canonicalizes only after readiness").toBe("");
  expect(await historyCalls(page),
    "exactly one owned mutation, performed by the ready owner").toEqual([{ method: "replaceState", url: "/admin/plan" }]);
});

test("pagehide while the import is pending blocks the late render; a persisted pageshow remounts", async ({ newContext, app, cleanup }) => {
  const page = await session(newContext, app);
  const gate = holdGate(cleanup);
  await page.route(I18N_MODULE, async (route) => { gate.arrive(); await gate.held; await route.continue(); });
  await page.goto("/admin/plan", { waitUntil: "domcontentloaded" });
  await gate.arrived;
  await page.evaluate(() => dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  gate.release();
  await holdsFor(async () => {
    expect((await planText(page)).trim(), "a stale import completion cannot render after pagehide").toBe("");
    expect(await historyCalls(page)).toEqual([]);
  }, { during: 600 });
  await page.evaluate(() => dispatchEvent(new PopStateEvent("popstate", { state: {} })));
  await holdsFor(async () => {
    expect((await planText(page)).trim(), "the disposed owner ignores later history events").toBe("");
  }, { during: 200 });
  await page.evaluate(() => dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await planShows(page, /Qué ya funciona/, 8000);
  expect(await historyCalls(page), "restoration renders without any history write").toEqual([]);
});

test("a rejected controller import shows visible recovery with a working Reload (Spanish)", async ({ newContext, app }, testInfo) => {
  const page = await session(newContext, app, { width: 390 });
  await page.route(ROADMAP_MODULE, (route) => route.abort("failed"));
  await page.goto("/admin/plan", { waitUntil: "load", timeout: 15000 });
  const alert = page.locator("#sec-plan").getByRole("alert");
  await expect(alert).toBeVisible({ timeout: 12000 });
  await expect(alert).toContainText(/No se pudo cargar esta sección/);
  const retry = page.locator("#sec-plan [data-retry]");
  await expect(retry).toHaveText("Recargar");
  expect(await page.locator("#sec-plan .rm-flow").count(), "no roadmap content ships as if it had loaded").toBe(0);
  expect(await page.locator("#sec-plan .rm-tabs").count()).toBe(0);
  await expect(page.locator(".adm-subtitle")).toBeVisible();
  const menu = page.locator(".adm5-menu > summary");
  await menu.click();
  await expect(page.locator("#adm-nav")).toBeVisible();
  await menu.click();
  expect(new URL(page.url()).pathname, "recovery keeps the direct URL").toBe("/admin/plan");
  await shot(testInfo, page, "recovery-controller-es-390");
  await page.unroute(ROADMAP_MODULE);
  await Promise.all([
    page.waitForEvent("load", { timeout: 15000 }),
    retry.click(),
  ]);
  await planReady(page);
  expect(await planText(page), "the actual Reload control loads the real screen").toMatch(/Qué ya funciona/);
});

test("a rejected i18n import fails closed: recovery line, never untranslated roadmap content", async ({ newContext, app }) => {
  const page = await session(newContext, app);
  await page.route(I18N_MODULE, (route) => route.abort("failed"));
  await page.goto("/admin/plan", { waitUntil: "load", timeout: 15000 });
  await expect(page.locator("#sec-plan").getByRole("alert")).toBeVisible({ timeout: 12000 });
  expect(await page.locator("#sec-plan .rm-flow, #sec-plan .rm-tabs").count()).toBe(0);
  expect(await planText(page)).not.toMatch(/Qué ya funciona|El sistema ya está listo/);
  expect(await page.locator("#sec-plan [data-retry]").count(), "the shared recovery path exposes its reload").toBe(1);
});

test("at eight seconds the late copy shows beside the plan, and the held dependency still mounts it", async ({ newContext, app, cleanup }, testInfo) => {
  const page = await session(newContext, app, { width: 390 });
  const gate = holdGate(cleanup);
  await page.route(I18N_MODULE, async (route) => { await gate.held; await route.continue(); });
  await page.goto("/admin/plan", { waitUntil: "domcontentloaded" });
  const late = page.locator(".screen-load-slow").getByRole("status");
  await expect(late).toBeVisible({ timeout: 12000 });
  await expect(late).toHaveText("La carga tarda más de lo normal.");
  expect(await page.locator("#sec-plan").getByRole("alert").count(), "the copy never lands inside the screen").toBe(0);
  expect(await page.locator("#sec-plan .rm-flow").count()).toBe(0);
  await shot(testInfo, page, "late-copy-390");
  gate.release();
  await planReady(page);
  expect(await planText(page)).toMatch(/Qué ya funciona/);
  await expect(page.locator(".screen-load-slow"), "the late answer clears the copy").toHaveCount(0);
  note("late copy at the real 8 s deadline, then the held dependency mounts the plan in-browser");
});

test("recovery copy follows the page language (English)", async ({ newContext, app }, testInfo) => {
  const page = await session(newContext, app, { lang: "en", width: 1280 });
  await page.route(ROADMAP_MODULE, (route) => route.abort("failed"));
  await page.goto("/admin/plan", { waitUntil: "load", timeout: 15000 });
  const alert = page.locator("#sec-plan").getByRole("alert");
  await expect(alert).toBeVisible({ timeout: 12000 });
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(alert).toContainText(/Couldn't load this section/);
  await expect(page.locator("#sec-plan [data-retry]")).toHaveText("Reload");
  await shot(testInfo, page, "recovery-en-1280");
});

test("connector OFF: neither tab mentions HSI and the no-EMR copy is the one rendered", async ({ newContext, app }) => {
  const page = await session(newContext, app);
  await page.goto("/admin/plan", { waitUntil: "load", timeout: 15000 });
  await planReady(page);
  expect(await page.evaluate(() => window.SANA_EMR_SYNC_ENABLED === false),
    "the server-rendered flag is off on the default harness").toBe(true);
  const simple = await planText(page);
  expect(simple, "no HSI text on the simple tab").not.toMatch(/HSI/i);
  expect(simple, "static no-EMR progress copy (screen copy, not a measured metric)").toMatch(/7 de 14 condiciones/);
  expect(simple, "no-EMR flow wording").toMatch(/La dirección oficial y la fecha de lanzamiento/);
  await page.click('.rm-tabs a[href="/admin/plan?view=technical"]');
  await planShows(page, /Ya construido/, 8000);
  const tec = await planText(page);
  expect(tec, "no HSI text on the technical tab").not.toMatch(/HSI/i);
  expect(tec, "the no-EMR week list is the one rendered").toMatch(/Las dos cosas son suyas/);
  note("connector OFF: zero HSI mentions on both tabs");
});

// ---- connector ON harness (started after the OFF one is done with) ---------

test("OFF theme restores to404 before its server is retired and ON starts", async ({ newContext, app }) => {
  const page = await session(newContext, app);
  await setTheme(app.dbName, "humanrounds");
  await expect.poll(planStatus(page), { timeout: 15000 }).toBe(404);
  on = await startApp({ dbName: "sana_turnos_test_plan_lifecycle_hsi", hsi: true });
  expect(on.ok, on.reason).toBe(true);
});

test("connector ON: the server flag is true and the HSI conditions surface on both tabs", async ({ newContext }, testInfo) => {
  const page = await session(newContext, on);
  await setTheme(on.dbName, "pinamar");
  await expect.poll(planStatus(page), { message: "the plan route serves once the pinamar theme is live", timeout: 15000 }).toBe(200);
  await page.goto("/admin/plan", { waitUntil: "load", timeout: 15000 });
  await planReady(page);
  expect(await page.evaluate(() => window.SANA_EMR_SYNC_ENABLED === true),
    "the flag derives from the real hsi section presence").toBe(true);
  await expect(page.locator('#adm-nav a[data-nav="hsi"]')).toHaveCount(1);
  const simple = await planText(page);
  expect(simple, "the HSI condition card is visible").toMatch(/Conexión con el HSI del hospital/);
  expect(simple, "static EMR progress copy (screen copy, not a measured metric)").toMatch(/8 de 15 condiciones/);
  await page.click('.rm-tabs a[href="/admin/plan?view=technical"]');
  await planShows(page, /Ya construido/, 8000);
  const tec = await planText(page);
  expect(tec).toMatch(/Acceso al HSI productivo/);
  expect(tec).toMatch(/Push a HSI \+ resolutor de conflictos/);
  expect(tec, "the EMR week list is the one rendered").toMatch(/Las tres cosas son suyas/);
  await page.setViewportSize({ width: 390, height: 900 });
  await shot(testInfo, page, "hsi-on-technical-390");
  await page.goto("/admin/plan", { waitUntil: "load", timeout: 15000 });
  await planReady(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await shot(testInfo, page, "hsi-on-simple-1280");
  note("connector ON: HSI conditions on both tabs via local fixture config, no network sync");
});

test("connector ON: simple and technical views fit 360-1280px", async ({ newContext }) => {
  const page = await session(newContext, on);
  await page.goto("/admin/plan", { waitUntil: "load", timeout: 15000 });
  await planReady(page);
  await fits(page, "/admin/plan simple (hsi-on)");
  await page.goto("/admin/plan?view=technical", { waitUntil: "load", timeout: 15000 });
  await planShows(page, /Ya construido/);
  await fits(page, "/admin/plan technical (hsi-on)");
});

test("ON theme restored: /admin/plan 404s again", async ({ newContext }) => {
  const page = await session(newContext, on);
  await setTheme(on.dbName, "humanrounds");
  await expect.poll(planStatus(page), { timeout: 15000 }).toBe(404);
  note("theme restored to humanrounds on both throwaway databases");
});
