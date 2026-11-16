// Tier 5 slice 4 (docs/roadmap/tier5-frontend-design.md, "admin.html decomposition
// into /admin/<screen>"): the server-routed admin pages -- `/admin/entrevistas` (the
// intake module's pilot) and every direct screen after it (my-patients, prompts, the
// catalog editors, cola, reportes, solicitudes, preparations, knowledge, system,
// settings, users, account, legal, appearance, hsi, mis-turnos, pacientes, consult,
// plan) -- against a real running server and a real headless browser.
//
// The harness always spawns asgi_main.py (the legacy server.py topology is retired --
// see harness.mjs), so the direct-URL screens are unconditionally present. The legacy
// `#entrevistas` hash section remains proven too: it is a long-lived coexistence
// surface, not a shim scheduled for near-term deletion (see the design's "Sequencing"
// section).
//
// The file's app is seeded with:
//   requests: true       the two fictional pending rows /admin/solicitudes asserts against
//   doctorPatients: true the doctor operator, the roster patient and her interview
//   intake + doorMedico  ONE doctorless interview (the ownership-boundary row of /admin/consult)
//   hsi: true            the HSI connector, so /admin/hsi is reachable (it 404s by default)
//   queue: true          the three TODAY-dated approved appointments /admin/cola asserts against
//
// Port of tests/e2e/admin-screen.e2e.mjs (47 tests: same titles, same order) to
// @playwright/test.
//
// Differences from the node:test original:
// - The eight «404s in legacy mode» tests (preparations, knowledge, settings, account, hsi,
//   appearance, legal, users) were dead code: the original skipped them with
//   `if (ASGI) t.skip(...)` where `ASGI` was the constant `true`. They are removed together
//   with the constant and every other `if (!ASGI)` guard (never taken), so 55 node tests
//   (47 run + 8 skipped) become 47. Titles keep their «(ASGI only)» suffix.
// - The file is `serial`: its tests hand state to each other through the shared database --
//   «renders two rows» must run before the reject test (2 pending rows, then 1), the empty
//   patient list of /admin/pacientes must precede the patient it creates, and the plan test
//   flips the live theme. A failure skips the rest and a retry re-runs the whole file on a
//   fresh worker, with a fresh database.
// - Sessions: `adminPage` (the runner's page, already signed in as the harness admin; it
//   lands on /admin/account instead of the server-restored last screen) replaces the
//   hand-made `loginAdmin(newPage())`; the doctor operator signs in on the plain `page`
//   through the real login form (`signIn`, the original's `loginOperator`: any /admin/ page but
//   the login counts, a doctor lands on onboarding). Extra contexts (anonymous hits, the second page of the
//   role gate) come from `newContext()` and close themselves. The pages are closed by the runner,
//   so the `try/finally { page.close() }` frames are gone. A test that failed to sign in
//   fails there instead of skipping (`requireOk` is the `_gate` fixture).
// - Time-based waits became waits for the state: the 3.5 s sleeps that outwait the server's
//   3 s config/theme cache in «disabling intake…» and «/admin/plan…» poll the effect instead
//   (`expect.poll` on the status of /admin/entrevistas, /admin/plan). Both tests restore
//   their state in `cleanup` (modules back to «all enabled»; theme back to humanrounds, and
//   the route 404s again before the next test) even when they fail; the editorial-recovery test
//   restores the languages setting the same way.
// - The fixed 100 ms sleep in «protects a dirty draft before disabling it» and the instant
//   «blur must not save» snapshot are now `holdsFor` windows (100 ms and 300 ms): the claim is
//   that nothing is sent, so the check runs over the window instead of once. The route gates
//   of the three knowledge draft tests (title generation, save, create, disable) are
//   `holdGate`s; the test waits for `arrived` before it releases and before it counts the
//   held requests, and the request bodies the original asserted inside the route handler
//   (title, content, tags, source url; id and content of the second PUT) are asserted from
//   the test body.
// - `page.waitForNavigation` (deprecated) is `waitForEvent("load")`; the 15 s navigation
//   timeout of the original is `use.navigationTimeout`; per-wait `{ timeout }` values are
//   kept on the web-first expects.
// - `waitForFunction` polls are retrying expects (`toContainText({ useInnerText })`,
//   `toHaveURL(predicate)`, `toHaveCount`, `toBeDisabled`...). The «section is loaded» pair
//   (non-empty and no «Cargando…») is `expectLoaded`; «the wiki tree shows something» is an
//   `expect(...).toPass()`; the ?view=gestion body check (crumb + CSV link, no summary card)
//   is three locator expects. Geometry, computed styles and focus are still read in
//   `page.evaluate` snapshots (the focus checks are snapshots on purpose: a retrying
//   `toBeFocused` would also pass when focus arrives late). The original's one-shot negative
//   reads (absent, hidden, no attribute, not disabled) stay one-shot for the same reason.
// - The editorial-recovery screenshots keep their folder (`<artifacts>/human-rounds/
//   editorial-recovery-*`, via `artifactPath`) and are also attached to the report;
//   `console.log` of the folder is a test annotation. The optional matrix screenshots
//   (TURNOS_E2E_SCREENSHOT_DIR) go through `saveShot`.
// - `fitsNoOverflow` asserts the page overflow and the list of overflowing elements as two
//   checks (one message each) instead of one boolean; the repeated `scrollWidth -
//   clientWidth` read is `horizontalOverflow`; `expectToast` is a single `toContainText`.
// - Kept as the original (behaviour, not mechanics): the /admin/pacientes test asserts that
//   the viewport is still a phone width after `captureResponsiveMatrix`, which only holds
//   while TURNOS_E2E_SCREENSHOT_DIR is unset (with it set the matrix ends at 1280px).
// - CSS locators stay CSS because the ids/classes/data-* ARE the screens' markup contract
//   that the file pins (`#sec-*`, `#adm-nav a[data-nav]`, `#kn-*`, `.cnt-*`, `.sol-*`, `#cst-*`,
//   `data-hsi-*`...), and every geometry/inert/aria/computed-style probe needs the element
//   itself. Row lookups by patient name use `locator({ hasText })`.
import fs from "node:fs/promises";
import path from "node:path";
import {
  test, expect, saveShot, holdGate, holdsFor, note, artifactPath,
  queryRequest, queryUser, queryConsultation, setEnabledModules, setTheme,
  ADMIN_USER, ADMIN_PASS,
} from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";

test.use({
  appOptions: {
    dbName: "sana_turnos_test_e2e_admin_screen", requests: true, doctorPatients: true,
    intake: true, doorMedico: "gonzalez-juan", hsi: true, queue: true,
  },
  navigationTimeout: 15_000,
});
test.describe.configure({ mode: "serial" });

// Sign in an operator through the real login form. The server owns last-screen
// restoration (a doctor may land on onboarding, an admin on any current direct
// screen), so any /admin/ page but the login counts, never a client-selected route.
async function signIn(page, user, password) {
  await page.goto("/admin/login");
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 8_000 });
  await page.locator("#gate-user").fill(user);
  await page.locator("#gate-pass").fill(password);
  await page.locator("#gate-btn").click();
  await page.waitForURL((url) => url.pathname.startsWith("/admin/") && url.pathname !== "/admin/login", { timeout: 10_000 });
}

// B9 (2026-09-10, core/deps.require_admin_screen): an HTML SCREEN without a session
// answers 303 to /admin/login?next=<screen>, not the JSON 401 -- which stays the
// contract of the API routes. `maxRedirects: 0` is mandatory: Playwright would follow
// the 303 on its own and hand back the login page with a 200.
async function assertScreenRedirectsToLogin(request, path) {
  const res = await request.get(path, { maxRedirects: 0 });
  expect(res.status(), `${path}: without a session the screen redirects to the login`).toBe(303);
  const location = res.headers().location || "";
  expect(location.startsWith("/admin/login?"), `${path}: unexpected Location (${location})`).toBe(true);
  expect(new URLSearchParams(location.split("?")[1]).get("next"),
    `${path}: the login receives ?next= with the requested screen`).toBe(path);
}

/** Horizontal overflow of the page in px (scrollWidth minus clientWidth). */
const horizontalOverflow = (page) => page.evaluate(
  () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
);

/** The section holds real content: not empty and no longer the "Cargando…" placeholder. */
async function expectLoaded(section, timeout = 10_000) {
  await expect(section).toHaveText(/\S/, { useInnerText: true, timeout });
  await expect(section).not.toContainText(/Cargando/i, { useInnerText: true, timeout });
}

/** Overflow of a table wrapper, or 999 when it is not on the page (never "fits" by absence). */
const wrapOverflow = (page, selector) => page.evaluate((sel) => {
  const wrap = document.querySelector(sel);
  return wrap ? wrap.scrollWidth - wrap.clientWidth : 999;
}, selector);

test("admin preferences use the real ES module and survive a reload", async ({ adminPage: page }) => {
  await page.goto("/admin/users");
  const search = page.locator("#usr-q");
  await expect(search).toBeVisible({ timeout: 10_000 });
  const proof = await page.evaluate(() => {
    const moduleUrl = window.SANA_STATIC["platform/admin-preferences.mjs"];
    return {
      moduleUrl,
      compatibilityGlobal: typeof window.SanaPreferences,
    };
  });
  expect(proof.moduleUrl).toMatch(/\/static\/platform\/admin-preferences\.[0-9a-f]{8}\.mjs$/);
  expect(proof.compatibilityGlobal).toBe("undefined");

  const isPreferencesPut = (response) => response.url().endsWith("/api/admin/preferences")
    && response.request().method() === "PUT";
  const saved = page.waitForResponse(isPreferencesPut);
  await search.fill("module-roundtrip");
  expect((await saved).ok(), "the preference is saved").toBe(true);
  await page.reload();
  await expect(search).toBeVisible({ timeout: 10_000 });
  await expect(search).toHaveValue("module-roundtrip");

  const cleared = page.waitForResponse(isPreferencesPut);
  await search.fill("");
  expect((await cleared).ok(), "the preference is cleared").toBe(true);
});

test("/admin/my-patients keeps an admin's doctor state in the direct URL", async ({ adminPage: page, app }) => {
  const fixture = app.fixture.doctor_patients;
  const roster = page.locator("#sec-mis-pacientes");
  await page.goto("/admin/my-patients");
  await expect(page.locator("#dp-medico")).toBeVisible({ timeout: 10_000 });
  await page.locator("#dp-medico").selectOption(fixture.juan_id);
  await expect(page).toHaveURL((url) => url.searchParams.get("doctor") === fixture.juan_id, { timeout: 10_000 });
  await expect(roster).toContainText(/Paula Roster/, { useInnerText: true, timeout: 10_000 });
  await page.reload();
  await expect(page.locator("#sec-mis-pacientes table tbody tr")).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).searchParams.get("doctor")).toBe(fixture.juan_id);
  await expect(page.locator("#sec-mis-pacientes .dp-iv-link"))
    .toHaveAttribute("href", `/admin/consult?interview=${fixture.consultation_id}`);
  for (const width of [360, 390, 430]) {
    await page.setViewportSize({ width, height: 844 });
    const overflow = await horizontalOverflow(page);
    expect(overflow, `/admin/my-patients: ${overflow}px overflow at ${width}px`).toBeLessThanOrEqual(1);
    await expect(page.locator("#sec-mis-pacientes .dp-iv-link")).toBeVisible({ timeout: 5_000 });
  }
  await page.goto(`/admin/my-patients?doctor=${fixture.juan_id}`);
  await expect(roster).toContainText(/Paula Roster/, { useInnerText: true, timeout: 10_000 });
});

test("/admin/my-patients strips a doctor's foreign query and never exposes the picker", async ({ page, app }) => {
  const fixture = app.fixture.doctor_patients;
  await signIn(page, fixture.medico_user, fixture.medico_pass);
  await page.goto(`/admin/my-patients?doctor=${fixture.carlos_id}`);
  await expect(page.locator("#sec-mis-pacientes table tbody tr")).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).search).toBe("");
  expect(await page.locator("#dp-medico").count()).toBe(0);
  await expect(page.locator("#sec-mis-pacientes")).toContainText(/Paula Roster/, { useInnerText: true });
  expect(await page.locator("#sec-mis-pacientes").innerText()).not.toMatch(/Carlos Wu/);
});

test("/admin/entrevistas renders the real content from its direct screen", async ({ adminPage: page }) => {
  await page.goto("/admin/entrevistas");
  const section = page.locator("#sec-entrevistas");
  await expect(section).toContainText(/M.dico/i, { useInnerText: true, timeout: 10_000 });
  await expect(page.locator("#ent-tbody [data-medico-row]").first()).toBeAttached();
  await expect(page.locator('#ent-tbody input[type="checkbox"][data-effective-medico]').first()).toBeVisible();
  expect(await page.locator("#ent-sistema, #ent-default-title").count()).toBe(0);
  await expect(section).toContainText(/Captura/i, { useInnerText: true });
});

test("/admin/entrevistas renders server-side for an admin session (ASGI only)", async ({ adminPage: page }) => {
  await page.goto("/admin/entrevistas");

  // The approved follow-up removes the general-default UI. Keep the shared
  // title and exercise the real renderer's effective per-doctor controls.
  await expect(page.locator('#ent-tbody input[type="checkbox"][data-effective-medico]').first())
    .toBeVisible({ timeout: 10_000 });
  await expect(page.locator("#adm-h1")).toHaveText(/Captura de s[ií]ntomas/i);
  expect(await page.locator("#sec-entrevistas h1").count()).toBe(0);
  expect(await page.locator("#ent-sistema, #ent-default-title").count()).toBe(0);
  // Like the other lists: no heading or counter over the table.
  expect(await page.locator("#sec-entrevistas h2, #ent-count").count()).toBe(0);
  await expect(page.locator('#sec-entrevistas th[data-ent-sort="nombre"]')).toHaveText(/M[eé]dico/i);

  // The server-rendered sidebar shows the entry, active.
  const navLink = page.locator('#adm-nav a[data-nav="entrevistas"]');
  await expect(navLink).toBeVisible({ timeout: 5_000 });
  await expect(navLink).toHaveClass(/\bon\b/);
});

test("/admin/prompts keeps exact prompt selection in the direct URL", async ({ adminPage: page }) => {
  await page.goto("/admin/prompts");
  await expect(page.locator("#sec-prompts .pr-card").first()).toBeVisible({ timeout: 10_000 });
  await page.locator("#sec-prompts .pr-card").first().click();
  await expect(page).toHaveURL((url) => !!url.searchParams.get("prompt"), { timeout: 10_000 });
  await expect(page.locator("#sec-prompts .pr-editor")).toBeAttached({ timeout: 10_000 });
  const selected = new URL(page.url()).searchParams.get("prompt");
  expect(selected || "").toMatch(/^[a-z0-9_]+$/);
  await page.reload();
  await expect(page.locator("#sec-prompts .pr-editor")).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).searchParams.get("prompt")).toBe(selected);
  for (const width of [360, 390, 430, 768, 834]) {
    await page.setViewportSize({ width, height: 844 });
    const overflow = await horizontalOverflow(page);
    expect(overflow, `/admin/prompts: ${overflow}px overflow at ${width}px`).toBeLessThanOrEqual(1);
  }
  await page.goto("/admin/prompts");
  await expect(page.locator("#sec-prompts .pr-card").first()).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe("/admin/prompts");
});

// Tier 5 catalog editors: the three direct screens share one module/template
// (admin_panel/admin_content_screen.py, templates/admin/content.html) and
// the shared platform/admin-content.mjs editor, dynamically imported and
// mounted on the document by the template's bootstrap. Item state is
// ?item=<id> with real history.
for (const [path, section] of [
  ["/admin/services", "servicios"],
  ["/admin/centers", "centros"],
  ["/admin/doctors", "medicos"],
]) {
  test(`${path} keeps ?item= state across click, reload and Back/Forward, and fits 360-834px`, async ({ adminPage: page }) => {
    const root = page.locator(`#sec-${section}`);
    const list = root.locator("table.cnt-table");
    const form = root.locator("#cnt-form");
    const pathAndSearch = () => new URL(page.url()).pathname + new URL(page.url()).search;
    await page.goto(path);
    await expect(list).toBeVisible({ timeout: 10_000 });
    expect(pathAndSearch(), "canonical list URL, no query").toBe(path);
    // the direct sidebar lists each editor once, on its direct path
    await expect(page.locator(`#adm-nav a[data-nav="${section}"]`)).toHaveCount(1);
    await expect(page.locator(`#adm-nav a[data-nav="${section}"]`)).toHaveAttribute("href", path);
    for (const alias of ["services", "centers", "doctors"]) {
      expect(await page.locator(`#adm-nav a[data-nav="${alias}"]`).count(), alias + " alias must not duplicate the sidebar").toBe(0);
    }
    // first item link: in-page navigation, ?item=<id>, form rendered
    const first = list.locator(`tbody a[href^="${path}?item="]`).first();
    await expect(first).toBeVisible({ timeout: 10_000 });
    const expectedHref = await first.getAttribute("href");
    await first.click();
    await expect(form).toBeVisible({ timeout: 10_000 });
    expect(pathAndSearch()).toBe(expectedHref);
    const item = new URL(page.url()).searchParams.get("item");
    expect(item && item !== "new").toBeTruthy();
    // reload keeps the same item
    await page.reload();
    await expect(form).toBeVisible({ timeout: 10_000 });
    expect(new URL(page.url()).searchParams.get("item")).toBe(item);
    // Back returns to the list, Forward to the form
    await page.goBack().catch(() => {});
    await expect(list).toBeVisible({ timeout: 10_000 });
    expect(new URL(page.url()).search).toBe("");
    await page.goForward().catch(() => {});
    await expect(form).toBeVisible({ timeout: 10_000 });
    expect(new URL(page.url()).searchParams.get("item")).toBe(item);
    // the form never overflows the page at the five widths
    for (const width of [360, 390, 430, 768, 834]) {
      await page.setViewportSize({ width, height: 844 });
      const overflow = await horizontalOverflow(page);
      expect(overflow, `${path}: ${overflow}px overflow at ${width}px (form)`).toBeLessThanOrEqual(1);
    }
    // new-item state is the stable English ?item=new
    await page.goto(path + "?item=new");
    await expect(form).toBeVisible({ timeout: 10_000 });
    expect(new URL(page.url()).searchParams.get("item")).toBe("new");
    await page.setViewportSize({ width: 1280, height: 844 });
    await page.goto(path);
    await expect(list).toBeVisible({ timeout: 10_000 });
    // Regression guard for the direct-shell extraction: catalog presentation
    // comes from admin-direct.css while SanaTable supplies resize/prefs.
    await expect(root.locator(".tbl-res").first()).toBeAttached({ timeout: 10_000 });
    const tableChrome = await page.evaluate((sid) => {
      const root = document.querySelector(`#sec-${sid}`);
      // Selection is an intentional 40px compact control track in the
      // doctor roster.  Measure readable data cells and headers instead of
      // treating that control column as ordinary text content.
      // The row's first cell starts on the panel's left edge (no inset of its
      // own, tests/e2e/admin-left-edge.spec.mjs); the rest keep the 12px track.
      const cell = root.querySelector(".cnt-table tbody td[data-col]:not(:first-child)");
      const first = root.querySelector(".cnt-table tbody tr > td:first-child");
      const head = root.querySelector(".cnt-table thead th[data-col]");
      const selection = root.querySelector(".cnt-table tbody .cnt-td-check");
      const gear = root.querySelector(".cnt-gear-btn[data-tblcol-gear]");
      const popover = root.querySelector(".cnt-gear-pop");
      return {
        padding: cell ? getComputedStyle(cell).padding : "",
        firstInset: first ? getComputedStyle(first).paddingLeft : "",
        selectionPadding: selection ? getComputedStyle(selection).padding : "",
        boundary: cell ? getComputedStyle(cell).borderBottomWidth : "",
        headerAlign: head ? getComputedStyle(head).textAlign : "",
        gearWidth: gear ? getComputedStyle(gear).width : "",
        pickerHidden: popover ? getComputedStyle(popover).display === "none" : false,
        resizeHandles: root.querySelectorAll(".tbl-res").length,
      };
    }, section);
    expect(tableChrome.padding, `${path}: readable shared table padding`).toBe("9px 12px");
    expect(tableChrome.firstInset, `${path}: the first cell starts on the left edge`).toBe("0px");
    if (section === "medicos") expect(tableChrome.selectionPadding, `${path}: selection keeps its compact control track`).toBe("9px 8px 9px 0px");
    expect(tableChrome.boundary, `${path}: rows retain a visible boundary`).toBe("1px");
    expect(tableChrome.headerAlign, `${path}: table headers align with cell text`).toBe("left");
    // 44px at every width (the product owner, 2026-09-10): the column button is a 44x44
    // touch target, no longer 40px with a pointer:coarse exception.
    expect(tableChrome.gearWidth, `${path}: column settings keeps the 44px touch target`).toBe("44px");
    expect(tableChrome.pickerHidden, `${path}: column chooser is closed until requested`).toBe(true);
    expect(tableChrome.resizeHandles, `${path}: SanaTable resize handles are present`).toBeGreaterThan(0);
    if (section === "medicos") {
      await page.setViewportSize({ width: 1440, height: 844 });
      const wideHeaders = await page.evaluate(() => Array.from(
        document.querySelectorAll("#sec-medicos .cnt-table thead th[data-col]"),
      ).filter((header) => header.textContent.replace(/[▲▼]/g, "").trim()).map((header) => {
        const headerBox = header.getBoundingClientRect();
        const walker = document.createTreeWalker(header, NodeFilter.SHOW_TEXT);
        const textRects = [];
        let node;
        while ((node = walker.nextNode())) {
          if (!node.nodeValue.trim()) continue;
          const range = document.createRange();
          range.selectNode(node);
          textRects.push(...Array.from(range.getClientRects()));
        }
        return {
          textClipped: textRects.some((rect) => rect.left < headerBox.left - 1 || rect.right > headerBox.right + 1),
          textOverflow: getComputedStyle(header).textOverflow,
        };
      }));
      expect(wideHeaders.every((header) => !header.textClipped && header.textOverflow === "clip"),
        "/admin/doctors: every visible header wraps instead of ellipsizing at 1440px").toBe(true);
      await page.setViewportSize({ width: 1280, height: 844 });
      await expect.poll(() => wrapOverflow(page, "#sec-medicos .cnt-table-wrap"), { timeout: 5_000 }).toBeLessThanOrEqual(1);
      const tableOverflow = await wrapOverflow(page, "#sec-medicos .cnt-table-wrap");
      expect(tableOverflow, `/admin/doctors: ${tableOverflow}px internal table overflow at 1280px`).toBeLessThanOrEqual(1);
    }
    await root.locator(".cnt-gear-btn[data-tblcol-gear]").click();
    await expect(root.locator(".cnt-gear-pop")).toBeVisible({ timeout: 5_000 });
    // A column change replaces the header/body. The existing SanaTable
    // instance must refit the replacement, restoring its resize handles.
    const firstHeader = root.locator(".cnt-table thead th[data-col]").first();
    await firstHeader.evaluate((th) => { th.dataset.beforeColumnChange = "1"; });
    await root.locator('.cnt-gear-pop input[type="checkbox"]').first().click();
    await expect(firstHeader).not.toHaveAttribute("data-before-column-change", "1", { timeout: 5_000 });
    await expect(firstHeader.locator(".tbl-res").first()).toBeAttached({ timeout: 5_000 });
    for (const width of [934, 1024]) {
      await page.setViewportSize({ width, height: 844 });
      await expect.poll(() => wrapOverflow(page, `#sec-${section} .cnt-table-wrap`), { timeout: 5_000 }).toBeLessThanOrEqual(1);
      const layout = await page.evaluate((sid) => {
        const root = document.querySelector(`#sec-${sid}`);
        const wrap = root.querySelector(".cnt-table-wrap");
        const cell = root.querySelector(".cnt-table tbody td[data-label]");
        const filters = root.querySelector(".cnt-toolbar-filters");
        const popover = root.querySelector(".cnt-gear-pop");
        const controls = filters ? Array.from(filters.querySelectorAll("input,select")).filter((el) => getComputedStyle(el).display !== "none") : [];
        const filterBox = filters && controls.length ? filters.getBoundingClientRect() : null;
        // A list's search opens its hover frame --adm-search-out OUTSIDE the
        // screen's left edge on purpose (negative margin-left, e331c6f5d): its
        // edge is the box's left minus that margin, where the magnifier stands.
        const leftEdge = (el) => el.getBoundingClientRect().left - Math.min(0, parseFloat(getComputedStyle(el).marginLeft) || 0);
        const filterOverflow = filterBox ? Math.max(
          filterBox.left - Math.min(...controls.map(leftEdge)),
          Math.max(...controls.map((el) => el.getBoundingClientRect().right)) - filterBox.right,
          0,
        ) : 0;
        return {
          pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          tableOverflow: wrap ? wrap.scrollWidth - wrap.clientWidth : 999,
          rowDisplay: cell ? getComputedStyle(cell).display : "",
          rowFlow: cell ? getComputedStyle(cell.parentElement).display : "",
          checkOffName: cell && cell.classList.contains("cnt-td-check") && cell.parentElement.querySelector("td[data-col=nombre]")
            ? Math.abs(cell.getBoundingClientRect().top - cell.parentElement.querySelector("td[data-col=nombre]").getBoundingClientRect().top) : 0,
          rowCell: cell ? (cell.classList.contains("cnt-td-check") ? "check" : cell.getAttribute("data-col") || "") : "",
          filterOverflow,
          filterColumns: filters ? getComputedStyle(filters).gridTemplateColumns : "",
          popoverOverflow: popover && getComputedStyle(popover).display !== "none" ? Math.max(
            -popover.getBoundingClientRect().left,
            popover.getBoundingClientRect().right - innerWidth,
            0,
          ) : 0,
          popoverOpen: popover ? getComputedStyle(popover).display === "flex" : false,
          popoverChoices: popover ? popover.querySelectorAll("input[type=checkbox]").length : 0,
        };
      }, section);
      expect(layout.pageOverflow, `${path}: ${layout.pageOverflow}px overflow at ${width}px`).toBeLessThanOrEqual(1);
      expect(layout.tableOverflow, `${path}: ${layout.tableOverflow}px table overflow at ${width}px`).toBeLessThanOrEqual(1);
      expect(layout.filterOverflow, `${path}: filters stay inside their toolbar at ${width}px`).toBeLessThanOrEqual(1);
      expect(layout.popoverOverflow, `${path}: ${layout.popoverOverflow}px open column chooser overflow at ${width}px`).toBeLessThanOrEqual(1);
      expect(layout.popoverOpen, `${path}: column chooser stays open through the ${width}px resize`).toBe(true);
      expect(layout.popoverChoices, `${path}: column chooser keeps its toggles at ${width}px`).toBeGreaterThan(0);
      if (layout.filterColumns !== "none") expect(layout.filterColumns, `${path}: filters use two bounded columns at ${width}px`).toMatch(/px .*px/);
      // Business-card stacking (d4588180, 2026-09-15; one flex rule for the
      // three catalog cards 2026-11-07): the row is a wrapping flex line, the
      // name is its own line and every other datum is a block flowing after
      // it -- never a table cell. The selection checkbox sits on the name's
      // line (UI review 2026-11-03) instead of alone above it.
      expect(layout.rowFlow, `${path}: catalog rows stack through the SanaTable 1024px cutoff`).toBe("flex");
      expect(layout.rowDisplay, `${path}: stacked cells are blocks inside the card (${layout.rowCell})`).toBe("block");
      expect(layout.checkOffName, `${path}: the checkbox sits on the name's line at ${width}px`).toBeLessThanOrEqual(12);
    }
    await page.setViewportSize({ width: 1280, height: 844 });
    for (const width of [360, 390, 430, 768, 834]) {
      await page.setViewportSize({ width, height: 844 });
      const layout = await page.evaluate((sid) => {
        const wrap = document.querySelector(`#sec-${sid} .cnt-table-wrap`);
        const firstCell = document.querySelector(`#sec-${sid} .cnt-table tbody td[data-label]`);
        return {
          pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          tableOverflow: wrap ? wrap.scrollWidth - wrap.clientWidth : 999,
          rowDisplay: firstCell ? getComputedStyle(firstCell).display : "",
          rowCell: firstCell ? (firstCell.classList.contains("cnt-td-check") ? "check" : firstCell.getAttribute("data-col") || "") : "",
          label: firstCell ? firstCell.getAttribute("data-label") : "",
        };
      }, section);
      expect(layout.pageOverflow, `${path}: ${layout.pageOverflow}px page overflow at ${width}px (list)`).toBeLessThanOrEqual(1);
      expect(layout.tableOverflow, `${path}: ${layout.tableOverflow}px table overflow at ${width}px (list)`).toBeLessThanOrEqual(1);
      expect(layout.rowDisplay, `${path}: rows must stack at ${width}px (${layout.rowCell})`).toBe("block");
      expect(layout.label, `${path}: stacked cells need visible labels at ${width}px`).toBeTruthy();
    }
  });
}

test("/admin/cola renders server-side for an admin session, the real admin-queue.js content (Tier 5 slice 5, ASGI only)", async ({ adminPage: page, app }) => {
  await page.goto("/admin/cola");

  // Painted by the REAL renderer (admin-queue.js): either appointment rows or
  // the real empty state ("No hay turnos aprobados para..."), never the
  // "Cargando…" placeholder -- same discipline as the /admin/entrevistas test
  // above (real content, not a reimplementation).
  await expectLoaded(page.locator("#sec-cola"));

  // The server-rendered title (adm_cola_screen.py) matches what admin-queue.js
  // would have written client-side -- same text, two paths (see
  // templates/admin/cola.html).
  await expect(page.locator("#adm-h1")).toHaveText(/Pacientes de hoy/i);

  // window.CURRENT_ROLE is set server-side (admin_cola_screen.py's "rol
  // template context") -- without it "Crear turno" and the interview banners
  // stay invisible even though the session DOES have permission.
  expect(await page.evaluate(() => window.CURRENT_ROLE)).toBe("admin");

  // The server-rendered sidebar shows the entry, active.
  const navLink = page.locator('#adm-nav a[data-nav="cola"]');
  await expect(navLink).toBeVisible({ timeout: 5_000 });
  await expect(navLink).toHaveClass(/\bon\b/);

  // Ported from the retired queue.e2e.mjs (Tier 3 slice 2): the seeded
  // --queue fixture's three TODAY-dated appointments, exercised through
  // the real production interaction rather than reimplemented markup.
  // 2026-11-08: the day is one list; the seeded lab appointments carry
  // «Sin médico asignado» in the doctor column, a link that narrows the
  // list to them and names them once above it.
  const { queue_pendiente, queue_presente, queue_ausente } = app.fixture;
  expect(queue_pendiente && queue_presente && queue_ausente,
    "fixture did not return the --queue seeded patient names").toBeTruthy();

  const unassigned = page.locator('#sec-cola button.queue-doctor-link[data-cola-doctor="_sin_medico"]').first();
  await expect(unassigned).toBeVisible({ timeout: 15_000 });
  await unassigned.click();
  await expect(page.locator("#sec-cola .queue-doctor-head h2")).toHaveText("Sin médico asignado");
  await expect(page.locator('#sec-cola th[data-col="medico"]')).toHaveCount(0);
  // ...and «Ver todos los médicos» brings the whole day back (the choice is
  // a saved preference: leave it as the next tests expect it).
  const saved = page.waitForResponse((res) => res.url().includes("/api/admin/preferences") && res.request().method() !== "GET");
  await page.locator("#sec-cola .queue-doctor-all").click();
  await saved;
  await expect(page.locator('#sec-cola th[data-col="medico"]')).toBeVisible();

  // Real seeded content, not an empty/loading shell -- wait for the slowest
  // of the three (GET /api/admin/day-queue resolving) before reading any row.
  await expect(page.locator("#sec-cola")).toContainText(queue_ausente, { useInnerText: true, timeout: 15_000 });

  const rowFor = (nombre) => page.locator("tr.cnt-tr", { hasText: nombre }).first();
  const done = /(^|\s)queue-tr-done(\s|$)/;

  // Attendance lives in the appointment card, not in the row (the product owner,
  // 2026-11-09): the row only carries the «done» weight; the card, opened
  // from the name, holds the two buttons or the marked verdict.
  const card = page.locator(".queue-card");
  const openCard = async (row) => {
    await row.locator(".queue-pat-link").click();
    await expect(card).toBeVisible();
  };
  const closeCard = async () => {
    await card.locator(".solmod-x").click();
    await expect(card).toHaveCount(0);
  };

  // Unmarked: not "done"; the card shows the two action buttons rather than a marked verdict.
  const rowPendiente = rowFor(queue_pendiente);
  await expect(rowPendiente).toBeVisible();
  expect(await rowPendiente.evaluate((tr) => tr.classList.contains("queue-tr-done"))).toBe(false);
  await expect(rowPendiente).not.toContainText(/Atendió|No vino/, { useInnerText: true });
  await openCard(rowPendiente);
  await expect(card.locator('[data-asis][data-valor="presente"]')).toHaveText("Atendió");
  await expect(card.locator('[data-asis][data-valor="ausente"]')).toHaveText("No vino");
  await closeCard();

  // Marked 'presente' (admin.queue.attendance.presente's label, "Atendió")
  // -- the row is "done" and the card shows the verdict, not the action buttons.
  const rowPresente = rowFor(queue_presente);
  await expect(rowPresente).toBeVisible();
  await expect(rowPresente).toHaveClass(done);
  await openCard(rowPresente);
  await expect(card.locator(".queue-attendance-marked")).toContainText("Atendió");
  await expect(card.locator("[data-asis]")).toHaveCount(0);
  await closeCard();

  // Marked 'ausente' ("No vino").
  const rowAusente = rowFor(queue_ausente);
  await expect(rowAusente).toBeVisible();
  await expect(rowAusente).toHaveClass(done);
  await openCard(rowAusente);
  await expect(card.locator(".queue-attendance-marked")).toContainText("No vino");
  await closeCard();
});

test("/admin/reportes renders server-side for an admin session, the real admin-reports.js summary landing view (Tier 5 slice 5, ASGI only)", async ({ adminPage: page }) => {
  await page.goto("/admin/reportes");

  // Painted by the REAL renderer (admin-reports.js): the "Resumen" summary landing view is
  // a card grid (.rep-sumcard) -- one per sub-report -- that even with an EMPTY
  // disposable DB degrades to "s/d / Sin datos disponibles todavía." (each of
  // the renderer's sumTile*() guarantees this), never the "Cargando…"
  // placeholder. Same discipline as /admin/cola above: real content, not a
  // markup reimplementation in the template.
  await expect(page.locator("#sec-reportes .rep-sumcard").first()).toBeAttached({ timeout: 15_000 });
  await expectLoaded(page.locator("#sec-reportes"), 15_000);

  // The server-rendered title (admin_reportes_screen.py + reportes.html)
  // matches what admin-reports.js would have written client-side -- same
  // text, two paths (see templates/admin/reportes.html).
  await expect(page.locator("#adm-h1")).toHaveText(/Reportes/i);

  // The summary grid is actually painted by the renderer (not template markup).
  const cardCount = await page.locator("#sec-reportes .rep-sumcard").count();
  expect(cardCount, `expected >=1 .rep-sumcard on the summary landing view, found ${cardCount}`).toBeGreaterThanOrEqual(1);

  // The server-rendered sidebar shows the entry, active.
  const navLink = page.locator('#adm-nav a[data-nav="reportes"]');
  await expect(navLink).toBeVisible({ timeout: 5_000 });
  await expect(navLink).toHaveClass(/\bon\b/);
});

test("/admin/reportes reloads the summary landing view from its direct route", async ({ adminPage: page }) => {
  await page.goto("/admin/reportes");
  // Same assertion as the direct screen: the summary landing view paints its .rep-sumcard
  // tiles (real renderer). Empty disposable DB -> they degrade to "s/d".
  await expect(page.locator("#sec-reportes .rep-sumcard").first()).toBeAttached({ timeout: 15_000 });
  const cardCount = await page.locator("#sec-reportes .rep-sumcard").count();
  expect(cardCount, `/admin/reportes: expected >=1 .rep-sumcard, found ${cardCount}`).toBeGreaterThanOrEqual(1);
});

test("/admin/reportes?view=gestion deep-link renders the real gestion view", async ({ adminPage: page }) => {
  await page.goto("/admin/reportes?view=gestion");
  await expect(page).toHaveURL((url) => url.search === "?view=gestion", { timeout: 5_000 });
  // Distinctive assertion: the gestion sub-report paints its breadcrumb
  // (.cnt-crumb, "Reportes / Gestión") and its solicitudes CSV link
  // (/api/admin/reports/requests.csv -- exclusive to gestion), and NOT the
  // summary grid .rep-sumcard -- proving it is the real sub-report, not the
  // summary landing view with a different hash. These targets are static markup from
  // renderGestion(), stable even with an empty disposable DB.
  const body = page.locator("#rep-body");
  await expect(body.locator(".cnt-crumb").first()).toBeAttached({ timeout: 15_000 });
  await expect(body.locator('a[href="/api/admin/reports/requests.csv"]').first()).toBeAttached({ timeout: 15_000 });
  await expect(body.locator(".rep-sumcard")).toHaveCount(0);
  await expect(page.locator("#rep-body .cnt-crumb")).toHaveText(/Reportes\s*\/\s*Gesti/i);
});

test("narrow viewport (390px): /admin/entrevistas, /admin/cola, /admin/reportes, /admin/preparations and /admin/knowledge render with no horizontal overflow (ASGI only)", async ({ adminPage: page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const [path, secId] of [["/admin/entrevistas", "sec-entrevistas"], ["/admin/cola", "sec-cola"], ["/admin/reportes", "sec-reportes"], ["/admin/preparations", "sec-preparaciones"], ["/admin/knowledge", "sec-knowledge"]]) {
    await page.goto(path);
    await expectLoaded(page.locator("#" + secId));
    // The narrow base.html shell (adm5-*, @media max-width:900px):
    // one native disclosure replaces the sidebar and starts closed.
    const overflow = await horizontalOverflow(page);
    expect(overflow, `${path}: ${overflow}px of horizontal overflow at 390px wide`).toBeLessThanOrEqual(1);
    const menu = page.locator(".adm5-menu");
    await expect(menu.locator("summary")).toBeVisible({ timeout: 5_000 });
    expect(await menu.getAttribute("open"), `${path}: compact menu must start closed`).toBeNull();
    await menu.locator("summary").click();
    await expect(page.locator("#adm-nav")).toBeVisible({ timeout: 5_000 });
    await menu.locator("summary").click();
  }
});

test("disabling intake 404s /admin/entrevistas and drops its nav entry, in both surfaces (ASGI only for the URL check)", async ({ adminPage: page, app, cleanup }) => {
  // Restore the default (everything enabled) so no later test is affected.
  cleanup(() => setEnabledModules(app.dbName, ""));
  await setEnabledModules(app.dbName, "turnos"); // intake excluded
  // config.py's cache TTL (_TTL=3s): the running server subprocess may still be
  // serving its own pre-change cached read for up to that long. Wait for the
  // change to show up instead of sleeping through the TTL.
  await expect.poll(
    async () => (await page.request.get("/admin/entrevistas", { maxRedirects: 0 })).status(),
    { message: "intake disabled: /admin/entrevistas answers 404", timeout: 15_000 },
  ).toBe(404);

  // The server-rendered/client-fetched sidebar reflects the change on a fresh load.
  await page.reload();
  await expect(page.locator('#adm-nav a[data-nav="entrevistas"]'),
    "nav-entrevistas should be gone with intake disabled").toHaveCount(0, { timeout: 8_000 });

  const resp = await page.goto("/admin/entrevistas");
  expect(resp.status()).toBe(404);
});

// ---------------------------------------------------------------------------
// Tier 5 Requests direct screen: /admin/solicitudes (admin_solicitudes_screen.py
// + templates/admin/solicitudes.html, driven by the real admin-app.js +
// admin-solicitudes.js controller). The two fictional pending rows come from
// fixture.py's --requests (seeded for the file's app). These prove the runtime
// behavior the source-contract test (admin-solicitudes-direct.test.mjs) can
// only pin on disk: a real browser loads the server-routed page, the real
// controller hydrates #list with two rows, a detail opens, and one safe
// reject persists through the real
// /api/admin/requests/<id>/reject DB path, and a phone-width viewport has no
// horizontal overflow with an operable list/detail.
// ---------------------------------------------------------------------------

// The non-empty detail panel: admin-solicitudes.js's detalleHtml() emits
// `<aside class="sol-det card" data-id="…">`; the EMPTY one is
// `<aside class="sol-det sol-det-vacia">` (no data-id). Selecting on
// `[data-id]` tells "a real request is open" apart from the placeholder.
const DET_OPEN = "aside.sol-det[data-id]";

test("/admin/solicitudes renders two rows AND a detail, hash stays empty (ASGI only)", async ({ adminPage: page }) => {
  await page.goto("/admin/solicitudes");

  // Two real rows, painted by the real controller (not template markup and
  // not "Cargando…"): the direct screen gave admin-app.js the exact DOM it
  // already drives, so bootDirectScreen() -> load() hydrates the same queue.
  await expect(page.locator("#list .sol-row")).toHaveCount(2, { timeout: 10_000 });

  // The audit fix holds at runtime: a server-routed screen never forces a
  // hash (admin-solicitudes-direct.test.mjs pins this on disk; here it is
  // proven in the real URL bar). No hash shim -> location.hash is empty.
  await expect(page).toHaveURL((url) => url.hash === "", { timeout: 5_000 });
  expect(await page.evaluate(() => location.hash)).toBe("");

  // Open the detail: clicking a row sets SEL_ID and re-renders, painting the
  // right-hand panel (detalleHtml -> aside.sol-det[data-id]). The empty
  // placeholder (.sol-det-vacia) has no data-id, so [data-id] = a real open.
  await page.locator("#list .sol-row").first().click();
  await expect(page.locator(DET_OPEN)).toBeVisible({ timeout: 8_000 });
  await expect(page.locator(DET_OPEN), "the detail panel should be painted with the id of the open request")
    .toHaveAttribute("data-id", /.+/);

  // The detail header carries the patient's name (detalleHtml's .sol-det-n),
  // proving it is the REAL per-request panel, not a static stub.
  await expect(page.locator(".sol-det-n")).toHaveText(/\S/);
});

test("rejecting one pending solicitude on /admin/solicitudes persists through the real DB path (ASGI only)", async ({ adminPage: page, app }) => {
  await page.goto("/admin/solicitudes");
  await expect(page.locator("#list .sol-row")).toHaveCount(2, { timeout: 10_000 });

  // Open the first row's detail and read which request it is, so the DB
  // assertion below targets the SAME row the browser rejected (queue order
  // is the controller's, not the fixture's).
  await page.locator("#list .sol-row").first().click();
  await expect(page.locator(DET_OPEN)).toBeVisible({ timeout: 8_000 });
  const targetId = await page.locator(DET_OPEN).getAttribute("data-id");
  const target = (app.fixture.requests || []).find((r) => String(r.id) === String(targetId));
  expect(target, `the open row (${targetId}) must be one of the seeded ones -- check fixture.requests`).toBeTruthy();

  // Click the REAL reject control. executePending() reads the pre-selected
  // .motivo-sel (defaultMotivo() always yields a non-empty standard motivo
  // for a request without an order) and POSTs
  // /api/admin/requests/<id>/reject { motivo } -- the same path the legacy
  // page takes. No confirm() dialog on the single-row reject (only bulk/HSI
  // use one), so no dialog handler is needed.
  await page.locator(`${DET_OPEN} [data-act="rechazar"]`).click();

  // Durable success signal: the rejected row leaves "Pendientes" (estado ->
  // 'rechazada'), so the live queue re-renders from 2 rows to 1. The toast
  // (#toast, role=status) is transient; the row count is the persisted truth.
  await expect(page.locator("#list .sol-row")).toHaveCount(1, { timeout: 10_000 });
  // The controller writes the confirmation to the #toast announcement on every resolve.
  await expect(page.locator("#toast")).toContainText(/rechazad/i, { timeout: 8_000 });

  // The real DB gate: read the row back through the app's own read path
  // (db._connect/_wire, via fixture.py's query-request) and assert the
  // estado the /reject route wrote. This is what makes the proof end-to-end
  // -- the browser action reached the database, not just the DOM.
  const row = await queryRequest(app.dbName, target.dni);
  expect(row.found, "the rejected request must still exist in booking_requests").toBe(true);
  expect(row.estado, `expected estado 'rechazada', got '${row.estado}'`).toBe("rechazada");
});

test("narrow viewport (360px): /admin/solicitudes has no horizontal overflow and an operable list/detail (ASGI only)", async ({ adminPage: page }) => {
  await page.setViewportSize({ width: 360, height: 844 });
  await page.goto("/admin/solicitudes");
  // Order-independent: the reject test runs before this one (the file is
  // serial) and permanently drops the seeded pending count from 2 to 1, so
  // this test must tolerate whichever count the earlier reject left behind.
  // The exact two-row proof lives in the dedicated test above; here "the
  // queue hydrated with real rows" is enough to validate the narrow layout.
  await expect(page.locator("#list .sol-row").first()).toBeAttached({ timeout: 10_000 });
  expect(await page.locator("#list .sol-row").count()).toBeGreaterThanOrEqual(1);

  // No horizontal overflow at phone width: admin-solicitudes.js's injected
  // @media(max-width:760px) collapses the row grid to checkbox + IA-verdict
  // + name and hides the rest, so 360px never stretches past the viewport.
  const overflow = await horizontalOverflow(page);
  expect(overflow, `/admin/solicitudes: ${overflow}px of horizontal overflow at 360px wide`).toBeLessThanOrEqual(1);

  // Operable list/detail: a tap on a row opens the detail. Below 1200px the
  // detail is a full-screen sheet (position:fixed;inset:0); the ✕ in the name
  // row (.sol-det-x, critical review 2026-11-08) is the way back on a phone,
  // so it must be present, reachable and at least 44px to the finger.
  await page.locator("#list .sol-row").first().click();
  await expect(page.locator(DET_OPEN)).toBeVisible({ timeout: 8_000 });
  const closeBtn = page.locator(DET_OPEN).getByRole("button", { name: "Cerrar" });
  await expect(closeBtn).toBeVisible({ timeout: 5_000 });
  const box = await closeBtn.boundingBox();
  expect(box.width).toBeGreaterThanOrEqual(44);
  expect(box.height).toBeGreaterThanOrEqual(44);

  // And closing returns to the list: the detail sheet disappears.
  await closeBtn.click();
  await expect(page.locator(DET_OPEN)).toHaveCount(0, { timeout: 5_000 });
  // The list is still there after closing the sheet.
  expect(await page.locator("#list .sol-row").count()).toBeGreaterThanOrEqual(1);
});

// ---------------------------------------------------------------------------
// Tier 5 Preparations direct screen: /admin/preparations
// (admin_preparations_screen.py + templates/admin/preparations.html),
// driven by the real admin-preparations.js. Legacy #preparaciones stays
// on both modes; the English path is ASGI-only (404 in legacy, pinned).
// ---------------------------------------------------------------------------

const PREP_WIDTHS = [
  { width: 360, height: 740 },
  { width: 390, height: 844 },
  { width: 430, height: 932 },
];

async function waitForPrepList(page) {
  const section = page.locator("#sec-preparaciones");
  await expectLoaded(section);
  await expect(section.locator("table tbody tr").first()).toBeAttached({ timeout: 10_000 });
}

test("/admin/preparations renders the real list from its direct route", async ({ adminPage: page }) => {
  await page.goto("/admin/preparations");
  await waitForPrepList(page);
  await expect(page.locator("#adm-h1.cnt-h1")).toHaveText(/C.mo prepararse|How to prepare/i);
  const rows = await page.locator("#sec-preparaciones table tbody tr").count();
  expect(rows, `/admin/preparations: expected >0 table rows, found ${rows}`).toBeGreaterThan(0);
});

test("/admin/preparations boots the list without a hash (ASGI only)", async ({ adminPage: page }) => {
  await page.goto("/admin/preparations");
  expect(await page.evaluate(() => location.hash)).toBe("");
  await waitForPrepList(page);
  await expect(page.locator("#pa-nueva")).toBeVisible({ timeout: 5_000 });
  await expect(page.locator('#adm-nav a[data-nav="preparaciones"].on').first(),
    "nav link for preparaciones must carry .on").toBeAttached();

  const visibleH1 = await page.evaluate(() => {
    return [...document.querySelectorAll("h1")].filter((h) => {
      const s = getComputedStyle(h);
      return s.display !== "none" && s.visibility !== "hidden" && h.offsetParent !== null;
    }).map((h) => h.className);
  });
  expect(visibleH1.length, `expected one visible h1, found ${visibleH1.length}`).toBe(1);
  expect(visibleH1[0].includes("cnt-h1"), "the visible h1 must be the owned shared title").toBe(true);
  expect(await page.evaluate(() => getComputedStyle(document.getElementById("adm-h1")).display),
    "the shared title and its alert attachment band remain visible").not.toBe("none");
  expect(await page.locator("#sec-preparaciones h1").count(),
    "the screen must not recreate the duplicate heading below the shared band").toBe(0);

  for (const vp of PREP_WIDTHS) {
    await page.setViewportSize(vp);
    const overflow = await horizontalOverflow(page);
    expect(overflow, `/admin/preparations list: ${overflow}px overflow at ${vp.width}px`).toBeLessThanOrEqual(0);
    await expect(page.locator("#sec-preparaciones table tbody tr:first-child a")).toBeVisible({ timeout: 5_000 });
  }

  await page.locator("#sec-preparaciones table tbody tr:first-child a").click();
  await expect(page.locator("#pa-nombre")).toBeVisible({ timeout: 8_000 });
  const a11y = await page.evaluate(() => {
    const root = document.getElementById("sec-preparaciones");
    const fields = [...root.querySelectorAll("input, textarea")];
    const unlabeled = fields.filter((el) => !(el.labels && el.labels.length > 0)).map((el) => el.id);
    const msg = document.getElementById("pa-msg");
    const actions = [...root.querySelectorAll(".pa-actions *")].filter((el) =>
      el.id && ["pa-guardar", "pa-restaurar", "pa-habilitar", "pa-deshabilitar"].includes(el.id)
    );
    return {
      unlabeled,
      role: msg && msg.getAttribute("role"),
      live: msg && msg.getAttribute("aria-live"),
      actionTags: actions.map((el) => el.tagName),
      anchorsInActions: root.querySelectorAll(".pa-actions a").length,
    };
  });
  expect(a11y.unlabeled.length, `unlabelled fields: ${a11y.unlabeled.join(",")}`).toBe(0);
  expect(a11y.role).toBe("status");
  expect(a11y.live, "aria-live must be non-empty").toBeTruthy();
  expect(a11y.actionTags.every((tag) => tag === "BUTTON"), `action tags: ${a11y.actionTags}`).toBe(true);
  expect(a11y.anchorsInActions).toBe(0);
});

test("/admin/preparations save + deep-link reload and editor overflow (ASGI only)", async ({ adminPage: page, baseURL }) => {
  await page.goto("/admin/preparations");
  await waitForPrepList(page);
  const firstLink = page.locator("#sec-preparaciones table tbody tr:first-child a");
  const href = await firstLink.getAttribute("href");
  expect(href).toMatch(/^\/admin\/preparations\?item=/);
  const itemId = new URL(href, baseURL).searchParams.get("item");
  expect(itemId, "preparation link must carry an item query").toBeTruthy();
  await firstLink.click();
  await expect(page.locator("#pa-nombre")).toBeVisible({ timeout: 8_000 });
  const marker = `gate-r-${Date.now()}`;
  await page.locator("#pa-resumen").fill(marker);
  const [put] = await Promise.all([
    page.waitForResponse(
      (r) => /\/api\/admin\/content\/preparations\//.test(r.url()) && r.request().method() === "PUT",
      { timeout: 15_000 },
    ),
    page.locator("#pa-guardar").click(),
  ]);
  expect(put.ok(), `PUT preparations failed: ${put.status()}`).toBe(true);
  await expect(page).toHaveURL((url) => url.searchParams.get("item") === itemId && url.hash === "", { timeout: 10_000 });
  await expect(page.locator("#pa-nombre")).toBeAttached({ timeout: 10_000 });
  await page.reload();
  await expect(page.locator("#pa-nombre")).toBeVisible({ timeout: 10_000 });
  expect(await page.evaluate(() => new URLSearchParams(location.search).get("item"))).toBe(itemId);
  await expect(page.locator("#pa-resumen")).toHaveValue(marker);

  for (const vp of PREP_WIDTHS) {
    await page.setViewportSize(vp);
    const overflow = await horizontalOverflow(page);
    expect(overflow, `/admin/preparations editor: ${overflow}px overflow at ${vp.width}px`).toBeLessThanOrEqual(0);
    await expect(page.locator("#pa-guardar")).toBeVisible({ timeout: 5_000 });
  }
});

// ---------------------------------------------------------------------------
// Tier 5 Knowledge/Wiki direct screen: /admin/knowledge
// (admin_knowledge_screen.py + templates/admin/knowledge.html),
// driven by the real admin-knowledge.js. Legacy #knowledge/<id> stays
// on both modes; the English path is ASGI-only. Direct item state is
// ?item=<id> (pushState + popstate), not a hash.
// ---------------------------------------------------------------------------

const KN_WIDTHS = [
  { width: 360, height: 740 },
  { width: 390, height: 844 },
  { width: 430, height: 932 },
  { width: 768, height: 1024 },
  { width: 834, height: 1112 },
];

test("direct editorial screens expose reload recovery when their fingerprinted controller import fails", async ({ adminPage: page, baseURL, cleanup }, testInfo) => {
  const artifactRoot = artifactPath("human-rounds");
  await fs.mkdir(artifactRoot, { recursive: true });
  const evidence = await fs.mkdtemp(path.join(artifactRoot, "editorial-recovery-"));
  note(testInfo, `Editorial recovery screenshots: ${evidence}`);
  // The synthetic language setup is stored in the file's database: always put the
  // fixture language back, even when the test fails.
  cleanup(async () => {
    const reset = await page.request.post("/api/admin/settings", { data: {
      grupo: "languages", valores: { LANGUAGES: "es", LANG_DEFAULT: "es", LANG_SWITCHER_MODE: "secret" },
    } });
    expect(reset, "restore synthetic fixture language").toBeOK();
  });
  for (const language of ["es", "en"]) {
    const settings = await page.request.post("/api/admin/settings", { data: {
      grupo: "languages", valores: { LANGUAGES: "es,en", LANG_DEFAULT: language, LANG_SWITCHER_MODE: "secret" },
    } });
    expect(settings, "synthetic editorial language setup").toBeOK();
    await page.context().addCookies([{ name: "lang", value: language, url: baseURL }]);
    for (const [screen, key, section, recovered] of [
      ["/admin/prompts", "platform/admin-prompts.mjs", "sec-prompts", "#sec-prompts .pr-card"],
      ["/admin/legal", "platform/admin-legal.mjs", "sec-legal", "#sec-legal .lg-card"],
      ["/admin/knowledge", "platform/admin-knowledge.mjs", "sec-knowledge", "#sec-knowledge #kn-tree a.kn-node"],
    ]) {
      // Read this screen's own manifest before installing the one-shot route.
      // A missing manifest key is a test failure, never a null URL that happens
      // not to intercept anything.
      await page.goto(screen);
      const modulePath = await page.evaluate((k) => window.SANA_STATIC && window.SANA_STATIC[k], key);
      expect(typeof modulePath, `${key} is present in the static manifest`).toBe("string");
      expect(modulePath.length, `${key} has a fingerprinted URL`).toBeGreaterThan(0);
      const moduleUrl = new URL(modulePath, baseURL).href;
      let failed = false;
      await page.route("**/*", (route) => {
        if (!failed && route.request().url() === moduleUrl) { failed = true; return route.abort("failed"); }
        return route.continue();
      });
      await page.goto(screen);
      const retry = page.locator(`#${section} [data-retry]`);
      await expect(retry).toBeVisible({ timeout: 10_000 });
      expect(failed, `${key} request was intercepted`).toBe(true);
      await expect(page.locator("html")).toHaveAttribute("lang", language);
      await expect(retry).toHaveText(language === "en" ? "Reload" : "Recargar");
      await expect(page.locator(`#${section} [data-error-message]`))
        .toHaveText(language === "en" ? "Could not load." : "No se pudo cargar.");
      await page.unroute("**/*");
      await Promise.all([
        page.waitForEvent("load", { timeout: 10_000 }),
        retry.click(),
      ]);
      await expect(page.locator(recovered).first()).toBeVisible({ timeout: 10_000 });
      expect(await page.locator(`#${section} [data-retry]`).count(), `${key} recovered real UI after reload`).toBe(0);
      await fitsNoOverflow(page, screen + " recovered editor");
      for (const width of [390, 1280]) {
        await page.setViewportSize({ width, height: 900 });
        await saveShot(testInfo, page, path.join(evidence, `${section}-${language}-${width}.png`), { fullPage: true });
      }
    }
  }
});

async function waitForKnowledgeTree(page) {
  const tree = page.locator("#sec-knowledge #kn-tree");
  await expect(tree).toBeAttached({ timeout: 10_000 });
  // Either a real node or some text (e.g. the empty state).
  await expect(async () => {
    const hasNode = (await tree.locator("a.kn-node").count()) > 0;
    const text = ((await tree.textContent()) || "").trim();
    expect(hasNode || text.length > 0, "the wiki tree shows a node or a message").toBe(true);
  }).toPass({ timeout: 10_000 });
}

test("/admin/knowledge renders the real wiki tree from its direct route", async ({ adminPage: page }) => {
  await page.goto("/admin/knowledge");
  await waitForKnowledgeTree(page);
  // Wiki uses the shared direct-screen title; its list has no duplicate
  // inner heading after the compact navigation redesign.
  await expect(page.locator("#adm-h1")).toHaveText(/Wiki/i);
  expect(await page.locator("#sec-knowledge .adm-h1").count(),
    "the editor begins with its editable title, not a redundant Wiki heading").toBe(0);
  expect(await page.locator("#sec-knowledge #kn-search").count()).toBeTruthy();
  await expect(page.locator("#sec-knowledge a.kn-node").first()).toHaveAttribute("href", /^\/admin\/knowledge\?item=/);
});

test("/admin/knowledge starts compact, searches contextually, and preserves item history", async ({ adminPage: page }) => {
  await page.goto("/admin/knowledge");
  await waitForKnowledgeTree(page);
  expect(await page.locator("#kn-tree [data-kn-toggle][aria-expanded='true']").count(),
    "categories start collapsed so navigation stays compact").toBe(0);
  const toggle = page.locator("#kn-tree [data-kn-toggle]").first();
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  const node = page.locator("#kn-tree a.kn-node").first();
  const title = (await node.innerText()).trim().split(/\s+/).slice(0, 2).join(" ");
  await page.locator("#kn-search").fill(title);
  await expect(page.locator("#kn-tree [data-kn-toggle][aria-expanded='true']").first()).toBeAttached({ timeout: 5_000 });
  await node.click();
  await expect(page.locator("#kn-form")).toBeVisible({ timeout: 8_000 });
  expect(new URL(page.url()).searchParams.get("item"), "selection writes an item deep link").toBeTruthy();
  await page.goBack();
  await expect(page).toHaveURL((url) => !url.searchParams.get("item"), { timeout: 8_000 });
});

test("/admin/knowledge boots the wiki without a hash and keeps item state in ?item= (ASGI only)", async ({ adminPage: page }) => {
  const overflowNow = () => horizontalOverflow(page);
  const hasItem = (id) => (url) => url.searchParams.get("item") === id && url.hash === "";
  await page.goto("/admin/knowledge");
  expect(await page.evaluate(() => location.hash)).toBe("");
  await waitForKnowledgeTree(page);
  await expect(page.locator('#adm-nav a[data-nav="knowledge"].on').first(),
    "nav link for knowledge must carry .on").toBeAttached();

  const nodeCount = await page.locator("#sec-knowledge a.kn-node").count();
  expect(nodeCount, "wiki tree must have at least one item").toBeGreaterThan(0);
  await page.locator("#sec-knowledge a.kn-node").first().click();
  await expect(page).toHaveURL((url) => !!url.searchParams.get("item") && url.hash === "", { timeout: 8_000 });
  const item = new URL(page.url()).searchParams.get("item");
  expect(item, "direct click must write ?item=").toBeTruthy();
  await expect(page.locator("#kn-form")).toBeVisible({ timeout: 8_000 });
  const desktop = await page.evaluate(() => {
    const editor = document.getElementById("kn-editor");
    const list = document.querySelector(".kn-list");
    return {
      role: editor && editor.getAttribute("role"),
      listInert: !!(list && list.hasAttribute("inert")),
    };
  });
  expect(desktop.role, "desktop split pane must not become a dialog").toBeNull();
  expect(desktop.listInert, "desktop list must not be inert").toBe(false);
  await page.reload();
  await expect(page.locator("#kn-form")).toBeVisible({ timeout: 10_000 });
  const after = await page.evaluate(() => ({
    item: new URLSearchParams(location.search).get("item"),
    hash: location.hash,
  }));
  expect(after.item).toBe(item);
  expect(after.hash).toBe("");

  for (const vp of KN_WIDTHS) {
    await page.setViewportSize(vp);
    await page.goto("/admin/knowledge");
    await waitForKnowledgeTree(page);
    let overflow = await overflowNow();
    expect(overflow, `/admin/knowledge list: ${overflow}px overflow at ${vp.width}px`).toBeLessThanOrEqual(0);
    await page.locator("#sec-knowledge a.kn-node").first().click();
    await expect(page).toHaveURL(hasItem(item), { timeout: 8_000 });
    await expect(page.locator("#kn-form")).toBeVisible({ timeout: 8_000 });
    overflow = await overflowNow();
    expect(overflow, `/admin/knowledge editor: ${overflow}px overflow at ${vp.width}px`).toBeLessThanOrEqual(0);
    await page.goBack();
    await expect(page).toHaveURL((url) => !url.searchParams.get("item") && url.hash === "", { timeout: 8_000 });
    await waitForKnowledgeTree(page);
    overflow = await overflowNow();
    expect(overflow, `/admin/knowledge back-to-list: ${overflow}px overflow at ${vp.width}px`).toBeLessThanOrEqual(0);
    await page.goForward();
    await expect(page).toHaveURL(hasItem(item), { timeout: 8_000 });
    await expect(page.locator("#kn-form")).toBeVisible({ timeout: 8_000 });
    expect(new URL(page.url()).searchParams.get("item"), `Forward must restore the same item at ${vp.width}px`).toBe(item);
    overflow = await overflowNow();
    expect(overflow, `/admin/knowledge forward-item: ${overflow}px overflow at ${vp.width}px`).toBeLessThanOrEqual(0);
    await page.keyboard.press("Escape");
    await expect(page).toHaveURL((url) => !url.searchParams.get("item"), { timeout: 8_000 });
    await expect(page.locator("#kn-form")).toHaveCount(0, { timeout: 8_000 });
    // A snapshot on purpose: focus must already be back on the row once the editor is gone.
    const restoredAfterForward = await page.evaluate(() => {
      const active = document.activeElement;
      return !!(active && active.classList.contains("kn-node"));
    });
    expect(restoredAfterForward, `Escape after Forward must restore item focus at ${vp.width}px`).toBe(true);
  }
});

test("/admin/knowledge <=900px editor dialog keyboard lifecycle (ASGI only)", async ({ adminPage: page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/admin/knowledge");
  await waitForKnowledgeTree(page);
  await page.locator("#sec-knowledge a.kn-node").first().click();
  await expect(page.locator("#kn-form")).toBeVisible({ timeout: 8_000 });
  const openState = await page.evaluate(() => {
    const editor = document.getElementById("kn-editor");
    const list = document.querySelector(".kn-list");
    const side = document.querySelector(".adm5-side");
    const sizes = [...editor.querySelectorAll("#kn-form input, #kn-form select, #kn-form textarea")]
      .filter((el) => el.type !== "hidden")
      .map((el) => parseFloat(getComputedStyle(el).fontSize));
    const search = document.getElementById("kn-search");
    return {
      role: editor.getAttribute("role"),
      modal: editor.getAttribute("aria-modal"),
      listInert: list.hasAttribute("inert"),
      sideInert: !!(side && side.hasAttribute("inert")),
      focusInside: editor.contains(document.activeElement),
      minFieldPx: sizes.length ? Math.min.apply(null, sizes) : 0,
      searchPx: search ? parseFloat(getComputedStyle(search).fontSize) : 0,
    };
  });
  expect(openState.role).toBe("dialog");
  expect(openState.modal).toBe("true");
  expect(openState.listInert, "covered list must be inert while the dialog is open").toBe(true);
  expect(openState.sideInert, "covered nav tree must be inert while the dialog is open").toBe(true);
  expect(openState.focusInside, "focus must enter the editor dialog").toBe(true);
  expect(openState.minFieldPx, `editor controls ${openState.minFieldPx}px`).toBeGreaterThanOrEqual(16);
  expect(openState.searchPx, `search ${openState.searchPx}px`).toBeGreaterThanOrEqual(16);
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL((url) => !url.searchParams.get("item"), { timeout: 8_000 });
  await expect(page.locator("#kn-form")).toHaveCount(0, { timeout: 8_000 });
  const closed = await page.evaluate(() => {
    const editor = document.getElementById("kn-editor");
    const list = document.querySelector(".kn-list");
    const active = document.activeElement;
    return {
      role: editor && editor.getAttribute("role"),
      listInert: !!(list && list.hasAttribute("inert")),
      restored: !!(active && active.classList.contains("kn-node")),
    };
  });
  expect(closed.role).toBeNull();
  expect(closed.listInert).toBe(false);
  expect(closed.restored, "Escape must restore focus to the triggering wiki row").toBe(true);
});

const SYSTEM_FIXTURE = {
  estado: "warn",
  ok: 1,
  total: 2,
  clusters: [
    {
      clave: "application",
      titulo: "Application",
      estado: "ok",
      resumen: "Ready",
      items: [{ label: "Database", valor: "Connected", estado: "ok" }],
    },
    {
      clave: "email",
      titulo: "Email",
      estado: "warn",
      resumen: "Setup required",
      items: [{
        label: "Sending domain",
        valor: "Not configured",
        estado: "warn",
        detalle: "Use a verified domain before sending patient notifications.",
        campo: "MAILGUN_DOMAIN",
      }],
    },
  ],
};

async function stubSystemStatus(page) {
  await page.route("**/api/admin/system", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(SYSTEM_FIXTURE),
  }));
}

test("/admin/system renders the existing status UI without overflow (ASGI only)", async ({ adminPage: page }) => {
  await stubSystemStatus(page);
  await page.goto("/admin/system");
  await expect(page.locator("#sec-sistema .sis-card").first()).toBeVisible({ timeout: 8_000 });
  await expect(page.locator('#adm-nav a[data-nav="sistema"]')).toHaveAttribute("href", "/admin/system");
  await expect(page.locator(".sis-fix")).toHaveAttribute("href", "/admin/settings?field=MAILGUN_DOMAIN");
  for (const width of [360, 390, 430, 768, 834]) {
    await page.setViewportSize({ width, height: 844 });
    const stateAtWidth = await page.evaluate(() => {
      const detail = document.querySelector(".sis-detalle");
      return {
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        detailColor: detail ? getComputedStyle(detail).color : "",
        cards: document.querySelectorAll("#sec-sistema .sis-card").length,
      };
    });
    expect(stateAtWidth.overflow, `/admin/system: ${stateAtWidth.overflow}px overflow at ${width}px`).toBeLessThanOrEqual(1);
    expect(stateAtWidth.cards).toBe(2);
    expect(stateAtWidth.detailColor, "detail text must not use the old light gray").not.toBe("rgb(138, 138, 133)");
  }
});

test("/admin/system keeps its configuration deep link on the direct route", async ({ adminPage: page }) => {
  await stubSystemStatus(page);
  await page.goto("/admin/system");
  await expect(page.locator("#sec-sistema .sis-card").first()).toBeVisible({ timeout: 8_000 });
  await expect(page.locator(".sis-fix")).toHaveAttribute("href", "/admin/settings?field=MAILGUN_DOMAIN");
});

const SETTINGS_FIXTURE = {
  lock: false,
  location: {
    cadena_actual: ["global", "example-town"],
    paquetes_disponibles: [
      { id: "global", name: "Global", level: 0, parent: "" },
      { id: "example-town", name: "Example Town", level: 3, parent: "global" },
    ],
    modo_paquetes: true,
    identidad: {
      hospital: "Example Hospital",
      city: "Example Town",
      province: "Example Province",
      network: "Example Network",
      network_long: "Example Public Health Network",
      emergency_number: "000",
    },
  },
  languages: {
    disponibles: ["en", "es"],
    habilitados: ["en", "es"],
    default: "en",
    modo_switcher: "nav",
  },
  chat: { greetings: [], max_length: 1200 },
  turnos: { max_activos: 1 },
  seguridad: { ips_exentas: [] },
  // settings_admin._payload always includes explicit connector state.
  connectors: { emr_sync: { selected: null, source: "disabled", hsi_enabled: false } },
  api_keys: [
    {
      id: "mailgun",
      titulo: "Mailgun",
      probar: "mailgun",
      campos: [
        { clave: "MAILGUN_DOMAIN", etiqueta: "Sending domain", tipo: "text", valor: "" },
        { clave: "MAILGUN_API_KEY", etiqueta: "API key", tipo: "secret", configurado: false, last4: null },
      ],
    },
  ],
};

async function stubSettings(page) {
  await page.route("**/api/admin/settings", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(SETTINGS_FIXTURE),
  }));
}

test("/admin/settings owns query state, history, field focus, and responsive fit (ASGI only)", async ({ adminPage: page }) => {
  const integrationCard = page.locator('[data-integracion-card="mailgun"]');
  const locationCard = page.locator('.set-card[data-grupo="location"]');
  await stubSettings(page);
  await page.goto("/admin/settings");
  await expect(page.locator('#sec-settings .set-card[data-grupo="location"]')).toBeVisible({ timeout: 8_000 });
  await expect(page.locator('#adm-nav a[data-nav="settings"]')).toHaveAttribute("href", "/admin/settings");
  await expect(page.locator('[data-settings-view="general"]')).toHaveAttribute("aria-current", "page");
  expect(await page.locator('[data-settings-view="integrations"]').getAttribute("aria-current")).toBeNull();

  await page.locator('[data-settings-view="integrations"]').click();
  await expect(integrationCard).toBeVisible({ timeout: 5_000 });
  expect(new URL(page.url()).search).toBe("?view=integrations");
  await expect(page.locator('[data-settings-view="integrations"]')).toHaveAttribute("aria-current", "page");
  await page.locator('[data-settings-view="general"]').click();
  await expect(locationCard).toBeVisible({ timeout: 5_000 });
  expect(new URL(page.url()).search).toBe("");
  await page.goBack();
  await expect(integrationCard).toBeVisible({ timeout: 5_000 });
  expect(new URL(page.url()).search).toBe("?view=integrations");
  await page.goForward();
  await expect(locationCard).toBeVisible({ timeout: 5_000 });

  await page.goto("/admin/settings?view=general&field=MAILGUN_DOMAIN");
  const domainInput = page.locator('[data-campo="MAILGUN_DOMAIN"] input');
  await expect(domainInput).toBeVisible({ timeout: 8_000 });
  expect(new URL(page.url()).search).toBe("?field=MAILGUN_DOMAIN");
  // A snapshot on purpose: focus must already be on the field once it shows.
  expect(await domainInput.evaluate((el) => el === document.activeElement)).toBe(true);
  await page.goto("/admin/settings?view=integrations&field=SITE_ID_CITY");
  await expect(page.locator('[data-campo="SITE_ID_CITY"] input')).toBeVisible({ timeout: 8_000 });
  expect(new URL(page.url()).search).toBe("?field=SITE_ID_CITY");

  for (const view of ["", "?view=integrations"]) {
    await page.goto("/admin/settings" + view);
    await expect(view ? integrationCard : locationCard).toBeVisible({ timeout: 8_000 });
    for (const width of [360, 390, 430, 768, 834]) {
      await page.setViewportSize({ width, height: 844 });
      const layout = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        minControl: Math.min(...Array.from(document.querySelectorAll("#sec-settings input,#sec-settings select,#sec-settings textarea"), (el) => parseFloat(getComputedStyle(el).fontSize))),
      }));
      expect(layout.overflow, `/admin/settings${view}: ${layout.overflow}px overflow at ${width}px`).toBeLessThanOrEqual(1);
      expect(layout.minControl, `/admin/settings${view}: controls ${layout.minControl}px at ${width}px`).toBeGreaterThanOrEqual(16);
    }
  }
});

test("/admin/settings direct URLs retain integration and field state", async ({ adminPage: page }) => {
  await stubSettings(page);
  await page.goto("/admin/settings?view=integrations");
  await expect(page.locator('[data-integracion-card="mailgun"]')).toBeVisible({ timeout: 8_000 });
  await page.goto("/admin/settings?field=SITE_ID_CITY");
  await expect(page.locator('[data-campo="SITE_ID_CITY"] input')).toBeVisible({ timeout: 8_000 });
});

test("/admin/users owns create state, history, reload, and responsive fit (ASGI only)", async ({ adminPage: page }) => {
  await page.goto("/admin/users");
  await expect(page.locator("#usr-tbody tr").first()).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).search).toBe("");
  await expect(page.locator('#adm-nav a[data-nav="usuarios"]')).toHaveAttribute("href", "/admin/users");
  await expect(page.locator('#adm-nav a[data-nav="usuarios"]')).toHaveClass("on");
  await expect(page.locator('#usr-tbody a[href="/admin/account"]')).toHaveCount(1);

  for (const width of [360, 390, 430, 768, 834]) {
    await page.setViewportSize({ width, height: 900 });
    const layout = await page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      minControl: Math.min(...Array.from(
        document.querySelectorAll(".cnt-toolbar-filters input"),
        (el) => parseFloat(getComputedStyle(el).fontSize),
      )),
      // Filter selects are the shared compact label+chevron control
      // (11ef515a, 2026-09-15: 15px, sized to the chosen option).
      filterFonts: Array.from(
        document.querySelectorAll(".cnt-toolbar-filters select.adm-sel"),
        (el) => parseFloat(getComputedStyle(el).fontSize),
      ),
    }));
    expect(layout.overflow, `/admin/users: ${layout.overflow}px overflow at ${width}px`).toBeLessThanOrEqual(1);
    expect(layout.minControl, `/admin/users: controls ${layout.minControl}px at ${width}px`).toBeGreaterThanOrEqual(16);
    expect(layout.filterFonts.length, `/admin/users: filter selects exist at ${width}px`).toBeGreaterThan(0);
    expect([...new Set(layout.filterFonts)], `/admin/users: compact filters at ${width}px`).toEqual([15]);
  }

  await page.locator('a[data-users-state="new"]').click();
  await expect(page.locator("#usr-form")).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).search).toBe("?new=1");
  expect(new URL(page.url()).hash).toBe("");
  await page.reload();
  await expect(page.locator("#usr-form")).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).search).toBe("?new=1");

  for (const width of [360, 390, 430, 768, 834]) {
    await page.setViewportSize({ width, height: 900 });
    const layout = await page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      minControl: Math.min(...Array.from(
        document.querySelectorAll("#sec-usuarios input:not([type=checkbox]):not([type=radio]),#sec-usuarios select,#sec-usuarios textarea"),
        (el) => parseFloat(getComputedStyle(el).fontSize),
      )),
    }));
    expect(layout.overflow, `/admin/users?new=1: ${layout.overflow}px overflow at ${width}px`).toBeLessThanOrEqual(1);
    expect(layout.minControl, `/admin/users?new=1: controls ${layout.minControl}px at ${width}px`).toBeGreaterThanOrEqual(16);
  }

  await page.goBack();
  await expect(page.locator("#usr-tbody tr").first()).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).search).toBe("");
  await page.goForward();
  await expect(page.locator("#usr-form")).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).search).toBe("?new=1");
});

test("/admin/users?new=1 renders the create state from the direct URL", async ({ adminPage: page }) => {
  await page.goto("/admin/users?new=1");
  await expect(page.locator("#usr-form")).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe("/admin/users?new=1");
});

// Tier 5 platform-core Account: direct routed screen. Same
// controller (admin-account.js), same APIs; state is bare /admin/account or
// /admin/account?setup=2fa with real history. Passkey registration itself
// needs the platform WebAuthn prompt (a CDP virtual authenticator); this gate
// covers the screen, the 2FA setup state and the list/add control, not the
// ceremony.
test("/admin/account renders the one account UI and keeps ?setup=2fa state across click, reload and Back/Forward (ASGI only)", async ({ adminPage: page }) => {
  const fits = async (label) => {
    for (const width of [360, 390, 430, 768, 834, 1024, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      const layout = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        qrContained: (() => {
          const svg = document.querySelector("#acct-2fa-setup .acct-qr svg");
          if (!svg) return true;
          const qr = svg.getBoundingClientRect();
          const plate = svg.parentElement.getBoundingClientRect();
          return qr.left >= plate.left && qr.right <= plate.right + 1
            && qr.top >= plate.top && qr.bottom <= plate.bottom + 1;
        })(),
        overflowers: Array.from(document.querySelectorAll("body *"), (el) => {
          const rect = el.getBoundingClientRect();
          return rect.right > document.documentElement.clientWidth + 1
            ? { tag: el.tagName, id: el.id, className: String(el.className || ""), right: rect.right }
            : null;
        }).filter(Boolean).slice(0, 10),
        minFont: Math.min(...Array.from(
          document.querySelectorAll("#sec-cuenta input"),
          (el) => parseFloat(getComputedStyle(el).fontSize),
        )),
        minTouch: Math.min(...Array.from(
          document.querySelectorAll("#sec-cuenta button, #adm-logout"),
          (el) => el.getBoundingClientRect(),
        ).filter((rect) => rect.width > 0 && rect.height > 0)
          .map((rect) => Math.min(rect.width, rect.height))),
      }));
      expect(layout.overflow, `${label}: ${layout.overflow}px overflow at ${width}px; ${JSON.stringify(layout.overflowers)}`).toBeLessThanOrEqual(1);
      expect(layout.overflowers, `${label}: overflowing elements at ${width}px`).toEqual([]);
      expect(layout.qrContained, `${label}: clipped QR at ${width}px`).toBe(true);
      expect(layout.minFont, `${label}: inputs ${layout.minFont}px at ${width}px`).toBeGreaterThanOrEqual(16);
      if (width <= 834) {
        expect(layout.minTouch, `${label}: touch control ${layout.minTouch}px at ${width}px`).toBeGreaterThanOrEqual(44);
      }
    }
    await page.setViewportSize({ width: 1280, height: 900 });
  };
  const currentSearch = () => new URL(page.url()).search;
  await page.goto("/admin/account");
  // Cuenta is a view: the email is a value with its «Editar», no open field.
  await expect(page.locator("#acct-email-edit")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator("#acct-email-form")).toBeHidden();
  await expect(page.locator("#acct-password-form")).toBeHidden();
  expect(currentSearch()).toBe("");
  expect(new URL(page.url()).hash).toBe("");
  await expect(page.locator('#adm-nav a[data-nav="cuenta"]')).toHaveAttribute("href", "/admin/account");
  await expect(page.locator('#adm-nav a[data-nav="cuenta"]')).toHaveClass("on");
  // one h1, the two blocks an admin has (Datos de ingreso, Seguridad), sign-out in the footer
  await expect(page.locator("h1#adm-h1")).toHaveCount(1);
  await expect(page.locator("#sec-cuenta section.acct-card")).toHaveCount(2);
  expect(await page.locator("#sec-cuenta .card, #sec-cuenta [role=tab]").count()).toBe(0);
  await expect(page.locator("#adm-logout")).toBeVisible({ timeout: 5_000 });
  await expect(page.locator("#acct-keys-list li").first()).toBeVisible({ timeout: 10_000 });
  await fits("/admin/account");

  // unknown / duplicate query state canonicalizes safely to the bare screen
  for (const junk of ["?foo=1", "?setup=2fa&setup=2fa", "?setup=2fa&foo=1", "?setup=x"]) {
    await page.goto("/admin/account" + junk);
    await expect(page.locator("#acct-email-edit")).toBeVisible({ timeout: 10_000 });
    expect(currentSearch(), `${junk} must canonicalize to bare`).toBe("");
    expect(await page.locator("#acct-confirm-form").count(), `${junk} must not open the setup`).toBe(0);
  }

  const has2fa = (await page.locator("#acct-2fa-quitar").count()) === 1;
  if (!has2fa) {
    // the in-page activation pushes the canonical setup URL
    await page.goto("/admin/account");
    await expect(page.locator("#acct-2fa-start")).toBeVisible({ timeout: 10_000 });
    await page.locator("#acct-2fa-start").click();
    await expect(page.locator("#acct-confirm-form")).toBeVisible({ timeout: 10_000 });
    expect(currentSearch()).toBe("?setup=2fa");
    expect(new URL(page.url()).hash).toBe("");
    // the QR block and the code field fit and stack on narrow screens
    await fits("/admin/account?setup=2fa");
    const setup = page.locator("#acct-2fa-setup");
    // The app fallback links only Google Authenticator's official stores.
    await expect(setup).toContainText(/Instalá Google Authenticator en tu teléfono/, { useInnerText: true });
    await expect(setup).toContainText(/Google Authenticator en App Store/, { useInnerText: true });
    await expect(setup).toContainText(/código de 6 dígitos/i, { useInnerText: true });
    await expect(page.locator("#acct-2fa-setup .acct-steps li")).toHaveCount(4);
    await expect(page.locator("#acct-2fa-setup .acct-secret code")).toHaveCount(1);
    const qr = await page.locator("#acct-2fa-setup .acct-qr svg").evaluate((svg) => ({
      viewBox: svg.getAttribute("viewBox"),
      width: svg.getAttribute("width"),
      height: svg.getAttribute("height"),
      rect: svg.getBoundingClientRect().toJSON(),
      plate: svg.parentElement.getBoundingClientRect().toJSON(),
    }));
    expect(qr.viewBox || "").toMatch(/^0 0 \d+ \d+$/);
    expect(qr.width).toBeNull();
    expect(qr.height).toBeNull();
    expect(qr.rect.left >= qr.plate.left && qr.rect.right <= qr.plate.right + 1).toBe(true);
    expect(qr.rect.top >= qr.plate.top && qr.rect.bottom <= qr.plate.bottom + 1).toBe(true);
    // reload reproduces the setup state
    await page.reload();
    await expect(page.locator("#acct-confirm-form")).toBeVisible({ timeout: 10_000 });
    expect(currentSearch()).toBe("?setup=2fa");
    // Back closes it, Forward reopens it
    await page.goBack().catch(() => {});
    await expect(page.locator("#acct-2fa-start")).toBeVisible({ timeout: 10_000 });
    expect(currentSearch()).toBe("");
    expect(await page.locator("#acct-confirm-form").count()).toBe(0);
    await page.goForward().catch(() => {});
    await expect(page.locator("#acct-confirm-form")).toBeVisible({ timeout: 10_000 });
    expect(currentSearch()).toBe("?setup=2fa");
    // the direct 2FA notice is a real link to the setup URL
    await expect(page.locator("#adm-2fa-note a.adm-2fa-go")).toHaveAttribute("href", "/admin/account?setup=2fa");
  }

  await page.goto("/admin/account");
  await expect(page.locator("#sec-cuenta #acct-email-edit")).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).pathname + currentSearch()).toBe("/admin/account");
  await expect(page.locator('#adm-nav a[data-nav="cuenta"]')).toHaveAttribute("href", "/admin/account");
  if (!has2fa) {
    await page.goto("/admin/account?setup=2fa");
    await expect(page.locator("#sec-cuenta #acct-confirm-form")).toBeVisible({ timeout: 10_000 });
    expect(currentSearch()).toBe("?setup=2fa");
    await fits("/admin/account?setup=2fa");
  }
});

test("/admin/account sign-out returns to direct login and the session is gone (ASGI only)", async ({ adminPage: page }) => {
  await page.goto("/admin/account");
  await expect(page.locator("#adm-logout")).toBeVisible({ timeout: 10_000 });
  // Same race as the direct-screen sign-out below: a first read still in
  // flight gets its 401 after the logout and aborts the login navigation.
  await page.waitForLoadState("networkidle", { timeout: 15_000 });
  await Promise.all([
    page.waitForURL((url) => url.pathname === "/admin/login", { timeout: 15_000 }),
    page.locator("#adm-logout").click(),
  ]);
  await assertScreenRedirectsToLogin(page.request, "/admin/account");
  expect(new URL(page.url()).pathname).toBe("/admin/login");
});

test("a successful direct screen survives real browser sign-out and sign-in", async ({ adminPage: page }) => {
  await page.goto("/admin/legal");
  await expect.poll(() => page.evaluate(() => window.SANA_ADMIN_SCREEN), { timeout: 10_000 }).toBe("legal");
  // Let the screen's own first reads finish with a valid session: under
  // gate load a late one got its 401 after the logout and started a
  // redirect to login that aborted the explicit navigation below.
  await page.waitForLoadState("networkidle", { timeout: 15_000 });

  // Use the real logout endpoint from the loaded browser session without
  // visiting Account first: Account itself is a successful direct screen
  // and would correctly become the new saved destination.
  const logoutStatus = await page.evaluate(async () => {
    const response = await fetch("/api/admin/logout", {
      method: "POST", credentials: "same-origin",
    });
    return response.status;
  });
  expect(logoutStatus).toBe(200);

  await page.goto("/admin/login");
  await page.locator("#gate-user").fill(ADMIN_USER);
  await page.locator("#gate-pass").fill(ADMIN_PASS);
  await page.locator("#gate-btn").click();
  await page.waitForURL(/\/admin\/legal$/, { timeout: 15_000 });
  await expect.poll(() => page.evaluate(() => window.SANA_ADMIN_SCREEN)).toBe("legal");
});

// Tier 5 platform-core Legal: direct routed screen (terms +
// privacy editor). Same controller (admin-legal.js), same /api/admin/legal
// API; state is bare /admin/legal (two cards) or /admin/legal?doc=<id>
// (editor) with real history. Guardar/Restaurar are NOT clicked here (a
// mutation behind window.confirm; PUT/DELETE are covered by
// tests/test_legal_db.py) -- this gate covers the screen and the ?doc= state.
test("/admin/legal lists the two documents, keeps ?doc= state across click, reload and Back/Forward, and fits 360-834px (ASGI only)", async ({ adminPage: page }) => {
  const fits = async (label) => {
    for (const width of [360, 390, 430, 768, 834]) {
      await page.setViewportSize({ width, height: 900 });
      const overflow = await horizontalOverflow(page);
      expect(overflow, `${label}: ${overflow}px overflow at ${width}px`).toBeLessThanOrEqual(1);
    }
    await page.setViewportSize({ width: 1280, height: 900 });
  };
  // Editor-only mobile gates (both shells share the injected CSS): the
  // textarea is >= 16px (no iOS focus zoom) and EVERY visible action in
  // .lg-actions -- Save button and the Restore text link -- has a >= 44px
  // usable target at the three phone widths.
  const editorFits = async (label) => {
    for (const width of [360, 390, 430]) {
      await page.setViewportSize({ width, height: 900 });
      const m = await page.evaluate(() => {
        const ta = document.getElementById("lg-cuerpo");
        const targets = Array.from(document.querySelectorAll("#sec-legal .lg-actions a, #sec-legal .lg-actions button"))
          .map((el) => ({ id: el.id || el.textContent.trim(), rect: el.getBoundingClientRect(), tag: el.tagName, cls: el.className }))
          .filter((t) => t.rect.width > 0 && t.rect.height > 0)
          .map((t) => ({ id: t.id, w: t.rect.width, h: t.rect.height, tag: t.tag, cls: t.cls }));
        return { font: ta ? parseFloat(getComputedStyle(ta).fontSize) : 0, targets };
      });
      expect(m.font, `${label}: textarea ${m.font}px at ${width}px`).toBeGreaterThanOrEqual(16);
      expect(m.targets.length, `${label}: no visible action at ${width}px`).toBeGreaterThanOrEqual(1);
      expect(m.targets.some((t) => t.id === "lg-guardar"), `${label}: Save missing at ${width}px`).toBe(true);
      for (const t of m.targets) {
        expect(Math.min(t.w, t.h), `${label}: action ${t.id} ${t.w}x${t.h} < 44px at ${width}px`).toBeGreaterThanOrEqual(44);
      }
      // Restore, when present, is still a text link -- never a button or card
      const restore = m.targets.find((t) => t.id === "lg-restaurar");
      if (restore) {
        expect(restore.tag).toBe("A");
        expect(restore.cls).not.toMatch(/\bbtn\b/);
      }
    }
    await page.setViewportSize({ width: 1280, height: 900 });
  };
  const cards = page.locator("#sec-legal .lg-card");
  const editor = page.locator("#sec-legal #lg-cuerpo");
  const crumb = page.locator('#adm-h1 a.adm-crumb[href="/admin/legal"]');
  await page.goto("/admin/legal");
  await expect(cards.first()).toBeVisible({ timeout: 10_000 });
  await expect(cards).toHaveCount(2);
  expect(new URL(page.url()).search).toBe("");
  expect(new URL(page.url()).hash).toBe("");
  await expect(page.locator('#adm-nav a[data-nav="legal"]')).toHaveAttribute("href", "/admin/legal");
  await expect(page.locator('#adm-nav a[data-nav="legal"]')).toHaveClass("on");
  await expect(page.locator("h1#adm-h1")).toHaveCount(1);
  await expect(page.locator("h1#adm-h1")).toHaveText(/\S/);
  // nothing invented: no tabs, no dividers, no extra cards beyond the two documents
  expect(await page.locator("#sec-legal [role=tab], #sec-legal hr").count()).toBe(0);
  await fits("/admin/legal");

  // Unknown query state canonicalizes to the bare list. Fragments are not
  // client routing state and are intentionally not interpreted here.
  for (const junk of ["?foo=1", "?doc="]) {
    await page.goto("/admin/legal" + junk);
    await expect(cards.first()).toBeVisible({ timeout: 10_000 });
    expect(new URL(page.url()).search + new URL(page.url()).hash, `${junk} must canonicalize to bare`).toBe("");
  }

  // first card: in-page navigation, ?doc=terminos, editor rendered, no reload
  await page.goto("/admin/legal");
  await expect(cards.first()).toBeVisible({ timeout: 10_000 });
  const first = page.locator('#sec-legal a.lg-card[href="/admin/legal?doc=terminos"]');
  await expect(first).toHaveCount(1);
  await page.evaluate(() => { window.__legalNoReload = true; });
  await first.click();
  await expect(editor).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe("/admin/legal?doc=terminos");
  expect(await page.evaluate(() => window.__legalNoReload === true), "card click must not reload the page").toBe(true);
  await expect(editor).toHaveAttribute("aria-label", ((await page.locator("h1#adm-h1").textContent()) || "").split("/").pop().trim());
  await expect(page.locator("#sec-legal #lg-guardar")).toHaveCount(1);
  await expect(crumb).toHaveCount(1);
  await fits("/admin/legal?doc=terminos");
  await editorFits("/admin/legal?doc=terminos");
  // reload keeps the editor
  await page.reload();
  await expect(editor).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).searchParams.get("doc")).toBe("terminos");
  // Back returns to the list, Forward to the editor
  await page.goBack().catch(() => {});
  await expect(cards.first()).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).search).toBe("");
  await page.goForward().catch(() => {});
  await expect(editor).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).searchParams.get("doc")).toBe("terminos");
  // the crumb goes back to the list in place
  await crumb.click();
  await expect(cards.first()).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).search).toBe("");
  // the second document is reachable by URL too
  await page.goto("/admin/legal?doc=privacidad");
  await expect(editor).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).searchParams.get("doc")).toBe("privacidad");

  // an unknown document: the status text plus a link back to the list
  await page.goto("/admin/legal?doc=nope");
  const back = page.locator('#sec-legal .adm-empty a[href="/admin/legal"]');
  await expect(back).toBeVisible({ timeout: 10_000 });
  expect(await editor.count()).toBe(0);
  await back.click();
  await expect(cards.first()).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).search).toBe("");

  await page.goto("/admin/legal");
  await expect(cards.first()).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe("/admin/legal");
  await expect(page.locator('#adm-nav a[data-nav="legal"]')).toHaveAttribute("href", "/admin/legal");
  await expect(page.locator('#sec-legal a.lg-card[href="/admin/legal?doc=terminos"]')).toHaveCount(1);
  await page.goto("/admin/legal?doc=terminos");
  await expect(editor).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).search).toBe("?doc=terminos");
  await editorFits("/admin/legal?doc=terminos");
});

// Tier 5 platform-core Appearance: direct routed screen
// ("Apariencia y acceso"). Native admin-appearance.mjs, same five APIs
// (theme/auth-mode/turnstile turnos, chat-voice chat, branding platform);
// state is the bare /admin/appearance. NO mutation clicks here -- theme,
// sign-in mode, anti-bot and brand are global POSTs (covered by their own
// Python tests) -- this gate covers the screen, the canonical URL, the mobile
// targets, and the per-block module degradation (API routes stubbed in-page,
// no module toggling).
test("/admin/appearance renders the five blocks, canonicalises junk state, degrades per block, and fits 360-834px (ASGI only)", async ({ adminPage: page }) => {
  const fits = async (label) => {
    for (const width of [360, 390, 430, 768, 834]) {
      await page.setViewportSize({ width, height: 900 });
      const overflow = await horizontalOverflow(page);
      expect(overflow, `${label}: ${overflow}px overflow at ${width}px`).toBeLessThanOrEqual(1);
    }
    await page.setViewportSize({ width: 1280, height: 900 });
  };
  // Mobile gates (both shells share the injected CSS): the dictation state
  // line is >= 16px; the reset text links have a >= 44px target at the three
  // phone widths; the logo picker (the panel's button over a hidden file
  // input) and Save colour are the panel's buttons — one height for every
  // button (2026-11-08, R4): 36px under this run's mouse, 44px on a touch
  // screen (pointer:coarse, pinned in tests/admin-button-system.test.mjs).
  const controlsFit = async (label) => {
    for (const width of [360, 390, 430]) {
      await page.setViewportSize({ width, height: 900 });
      const m = await page.evaluate(() => {
        const model = document.getElementById("cfg-voice-model");
        const ids = ["cfg-mk-pick", "cfg-mk-save", "cfg-mk-reset", "cfg-mk-quitar"];
        const targets = ids.map((id) => document.getElementById(id)).filter(Boolean)
          .map((el) => ({ id: el.id, rect: el.getBoundingClientRect(), tag: el.tagName, cls: el.className }))
          .filter((t) => t.rect.width > 0 && t.rect.height > 0)
          .map((t) => ({ id: t.id, w: t.rect.width, h: t.rect.height, tag: t.tag, cls: t.cls }));
        return {
          font: model ? parseFloat(getComputedStyle(model).fontSize) : 0,
          targets,
        };
      });
      expect(m.font, `${label}: dictation state ${m.font}px at ${width}px`).toBeGreaterThanOrEqual(16);
      expect(m.targets.some((t) => t.id === "cfg-mk-pick"), `${label}: logo picker missing at ${width}px`).toBe(true);
      expect(m.targets.some((t) => t.id === "cfg-mk-save"), `${label}: Save colour missing at ${width}px`).toBe(true);
      for (const t of m.targets) {
        const minH = /(^| )btn( |$)/.test(t.cls) ? 36 : 44;
        expect(t.w, `${label}: control ${t.id} ${t.w}px wide at ${width}px`).toBeGreaterThanOrEqual(44);
        expect(t.h, `${label}: control ${t.id} ${t.h}px tall at ${width}px`).toBeGreaterThanOrEqual(minH);
        // Reset is a mutation, not navigation: native buttons retain a
        // keyboard action and an explicit non-submit type.
        if (t.id === "cfg-mk-reset" || t.id === "cfg-mk-quitar") {
          expect(t.tag).toBe("BUTTON");
          await expect(page.locator("#" + t.id)).toHaveAttribute("type", "button");
        }
      }
    }
    await page.setViewportSize({ width: 1280, height: 900 });
  };
  const h2s = page.locator("#sec-configuracion h2.cfg-h2");
  const themeCards = page.locator("#sec-configuracion .cfg-theme[data-tema]");
  const status = page.locator("#configuracion-status");
  await page.goto("/admin/appearance");
  await expect(h2s.first()).toBeVisible({ timeout: 10_000 });
  await expect(h2s, "all five blocks with every module on").toHaveCount(5);
  expect(new URL(page.url()).search).toBe("");
  expect(new URL(page.url()).hash).toBe("");
  await expect(page.locator('#adm-nav a[data-nav="configuracion"]')).toHaveAttribute("href", "/admin/appearance");
  await expect(page.locator('#adm-nav a[data-nav="configuracion"]')).toHaveClass("on");
  await expect(page.locator("h1#adm-h1")).toHaveCount(1);
  await expect(page.locator("h1#adm-h1")).toHaveText(/\S/);
  // nothing invented: no tabs, no dividers; the status line is hidden when all is well
  expect(await page.locator("#sec-configuracion [role=tab], #sec-configuracion hr").count()).toBe(0);
  expect(await status.isHidden()).toBe(true);
  // option cards expose their pressed state; exactly one theme card is pressed
  await expect(page.locator('#sec-configuracion .cfg-theme[data-tema][aria-pressed="true"]')).toHaveCount(1);
  await expect(page.locator('#sec-configuracion .cfg-theme[data-tema][aria-pressed="false"]').first()).toBeAttached();
  await fits("/admin/appearance");
  await controlsFit("/admin/appearance");

  // Unknown query state canonicalises to the bare path. Fragments are not
  // client routing state and are intentionally not interpreted here.
  for (const junk of ["?foo=1", "?tema=pinamar"]) {
    await page.goto("/admin/appearance" + junk);
    await expect(h2s.first()).toBeVisible({ timeout: 10_000 });
    expect(new URL(page.url()).search + new URL(page.url()).hash, `${junk} must canonicalize to bare`).toBe("");
  }

  // module degradation WITHOUT toggling modules: a 404 on the chat-voice
  // GET (what require_module_enabled answers with chat off) removes ONLY
  // the voice block and shows no status line
  await page.route("**/api/admin/chat-voice", (r) => r.fulfill({ status: 404, contentType: "application/json", body: "{}" }));
  await page.reload();
  await expect(h2s.first()).toBeVisible({ timeout: 10_000 });
  await expect(h2s, "voice block absent on 404").toHaveCount(4);
  expect(await page.locator("#cfg-voice-model").count()).toBe(0);
  await expect(page.locator("#cfg-mk-save"), "brand block still present").toHaveCount(1);
  await expect(themeCards.first(), "theme block still present").toBeAttached();
  expect(await status.isHidden(), "a 404 is silent").toBe(true);
  await page.unroute("**/api/admin/chat-voice");
  // a 5xx on the theme GET removes the theme block and shows ONE reload line
  await page.route("**/api/admin/theme", (r) => r.fulfill({ status: 500, contentType: "application/json", body: "{}" }));
  await page.reload();
  await expect(h2s.first()).toBeVisible({ timeout: 10_000 });
  await expect(h2s, "theme block absent on 500").toHaveCount(4);
  expect(await themeCards.count()).toBe(0);
  await expect(page.locator("#cfg-voice-model"), "voice block back").toHaveCount(1);
  await expect(status).toBeVisible({ timeout: 10_000 });
  await expect(status).toHaveText(/\S/);
  await page.unroute("**/api/admin/theme");
  // Only the server's explicit 2FA-repair response changes location. An
  // ordinary role/demo 403 remains a local error in the controller.
  await page.route("**/api/admin/branding", (r) => r.fulfill({ status: 403, contentType: "application/json", body: '{"error":"2fa_requerido"}' }));
  await Promise.all([
    page.waitForURL(/\/admin\/account\?setup=2fa$/, { timeout: 10_000 }),
    page.reload(),
  ]);
  await expect(page.locator("#sec-cuenta")).toBeVisible({ timeout: 10_000 });
  await page.unroute("**/api/admin/branding");

  await page.goto("/admin/appearance");
  await expect(h2s.first()).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe("/admin/appearance");
  await expect(page.locator('#adm-nav a[data-nav="configuracion"]')).toHaveAttribute("href", "/admin/appearance");
  await expect(h2s).toHaveCount(5);
  await controlsFit("/admin/appearance");
});

// Tier 5 turnos-owned HSI: direct routed screen ("Sincronización con
// HSI"). Native lifecycle-owned controller (static/platform/admin-hsi.mjs;
// the classic root admin-hsi.js is retired), same
// three APIs (GET /api/admin/hsi-sync, POST .../resolver, POST .../conflicts);
// state is the bare /admin/hsi. The harness database has no HSI conflicts and
// TURNOS_SKIP_HSI=1, so the panel payload is stubbed in-page (page.route) --
// this gate covers the screen, the canonical URL, the stacked mobile table
// and its 16px/44px controls, the load-failure line with Retry, the session
// boundary, and a resolution click re-rendering from the POST's panel.
test("/admin/hsi renders the conflicts panel, canonicalises junk state, stacks within a narrow container, recovers from a failed load, and keeps the session boundary local (ASGI only)", async ({ adminPage: page }) => {
  const panel = (abiertos) => ({
    abiertos,
    metricas: { futuras: 12, pct_futuras: 83 },
    conflictos: abiertos ? [{
      conflicto_id: 7101, fecha: "2026-09-03 10:30", solicitud_id: 1, paciente: "PACIENTE DE PRUEBA",
      medico: "DRA. PRUEBA", problema: "El turno en HSI quedó en otro horario que el local.",
      resoluciones: [
        { accion: "mover", label: "Mover al primer turno libre" },
        { accion: "sobreturno", label: "Dejar como sobreturno" },
        { accion: "rechazar", label: "Rechazar la solicitud" },
      ],
    }] : [],
    resueltos: [{ cuando: "2026-08-21T09:00:00-03:00", texto: "Se cerró un duplicado en HSI." }],
  });
  const syncBody = (abiertos) => JSON.stringify({ counts: {}, outbox: [], conflicts: [], hsi_activo: false, panel: panel(abiertos) });
  const stubSync = async (abiertos) => {
    await page.unroute("**/api/admin/hsi-sync");
    await page.route("**/api/admin/hsi-sync", (r) => r.fulfill({ status: 200, contentType: "application/json", body: syncBody(abiertos) }));
  };
  const fits = async (label) => {
    for (const width of [360, 390, 430, 768, 834]) {
      await page.setViewportSize({ width, height: 900 });
      const overflow = await horizontalOverflow(page);
      expect(overflow, `${label}: ${overflow}px overflow at ${width}px`).toBeLessThanOrEqual(1);
    }
    await page.setViewportSize({ width: 1280, height: 900 });
  };
  // Native container gates: within a <=900px container the table
  // stacks (thead visually hidden, cells block with their column label, the
  // patient first), the alternatives select is >=16px/44px, the recommended
  // button and Volver a revisar are >=44px. The desktop container still
  // renders a real table; tablet widths stack rather than clip controls.
  const stackedFits = async (label) => {
    for (const width of [360, 390, 430, 768, 834, 1024]) {
      await page.setViewportSize({ width, height: 900 });
      const m = await page.evaluate(() => {
        const td = document.querySelector("#sec-hsi .hsi-tabla td.hsi-td-pac");
        const prob = document.querySelector("#sec-hsi .hsi-tabla td.hsi-td-prob");
        const thead = document.querySelector("#sec-hsi .hsi-tabla thead");
        const sel = document.querySelector("#sec-hsi .hsi-alt");
        const btn = document.querySelector("#sec-hsi [data-hsi-apply]");
        const rescan = document.getElementById("hsi-rescan-btn");
        const rect = (el) => el ? el.getBoundingClientRect() : { width: 0, height: 0 };
        return {
          tdDisplay: td ? getComputedStyle(td).display : "",
          label: prob ? getComputedStyle(prob, "::before").content : "",
          theadH: thead ? thead.getBoundingClientRect().height : -1,
          selFont: sel ? parseFloat(getComputedStyle(sel).fontSize) : 0,
          selH: rect(sel).height, btnH: rect(btn).height, btnW: rect(btn).width,
          rescanH: rect(rescan).height, rescanW: rect(rescan).width,
          firstCell: td && td.parentElement ? td.parentElement.firstElementChild.className : "",
        };
      });
      expect(m.tdDisplay, `${label}: cells stack at ${width}px`).toBe("block");
      expect(m.label, `${label}: cell label at ${width}px`).toMatch(/Problema|Problem/);
      expect(m.theadH, `${label}: header row visually hidden at ${width}px`).toBeLessThanOrEqual(1);
      expect(m.selFont, `${label}: select ${m.selFont}px at ${width}px`).toBeGreaterThanOrEqual(16);
      expect(m.selH, `${label}: select ${m.selH}px tall at ${width}px`).toBeGreaterThanOrEqual(44);
      expect(Math.min(m.btnH, m.btnW), `${label}: resolution button ${m.btnW}x${m.btnH} at ${width}px`).toBeGreaterThanOrEqual(44);
      expect(Math.min(m.rescanH, m.rescanW), `${label}: rescan ${m.rescanW}x${m.rescanH} at ${width}px`).toBeGreaterThanOrEqual(44);
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    expect(await page.evaluate(() => getComputedStyle(document.querySelector("#sec-hsi .hsi-tabla td.hsi-td-pac")).display),
      `${label}: a real table at 1280px`).toBe("table-cell");
  };
  const table = page.locator("#sec-hsi .hsi-tabla");
  const hsiStatus = page.locator("#hsi-status");
  const applyMove = page.locator('#sec-hsi [data-hsi-apply][data-accion="mover"]');
  await stubSync(1);
  await page.goto("/admin/hsi");
  await expect(table).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).search).toBe("");
  expect(new URL(page.url()).hash).toBe("");
  await expect(page.locator('#adm-nav a[data-nav="hsi"]')).toHaveAttribute("href", "/admin/hsi");
  await expect(page.locator('#adm-nav a[data-nav="hsi"]')).toHaveClass("on");
  await expect(page.locator("h1#adm-h1")).toHaveCount(1);
  await expect(page.locator("h1#adm-h1")).toHaveText(/HSI/);
  await expect(page.locator("#sec-hsi .hsi-hero")).toHaveText(/83%/);
  await expect(page.locator("#sec-hsi .hsi-tabla tbody tr")).toHaveCount(1);
  // the patient link goes to the Solicitudes screen, no query state, no hash
  await expect(page.locator("#sec-hsi .hsi-td-pac a")).toHaveAttribute("href", "/admin/solicitudes");
  await expect(page.locator("#sec-hsi .hsi-td-pac a")).toHaveText("Paciente de Prueba");
  await expect(page.locator("#sec-hsi .hsi-td-med")).toHaveText("Dra. Prueba");
  expect(await page.locator("#sec-hsi [data-hsi-request]").count()).toBe(0);
  // recommended action as the button, the rest in the select (with a name)
  await expect(applyMove).toHaveCount(1);
  await expect(page.locator("#sec-hsi .hsi-alt option")).toHaveCount(3);
  await expect(page.locator("#sec-hsi .hsi-alt")).toHaveAttribute("aria-label", /.+/);
  // nothing invented: no tabs, no dividers; the status line is hidden when all is well
  expect(await page.locator("#sec-hsi [role=tab], #sec-hsi hr").count()).toBe(0);
  expect(await hsiStatus.isHidden()).toBe(true);
  await expect(hsiStatus).toHaveAttribute("role", "status");
  await expect(hsiStatus).toHaveAttribute("aria-live", "polite");
  await fits("/admin/hsi");
  await stackedFits("/admin/hsi");

  // Unknown query state canonicalises to the bare path. Fragments are not
  // client routing state and are intentionally not interpreted here.
  for (const junk of ["?foo=1", "?conflicto=7"]) {
    await page.goto("/admin/hsi" + junk);
    await expect(table).toBeVisible({ timeout: 10_000 });
    expect(new URL(page.url()).search + new URL(page.url()).hash, `${junk} must canonicalize to bare`).toBe("");
  }

  // a resolution click: the POST's panel re-renders the section (no
  // conflicts left), the result lands in the local status line, the
  // controls are usable again. The rejection path is not clicked here (it
  // opens a confirm dialog, executed in admin-hsi-lifecycle.e2e.mjs).
  await page.route("**/api/admin/hsi-sync/conflicts", (r) => r.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ ok: true, resultados: [{ conflicto_id: 7101, ok: true, detalle: "Movido al primer turno libre." }], panel: panel(0) }),
  }));
  await stubSync(0);
  await applyMove.click();
  await expect(page.locator("#sec-hsi .hsi-vacio")).toBeVisible({ timeout: 10_000 });
  await expect(hsiStatus).toBeVisible({ timeout: 10_000 });
  await expect(hsiStatus).toHaveText(/Movido al primer turno libre/);
  expect(await table.count()).toBe(0);
  await page.unroute("**/api/admin/hsi-sync/conflicts");

  // a failed apply re-enables every control and says so
  await stubSync(1);
  await page.reload();
  await expect(table).toBeVisible({ timeout: 10_000 });
  await page.route("**/api/admin/hsi-sync/conflicts", (r) => r.fulfill({ status: 503, contentType: "application/json", body: '{"error":"hsi_no_disponible"}' }));
  await applyMove.click();
  await expect(hsiStatus).toBeVisible({ timeout: 10_000 });
  await expect(hsiStatus).toHaveText(/HSI no responde|HSI is not responding/);
  expect(await page.locator("#sec-hsi [data-hsi-apply]:disabled, #sec-hsi .hsi-alt:disabled").count(),
    "controls re-enabled after a failure").toBe(0);
  await page.unroute("**/api/admin/hsi-sync/conflicts");

  // a 5xx on the GET never leaves "Cargando…": one load-error line with a
  // Retry control; once the API answers again, Retry draws the panel
  await page.unroute("**/api/admin/hsi-sync");
  await page.route("**/api/admin/hsi-sync", (r) => r.fulfill({ status: 500, contentType: "application/json", body: "{}" }));
  await page.reload();
  await expect(page.locator("#hsi-retry-btn")).toBeVisible({ timeout: 10_000 });
  // Read once, the moment Retry shows: an auto-waiting expect would let a
  // lingering "Cargando…" clear later and still pass.
  expect(await table.count()).toBe(0);
  expect((await page.locator("#sec-hsi").textContent()) || "").not.toMatch(/Cargando|Loading/);
  const retryBox = await stableBox(page.locator("#hsi-retry-btn"), "HSI retry button");
  expect(Math.min(retryBox.width, retryBox.height)).toBeGreaterThanOrEqual(44);
  await stubSync(1);
  await page.locator("#hsi-retry-btn").click();
  await expect(table).toBeVisible({ timeout: 10_000 });
  // Only exact 2fa_requerido changes screen; generic 403s stay local.
  await page.unroute("**/api/admin/hsi-sync");
  await page.route("**/api/admin/hsi-sync", (r) => r.fulfill({ status: 403, contentType: "application/json", body: '{"error":"2fa_requerido"}' }));
  await Promise.all([
    page.waitForURL(/\/admin\/account\?setup=2fa$/, { timeout: 10_000 }),
    page.reload(),
  ]);
  await expect(page.locator("#sec-cuenta")).toBeVisible({ timeout: 10_000 });

  await stubSync(1);
  await page.goto("/admin/hsi");
  await expect(table).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe("/admin/hsi");
  await expect(page.locator('#adm-nav a[data-nav="hsi"]')).toHaveAttribute("href", "/admin/hsi");
  await expect(page.locator("h1#adm-h1")).toHaveText(/HSI/);
  await expect(page.locator('#sec-hsi .hsi-td-pac a[href="/admin/solicitudes"]')).toHaveCount(1);
  await stackedFits("/admin/hsi");
});

// ---------------------------------------------------------------------------
// Tier 5 remaining turnos/intake/platform direct screens: /admin/mis-turnos,
// /admin/pacientes, /admin/consult and /admin/plan. Same contract as the
// screens above, proven through the same real browser: cold load renders the
// REAL controller's content (never a hand-rolled template copy), the role
// gate answers server-side before any HTML, ONE key user action persists
// through the real API+DB path, state survives reload, history goes where
// the screen actually keeps state, and nothing overflows the five-width
// responsive matrix (360/390/768/1024/1280).
// ---------------------------------------------------------------------------
const WIDTHS = [360, 390, 768, 1024, 1280];

async function fitsNoOverflow(page, label) {
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    const probe = await page.evaluate(() => {
      const vw = document.documentElement.clientWidth;
      const overflowers = [];
      document.querySelectorAll("*").forEach((el) => {
        const r = el.getBoundingClientRect();
        let containedByLocalScroller = false;
        for (let parent = el.parentElement; parent; parent = parent.parentElement) {
          const css = getComputedStyle(parent);
          const pr = parent.getBoundingClientRect();
          if ((css.overflowX === "auto" || css.overflowX === "scroll")
              && pr.left >= -1 && pr.right <= vw + 1) {
            containedByLocalScroller = true;
            break;
          }
        }
        if (r.width > 0 && !containedByLocalScroller && (r.right > vw + 1 || r.left < -1)) {
          overflowers.push({
            tag: el.tagName.toLowerCase(), id: el.id || "",
            cls: String(el.className || "").slice(0, 60),
            text: String(el.textContent || "").trim().slice(0, 60),
            right: Math.round(r.right),
          });
        }
      });
      return {
        page: document.documentElement.scrollWidth - vw,
        overflowers: overflowers.slice(0, 12),
      };
    });
    expect(probe.page, `${label}: overflow at ${width}px: ${JSON.stringify(probe)}`).toBeLessThanOrEqual(1);
    expect(probe.overflowers, `${label}: overflowing elements at ${width}px: ${JSON.stringify(probe)}`).toEqual([]);
  }
}

// Optional evidence: with TURNOS_E2E_SCREENSHOT_DIR set, the five-width matrix is also saved there.
async function captureResponsiveMatrix(testInfo, page, slug) {
  const dir = process.env.TURNOS_E2E_SCREENSHOT_DIR;
  if (!dir) return;
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    await saveShot(testInfo, page, `${dir}/${slug}-${width}.png`, { fullPage: true });
  }
}

// The doctor's own agenda. Medico-ONLY by design (admin gets the same
// solo_medico 403 the API has always answered) -- the happy path below runs
// as the fixture's doctor operator, and the gate test pins the owner admin
// out of it. No URL state exists on this screen: reload history is the
// screen itself, and the ONE safe user action is the shared column gear
// (SanaTable.columnas), which re-renders the day tables from its onChange.
test("/admin/mis-turnos cold-loads the doctor's agenda, re-renders through the column gear, reloads, and fits 360-1280px", async ({ page, app }, testInfo) => {
  const juan = app.fixture.doctor_patients;
  const dayRows = page.locator("#sec-mis-turnos .mt-day table tbody tr");
  const agenda = page.locator("#sec-mis-turnos");
  const servicioCells = page.locator("#sec-mis-turnos td[data-col='servicio']");
  await signIn(page, juan.medico_user, juan.medico_pass);
  await page.goto("/admin/mis-turnos");

  // Cold load: the fixture's approved appointment (2 days out) lands in
  // the 14-day window, with the intake analysis attached to its row.
  await expect(dayRows).toBeVisible({ timeout: 10_000 });
  const url = new URL(page.url());
  expect(url.pathname + url.search + url.hash, "canonical bare URL").toBe("/admin/mis-turnos");
  await expect(page.locator('#adm-nav a[data-nav="mis-turnos"]')).toHaveAttribute("href", "/admin/mis-turnos");
  await expect(page.locator('#adm-nav a[data-nav="mis-turnos"]')).toHaveClass(/\bon\b/);
  await expect(page.locator("h1#adm-h1")).toHaveText("Mis turnos");
  await expect(agenda, "the fixture patient's appointment row").toContainText(/Roster/, { useInnerText: true });
  // No counter above the days (the product owner, 2026-10-14); the gear and «Descargar»
  // ride on the title's line (one screen header, 2026-11-08).
  await expect(agenda).not.toContainText(/esta semana/, { useInnerText: true });
  await expect(page.locator(".adm-h1row #mt-tools [data-tblcol-gear]")).toBeVisible();
  await expect(page.locator("#sec-mis-turnos .queue-triage--verde").first(),
    "the intake triage chip rides on the doctor's own agenda").toBeAttached();
  await expect(agenda, "the intake summary cell").toContainText(/Synthetic roster consultation\./, { useInnerText: true });

  // Column gear: "Servicio" ships OFF (a doctor knows their own service).
  // Toggling it goes through SanaTable's document-level change handler ->
  // COLPICK._set -> onChange -> render(CACHE): a real re-render, in-page.
  expect(await servicioCells.count(), "Servicio ships behind the gear").toBe(0);
  await page.locator("[data-tblcol-gear]").click();
  await expect(page.locator("[data-tblcol-pop]")).toBeVisible({ timeout: 5_000 });
  await page.locator("[data-tblcol='servicio']").check();
  await expect(servicioCells).toHaveCount(1, { timeout: 5_000 });
  // The onChange re-render rebuilds the popup hidden; reopen the gear.
  await page.locator("[data-tblcol-gear]").click();
  await expect(page.locator("[data-tblcol-pop]")).toBeVisible({ timeout: 5_000 });
  await page.locator("[data-tblcol='servicio']").uncheck();
  await expect(servicioCells).toHaveCount(0, { timeout: 5_000 });

  // Reload: same screen state straight from the API again.
  await page.reload();
  await expect(dayRows).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe("/admin/mis-turnos");
  await expect(agenda).toContainText(/Roster/, { useInnerText: true });

  await fitsNoOverflow(page, "/admin/mis-turnos");
  await captureResponsiveMatrix(testInfo, page, "mis-turnos");
  await expect(dayRows).toBeVisible({ timeout: 5_000 });

  await page.goto("/admin/mis-turnos");
  await expect(dayRows).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe("/admin/mis-turnos");
});

// Review 03/11: an admin has no agenda of his own -- the SCREEN sends him to his
// role's start page (303) instead of printing a JSON error; the data API still
// answers him 403 solo_medico, so the policy is no more permissive.
test("/admin/mis-turnos stays medico-only: the owner admin lands on his start page, the data API 403s, an anonymous hit gets 401", async ({ adminPage: page, newContext }) => {
  const res = await page.request.get("/admin/mis-turnos", { maxRedirects: 0 });
  expect(res.status()).toBe(303);
  expect(res.headers()["location"]).not.toBe("/admin/mis-turnos");
  const api = await page.request.get("/api/admin/my-appointments");
  expect(api.status()).toBe(403);
  expect(await api.text()).toMatch(/solo_medico/);
  const anon = await newContext();
  await assertScreenRedirectsToLogin(anon.request, "/admin/mis-turnos");
});

// The patient registry. State is the controller's own sub-hash
// (#pacientes/nuevo, #pacientes/<dni>) -- it wins even on the direct URL,
// which is what gives this screen REAL Back/Forward and reload semantics.
// The one key action is creating a patient through the real form (empty
// password -> server-generated temporal shown once), proven against the
// usuarios row through db_accounts.get_user (fixture.py query-user).
test("/admin/pacientes creates a patient through the real form and owns ?new/?patient state across click, reload and Back/Forward", async ({ adminPage: page, app }, testInfo) => {
  const DNI = "30111999";
  await page.goto("/admin/pacientes");
  await expect(page.locator('#sec-pacientes [data-empty-state="empty"]')).toBeVisible({ timeout: 10_000 });

  // Cold load: canonical bare URL, nav on, and the honest empty state --
  // the throwaway DB has no patients: no search, filters or table header
  // (progressive disclosure, 2026-10-26), just what will appear here.
  const url = new URL(page.url());
  expect(url.pathname + url.search + url.hash).toBe("/admin/pacientes");
  await expect(page.locator('#adm-nav a[data-nav="pacientes"]')).toHaveAttribute("href", "/admin/pacientes");
  await expect(page.locator('#adm-nav a[data-nav="pacientes"]')).toHaveClass(/\bon\b/);
  await expect(page.locator("#sec-pacientes")).toContainText(/Todavía no hay pacientes/, { useInnerText: true });
  await expect(page.locator("#pac-q")).toHaveCount(0);
  await expect(page.locator("#sec-pacientes th")).toHaveCount(0);
  for (const width of [360, 390]) {
    await page.setViewportSize({ width, height: 900 });
    const emptyState = await page.locator('#sec-pacientes [data-empty-state="empty"]').evaluate((el) => {
      const css = getComputedStyle(el);
      return {
        fits: el.scrollWidth <= el.clientWidth + 1,
        whiteSpace: css.whiteSpace,
        textOverflow: css.textOverflow,
      };
    });
    expect(emptyState.fits, `patient empty state fits at ${width}px`).toBe(true);
    expect(emptyState.whiteSpace, `patient empty state wraps at ${width}px`).toBe("normal");
    expect(emptyState.textOverflow, `patient empty state is not clipped at ${width}px`).not.toBe("ellipsis");
  }
  await captureResponsiveMatrix(testInfo, page, "pacientes");

  // "Nuevo paciente" sets direct query state (same-document). It lives on the
  // title row at every width (the product owner, 2026-11-02); the toolbar has no copy.
  expect(await page.locator("a.cnt-add").count(), "no toolbar copy of the button").toBe(0);
  await expect(page.locator("a.pat-title-new")).toBeVisible();
  await page.locator("a.pat-title-new").click();
  await expect(page).toHaveURL((u) => u.searchParams.get("new") === "1" && u.hash === "", { timeout: 5_000 });
  await expect(page.locator("#pac-form")).toBeVisible({ timeout: 5_000 });

  // Create through the real POST /api/admin/patients: the temporal
  // password block is the in-page success signal; the password stays on
  // screen on purpose even when the shared shell also announces a toast.
  await page.locator("#pat-dni").fill(DNI);
  await page.locator("#pat-name").fill("Rosa");
  await page.locator("#pac-apellido").fill("Prueba");
  await page.locator("#pat-birth").fill("01/02/1990");
  await page.locator("#pat-sex").selectOption("F");
  await page.locator("#pac-contacto").fill("+5492254199001");
  await page.locator("#pac-save").click();
  await expect(page.locator("#pac-out code")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator("#pac-out")).toContainText(/Contraseña temporal/, { useInnerText: true });

  // DB proof: the real usuarios row landed (db_accounts.get_user read path).
  const u = await queryUser(app.dbName, DNI);
  expect(u.found, "the created patient landed in the padrón").toBe(true);
  expect(u.apellido).toBe("Prueba");
  expect(u.nombre).toBe("Rosa");

  // Back/Forward over direct query state: real history entries re-render.
  await page.goBack();
  await expect(page.locator("#pac-q")).toBeVisible({ timeout: 5_000 });
  expect(new URL(page.url()).search, "Back returns to the bare list").toBe("");
  await page.goForward();
  await expect(page.locator("#pac-form")).toBeVisible({ timeout: 5_000 });
  expect(new URL(page.url()).search, "Forward restores the form").toBe("?new=1");

  // Search by DNI (300ms debounce -> full re-render) -> the row's NAME is
  // the link (the product owner, 2026-07-28), and it opens the ficha under the same
  // direct query state.
  await page.goBack();
  await expect(page.locator("#pac-q")).toBeVisible({ timeout: 5_000 });
  await page.locator("#pac-q").fill(DNI);
  const fila = page.locator(`#pat-table a[href="/admin/pacientes?patient=${DNI}"]`);
  await expect(fila).toBeVisible({ timeout: 10_000 });
  await expect(fila).toHaveText(/^Rosa Prueba, \d+$/);
  await fila.click();
  await expect(page).toHaveURL((l) => l.searchParams.get("patient") === DNI && l.hash === "", { timeout: 5_000 });
  await expect(page.locator(".pat-head")).toBeVisible({ timeout: 5_000 });
  await expect(page.locator("#pat-name")).toHaveValue("Rosa");
  await expect(page.locator("#pac-apellido")).toHaveValue("Prueba");

  // Reload keeps the deep-linked ficha.
  await page.reload();
  await expect(page.locator(".pat-head")).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).search).toBe(`?patient=${DNI}`);

  // Responsive: the ficha (the width-critical form view) fits everywhere.
  await fitsNoOverflow(page, "/admin/pacientes ficha");
  await expect(page.locator("#pac-form"), "the data form is behind «Editar datos»").toBeHidden();
  await page.click("#pac-edit");
  await expect(page.locator("#pac-form")).toBeVisible({ timeout: 5_000 });
  await fitsNoOverflow(page, "/admin/pacientes ficha with the form open");
  await captureResponsiveMatrix(testInfo, page, "pacientes-form");

  await page.goto("/admin/pacientes");
  await expect(page.locator("#pac-q")).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe("/admin/pacientes");
});

test("/admin/pacientes stays closed to medicos: the doctor account gets 403, an anonymous hit gets 401", async ({ page, app, newContext }) => {
  const juan = app.fixture.doctor_patients;
  await signIn(page, juan.medico_user, juan.medico_pass);
  expect((await page.request.get("/admin/pacientes")).status()).toBe(403);
  const anon = await newContext();
  await assertScreenRedirectsToLogin(anon.request, "/admin/pacientes");
});

// The doctor's consultation console over one intake interview. State is
// ?interview=<id> (canonicalised in place; popstate-aware). The ownership
// boundary is the security story here: guard_own_interview_or_404 answers a
// doctor asking for SOMEBODY ELSE's (here: nobody's) interview exactly like
// a missing id -- 404, no information leak. The one key action is
// "Confirmar y generar documentos" through the real POST .../confirm,
// proven against the consultations row (db_clinical.consultation_latest_for_interview).
test("/admin/consult keeps ?interview= state, enforces the doctor boundary, confirms through the real POST, reloads, and fits 360-1280px", async ({ page, app, baseURL }, testInfo) => {
  const requestedPaths = [];
  page.on("request", (request) => requestedPaths.push(new URL(request.url()).pathname));
  const juan = app.fixture.doctor_patients;
  const own = juan.consultation_id;
  const doctorless = app.fixture.interview_id;
  const NOTA = "Indicación de control en 7 días (e2e).";
  const consultation = page.locator("#sec-consulta");
  const head = page.locator("#sec-consulta .cst-head h2");
  expect(own && doctorless, "fixture did not return the interviews this test needs").toBeTruthy();
  await signIn(page, juan.medico_user, juan.medico_pass);

  // No id: nothing to show, the doctor lands on Pacientes de hoy.
  await page.goto("/admin/consult");
  await expect(page).toHaveURL(/\/admin\/cola$/, { timeout: 10_000 });

  // The doctor's OWN interview: triage chip + AI brief from the fixture.
  await page.goto(`/admin/consult?interview=${own}`);
  await expect(page.locator("#sec-consulta .queue-triage--verde")).toBeVisible({ timeout: 10_000 });
  await expect(head).toHaveText(/Paula Roster/);
  await expect(consultation).toContainText(/Synthetic roster consultation\./, { useInnerText: true });
  const summaryModuleUrl = await page.evaluate(() => window.SANA_STATIC["platform/admin-patient-summary.mjs"]);
  expect(summaryModuleUrl, "the shell must publish the canonical patient-summary module").toBeTruthy();
  expect(requestedPaths,
    "the real consultation boot must request the fingerprinted patient-summary module",
  ).toContain(new URL(summaryModuleUrl, baseURL).pathname);
  expect(await page.evaluate(() => window.SanaResumenPaciente)).toBeUndefined();
  expect(requestedPaths).not.toContain("/admin-patient-summary.js");

  // Junk query/hash canonicalises to the exact interview URL.
  await page.goto(`/admin/consult?interview=${own}&foo=1#x`);
  await expect(head).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).pathname + new URL(page.url()).search + new URL(page.url()).hash)
    .toBe(`/admin/consult?interview=${own}`);

  // Ownership boundary: the doctorless interview answers like a missing id.
  await page.goto(`/admin/consult?interview=${doctorless}`);
  await expect(consultation).toContainText(/No se encontró esa entrevista\./, { useInnerText: true, timeout: 10_000 });

  // Key action: confirm -> both document links, and the real DB row.
  await page.goto(`/admin/consult?interview=${own}`);
  // Nothing is preselected; confirmation lives inside the explicit review.
  await expect(page.locator("#cst-review")).toBeVisible({ timeout: 10_000 });
  await page.locator("#cst-notes").fill(NOTA);
  await page.locator("#cst-review").click();
  await expect(page.locator("#cst-review-card")).toBeVisible({ timeout: 5_000 });
  await page.locator("#cst-confirmar").click();
  await expect(page.locator("#sec-consulta .cst-done")).toBeVisible({ timeout: 15_000 });
  // Only unsigned local drafts are produced, never issued documents.
  await expect(page.locator("#sec-consulta .cst-done")).toContainText(/Borradores generados/, { useInnerText: true });
  await expect(page.locator('#sec-consulta .cst-done a[href^="/r/"]')).toContainText(/Abrir borrador de receta/, { useInnerText: true });
  await expect(page.locator('#sec-consulta .cst-done a[href^="/o/"]')).toContainText(/Abrir borrador de orden/, { useInnerText: true });
  await expect(page.locator('#sec-consulta .cst-done a[href^="/r/"]'), "receta link").toHaveCount(1);
  await expect(page.locator('#sec-consulta .cst-done a[href^="/o/"]'), "orden link").toHaveCount(1);
  const c = await queryConsultation(app.dbName, own);
  expect(c.found, "the consultation landed in the DB").toBe(true);
  expect(c.notas).toBe(NOTA);
  expect(c.receta, "receta token persisted").toBe(true);
  expect(c.orden, "orden token persisted").toBe(true);

  // Reload: the URL state survives and the confirmed consultation reopens
  // READ-ONLY with what was issued (problems.md no. 1) -- never an empty form
  // that would issue a second prescription. Only an explicit, confirmed
  // «Reemplazar» brings the form back.
  await page.reload();
  await expect(page.locator("#sec-consulta #cst-emitida")).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).search).toBe(`?interview=${own}`);
  expect(await page.locator("#cst-review").count(), "no compose form on a confirmed consultation").toBe(0);
  await expect(page.locator('#cst-emitida a[href^="/r/"]'), "issued receta link").toHaveCount(1);
  await expect(page.locator("#cst-emitida"), "the issued indications are shown").toContainText(NOTA, { useInnerText: true });
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#cst-reemplazar").click();
  await expect(page.locator("#cst-review")).toBeVisible({ timeout: 10_000 });

  // The medication editor itself -- not only its empty state -- must reflow into
  // four labelled fields on a phone.
  await page.setViewportSize({ width: 360, height: 900 });
  await page.locator("[data-add-med]").click();
  const medRow = page.locator('#cst-med-body tr:has([data-med-field="nombre"])');
  await expect(medRow).toBeVisible({ timeout: 5_000 });
  expect(await medRow.locator("td[data-label]").evaluateAll(
    (cells) => cells.map((cell) => cell.getAttribute("data-label")),
  )).toEqual(["Medicamento", "Dosis", "Frecuencia", "Duración"]);

  await fitsNoOverflow(page, "/admin/consult");
  await captureResponsiveMatrix(testInfo, page, "consult");
  await expect(page.locator("#cst-review")).toBeVisible({ timeout: 5_000 });

  // Back/Forward along the REAL entry path: the roster screen's
  // "Iniciar consulta" deep link is how doctors reach this console.
  await page.goto("/admin/my-patients");
  await expect(page.locator("#sec-mis-pacientes .dp-iv-link")).toBeVisible({ timeout: 10_000 });
  await page.locator("#sec-mis-pacientes .dp-iv-link").click();
  await expect(head).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).search).toBe(`?interview=${own}`);
  await page.goBack();
  await expect(page.locator("#sec-mis-pacientes .dp-iv-link")).toBeVisible({ timeout: 10_000 });
  await page.goForward();
  await expect(head).toBeVisible({ timeout: 10_000 });

  await page.goto(`/admin/consult?interview=${own}`);
  await expect(head).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).search).toBe(`?interview=${own}`);
});

// The Pinamar-only roadmap. It 404s under every other theme (the route
// itself does not exist outside pinamar), so this test flips the LIVE theme
// through theme.save() (fixture.py set-theme -- the same catalogo row the
// Appearance screen writes), waits out the server's 3s theme cache, and
// ALWAYS restores humanrounds on cleanup. Runs after the other screens' tests
// in this file so none of them sees the flipped theme. State is ?view=technical
// with REAL pushState history (popstate -> route). No mutating user action
// exists on this read-only screen -- the tab switch IS the stateful action.
test("/admin/plan exists only under the pinamar theme and owns ?view= state across tab click, reload and Back/Forward", async ({ adminPage: page, app, cleanup, newContext }, testInfo) => {
  const juan = app.fixture.doctor_patients;
  const planStatus = async () => (await page.request.get("/admin/plan")).status();
  const planPane = page.locator("#sec-plan");
  // ALWAYS leave the default theme behind, even on a failure below. The server
  // caches theme.current() for ~3s (core/theme.py), so wait for the route to go.
  cleanup(async () => {
    await setTheme(app.dbName, "humanrounds").catch(() => {});
    await expect.poll(planStatus, { message: "default theme restored: /admin/plan 404s", timeout: 15_000 }).toBe(404);
  });
  // Outside pinamar the route does not exist: 404, never a redirect.
  expect(await planStatus()).toBe(404);

  await setTheme(app.dbName, "pinamar");
  await expect.poll(planStatus, { message: "pinamar theme: /admin/plan exists", timeout: 15_000 }).toBe(200);

  await page.goto("/admin/plan");
  await expect(page.locator("#sec-plan .rm-tabs")).toBeVisible({ timeout: 10_000 });
  const url = new URL(page.url());
  expect(url.pathname + url.search + url.hash, "canonical bare URL").toBe("/admin/plan");
  await expect(page.locator('#adm-nav a[data-nav="plan"]')).toHaveAttribute("href", "/admin/plan");
  await expect(page.locator('#adm-nav a[data-nav="plan"]')).toHaveClass(/\bon\b/);
  await expect(planPane, "simple view on cold load").toContainText(/Qué ya funciona/, { useInnerText: true });

  // Tab click = pushState + route (real history, not a location swap).
  await page.locator('.rm-tabs a[href="/admin/plan?view=technical"]').click();
  await expect(page).toHaveURL((l) => l.search === "?view=technical", { timeout: 5_000 });
  await expect(planPane).toContainText(/Ya construido/, { useInnerText: true, timeout: 5_000 });
  await expect(page.locator('.rm-tabs a[aria-selected="true"]')).toHaveAttribute("href", "/admin/plan?view=technical");

  // Reload keeps the technical view.
  await page.reload();
  await expect(page).toHaveURL((l) => l.search === "?view=technical", { timeout: 10_000 });
  await expect(planPane).toContainText(/Ya construido/, { useInnerText: true, timeout: 10_000 });

  // Back -> simple, Forward -> technical (popstate -> route).
  await page.goBack();
  await expect(page).toHaveURL((l) => l.search === "", { timeout: 5_000 });
  await expect(planPane).toContainText(/Qué ya funciona/, { useInnerText: true, timeout: 5_000 });
  await page.goForward();
  await expect(page).toHaveURL((l) => l.search === "?view=technical", { timeout: 5_000 });

  // Junk query canonicalises to the bare path.
  await page.goto("/admin/plan?foo=1");
  await expect(page.locator("#sec-plan .rm-tabs")).toBeVisible({ timeout: 10_000 });
  expect(new URL(page.url()).search).toBe("");

  // Role gates while the route exists: solo_admin (medico -> 403),
  // anonymous -> 303 to the login (B9 contract of the HTML screens).
  const medico = await (await newContext()).newPage();
  await signIn(medico, juan.medico_user, juan.medico_pass);
  expect((await medico.request.get("/admin/plan")).status()).toBe(403);
  const anon = await newContext();
  await assertScreenRedirectsToLogin(anon.request, "/admin/plan");

  await fitsNoOverflow(page, "/admin/plan");
  await captureResponsiveMatrix(testInfo, page, "plan");
  await expect(page.locator("#sec-plan .rm-tabs")).toBeVisible({ timeout: 5_000 });

  await page.goto("/admin/plan");
  await expect(page.locator("#sec-plan .rm-tabs")).toBeVisible({ timeout: 10_000 });

  // Restore the default theme and prove the route is gone again.
  await setTheme(app.dbName, "humanrounds");
  await expect.poll(planStatus, { message: "theme restored: /admin/plan 404s again", timeout: 15_000 }).toBe(404);
});

test("/admin/knowledge saves one complete explicit draft and keeps title generation preview-only", async ({ adminPage: page, cleanup }) => {
  let saveRequests = 0;
  let savedDoc;
  const titleGate = holdGate(cleanup);
  const saveGate = holdGate(cleanup);
  await page.goto("/admin/knowledge");
  await waitForKnowledgeTree(page);
  await page.route("**/api/admin/content/knowledge/properties", async (route) => {
    titleGate.arrive();
    await titleGate.held;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ properties: { title: "Generated title" } }) });
  });
  await page.route("**/api/admin/content/knowledge/*", async (route) => {
    if (route.request().url().endsWith("/properties")) return route.fallback();
    if (route.request().method() !== "PUT") return route.continue();
    saveRequests += 1;
    savedDoc = JSON.parse(route.request().postData() || "{}");
    saveGate.arrive();
    await saveGate.held;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, item: savedDoc }) });
  });
  await page.locator("#kn-search").fill("guardia");
  const visibleArticle = page.locator("#sec-knowledge a.kn-node--article:visible").first();
  await visibleArticle.click();
  await expect(page.locator("#kn-form")).toBeVisible({ timeout: 8_000 });
  await page.locator("[name=content]").fill("Changed body");
  await page.locator("[name=title]").blur();
  await holdsFor(async () => {
    expect(saveRequests, "blur must not save an explicit-save editor").toBe(0);
  }, { during: 300 });
  await page.locator("[data-kn-title]").click();
  await page.locator("[name=title]").fill("Manual title");
  await titleGate.arrived;
  titleGate.release();
  await expect(page.locator("#kn-generate-state")).toContainText("no se reemplazó", { timeout: 8_000 });
  await expect(page.locator("[name=title]")).toHaveValue("Manual title");
  await page.unroute("**/api/admin/content/knowledge/properties");
  await page.route("**/api/admin/content/knowledge/properties", (route) => route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: "generation failed" }) }));
  await page.locator("[data-kn-title]").click();
  await expect(page.locator("#kn-generate-state")).toContainText("generation failed", { timeout: 8_000 });
  const originalArticle = page.locator("#sec-knowledge a.kn-node--article").first();
  const originalHref = await originalArticle.getAttribute("href");
  const anotherArticle = page.locator("#sec-knowledge a.kn-node--article").nth(1);
  if (await anotherArticle.count()) {
    await anotherArticle.click();
    await expect(page.locator("#kn-form")).toBeVisible({ timeout: 8_000 });
    await page.locator('#sec-knowledge a.kn-node--article[href="' + originalHref + '"]').click();
    await expect(page.locator("#kn-form")).toBeVisible({ timeout: 8_000 });
    await expect(page.locator("[name=title]"), "article navigation must retain an unsaved draft").toHaveValue("Manual title");
    await expect(page.locator("[name=content]")).toHaveValue("Changed body");
  }
  await page.locator(".kn-optional > summary").click();
  await page.locator("[name=tags]").fill("one, two");
  await page.locator("[name=source_url]").fill("https://example.gov.ar/source");
  const beforeUnloadProtected = await page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(beforeUnloadProtected, "a dirty draft must protect reload/leave").toBe(true);
  await page.locator(".kn-save").click();
  await expect(page.locator(".kn-save")).toBeDisabled({ timeout: 8_000 });
  await saveGate.arrived;
  expect(saveRequests, "one click starts one save request").toBe(1);
  // The held PUT carries one complete snapshot of the explicit draft.
  expect(savedDoc.title).toBe("Manual title");
  expect(savedDoc.content).toBe("Changed body");
  expect(savedDoc.tags).toEqual(["one", "two"]);
  expect(savedDoc.source.url).toBe("https://example.gov.ar/source");
  await page.locator("[name=content]").fill("Changed after save");
  saveGate.release();
  await expect(page.locator(".kn-save")).toBeEnabled({ timeout: 8_000 });
  await expect(page.locator("[name=content]"), "the later body edit remains after the earlier save completes").toHaveValue("Changed after save");
  expect(await page.locator(".kn-save").isDisabled(), "the completed request restores its own button").toBe(false);
  const stillProtected = await page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(stillProtected, "an edit made while saving remains a protected draft").toBe(true);
  expect(saveRequests, "one explicit Save writes one complete snapshot only").toBe(1);
});

test("/admin/knowledge keeps a changed new-article draft after its first save", async ({ adminPage: page, cleanup }) => {
  let posts = 0;
  let puts = 0;
  let putDoc;
  const postGate = holdGate(cleanup);
  await page.goto("/admin/knowledge");
  await waitForKnowledgeTree(page);
  await page.route("**/api/admin/content/knowledge", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    posts += 1;
    const doc = JSON.parse(route.request().postData() || "{}");
    postGate.arrive();
    await postGate.held;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, item: { ...doc, id: "new-race-article" } }) });
  });
  await page.route("**/api/admin/content/knowledge/new-race-article", async (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    puts += 1;
    putDoc = JSON.parse(route.request().postData() || "{}");
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, item: putDoc }) });
  });
  await page.locator('[data-kn-new="articulo"]').click();
  await expect(page.locator("#kn-form")).toBeVisible({ timeout: 8_000 });
  await page.locator("[name=title]").fill("New race article");
  await page.locator("[name=content]").fill("First create body");
  await page.locator(".kn-save").click();
  await expect(page.locator(".kn-save")).toBeDisabled({ timeout: 8_000 });
  await postGate.arrived;
  await page.locator("[name=content]").fill("Changed after first create");
  postGate.release();
  await expect(page.locator("[name=id]")).toHaveValue("new-race-article", { timeout: 8_000 });
  expect(posts).toBe(1);
  await page.locator(".kn-save").click();
  await expect(page.locator("#kn-save-state")).toContainText("Guardado", { timeout: 8_000 });
  expect(posts, "a changed first save must not duplicate the new article").toBe(1);
  expect(puts, "the next explicit save updates the assigned article id").toBe(1);
  expect(putDoc.id).toBe("new-race-article");
  expect(putDoc.content).toBe("Changed after first create");
});

test("/admin/knowledge protects a dirty draft before disabling it", async ({ adminPage: page, cleanup }) => {
  let deletes = 0;
  const deleteGate = holdGate(cleanup);
  await page.goto("/admin/knowledge");
  await waitForKnowledgeTree(page);
  await page.route("**/api/admin/content/knowledge/*", async (route) => {
    if (route.request().method() !== "DELETE") return route.continue();
    deletes += 1;
    deleteGate.arrive();
    await deleteGate.held;
    await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
  await page.locator("#kn-search").fill("guardia");
  await page.locator("#sec-knowledge a.kn-node--article:visible").first().click();
  await expect(page.locator("#kn-form")).toBeVisible({ timeout: 8_000 });
  await page.locator("[name=title]").fill("Draft that must survive cancellation");
  await page.locator(".kn-more > summary").click();
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.locator("[data-kn-delete]").click();
  await holdsFor(async () => {
    expect(deletes, "cancelling the discard confirmation must not disable").toBe(0);
  }, { during: 100 });
  expect(await page.locator("[name=title]").inputValue()).toBe("Draft that must survive cancellation");
  expect(await page.locator("[name=title]").isDisabled(), "cancel keeps the editor editable").toBe(false);
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("[data-kn-delete]").click();
  await expect(page.locator("[name=title]")).toBeDisabled({ timeout: 8_000 });
  await deleteGate.arrived;
  expect(deletes, "confirmation issues one disable request").toBe(1);
  await expect(page.locator("[name=content]"), "the form locks while discard-and-disable is in flight").toBeDisabled();
  deleteGate.release();
  await expect(page.locator("#kn-form")).toHaveCount(0, { timeout: 8_000 });
});
