// Port of tests/e2e/auth-test-login-ownership.e2e.mjs (node:test, stays untouched) to
// @playwright/test. Covers the local-only identity simulator at /auth/test: the real
// router, template and fingerprinted controller, with synthetic identity responses
// (no production identity or live session is mutated). Every case is about an ORDERING:
//   - the simulator stays closed (404) without the explicit deployed opt-in;
//   - an older save that resolves (200 or 422) while the user already typed a second draft
//     must not clobber that draft, and the 44px touch-target gate really fails on old sizing;
//   - a stale initial list read must not overwrite a person created in the meantime;
//   - display formatting is the canonical one (and disappears when one formatter is bypassed);
//   - a module (auth-test / i18n / format / dates) that arrives AFTER the real 8 s deadline
//     still brings the simulator up: the deadline shows the shared late line, never the
//     failure box (2026-11-16, static/platform/screen-boot.mjs);
//   - pagehide/pageshow remounts keep exactly one pending save and its draft;
//   - mixed click/keyboard activation of «login» is serialized and a retry works.
// The races are kept deterministic exactly as in the original: a held route gated by a
// promise that the test releases at the chosen moment (never a sleep).
//
// Differences from the node:test original:
//   - The first step of the original's before-hook (second app WITHOUT the stub opt-in,
//     GET /auth/test must be 404) is now its own test, the first one, so this file has
//     one test more than the original (45 vs 44); all 44 others keep their titles.
//     It starts its app with startApp() and tears it down in try/finally.
//   - The app comes from the worker-scoped `app` fixture (test.use({ appOptions })) and the
//     page from the runner, so the per-width viewport is set with page.setViewportSize()
//     before the first goto (the original passed it to newPage()); there is no
//     requireOk()/skip path - a missing prerequisite fails the run.
//   - Evidence screenshots go to the test report (testInfo.attach), not to
//     ~/.claude/artifacts. The original's catch-block failure screenshot is replaced by the
//     runner's screenshot-on-failure and trace-on-retry; try/finally keeps only release().
//   - Waits are web-first (expect(locator)..., expect.poll, waitForEvent('load') instead of
//     the deprecated waitForNavigation). Assertions that guard a race stay point-in-time
//     snapshots (expect(await ...)), never retrying ones, so they cannot pass late.
//   - The 8 s bootstrap-timeout waits pass an explicit 20 s expect timeout (config default is 10 s).
import { test, expect } from "./lib/fixtures.mjs";

test.use({
  appOptions: {
    dbName: "sana_turnos_test_e2e_auth_ownership",
    env: {
      // This isolated throwaway process only. Never set this on a live site.
      TURNOS_AUTH_MODE: "renaper", TURNOS_RENAPER_PROVIDER: "stub", TURNOS_RENAPER_STUB_DEPLOYED: "1",
      // Since 2026-10-23 the deployed opt-in also needs a demo installation.
      TURNOS_DEMO_MODE: "1", TURNOS_DEMO_REFRESH_OFF: "1",
      LANGUAGES: "es,en", LANG_DEFAULT: "es", LANG_SWITCHER_MODE: "secret",
    },
  },
});

// The bootstrap gives up after 8 s (templates/auth-test.html), longer than the default expect timeout.
const BOOTSTRAP_WAIT = { timeout: 20_000 };

// The real «Crear manualmente» moves focus to DNI in a 0 ms timer. Filling
// before that timer runs lets it steal the next field's text under load
// (seen: DNI "99000001First synthetic draft", name empty, save refused).
// showModal() already focuses DNI, so focus alone proves nothing: wait for a
// 0 ms timer queued after the click, which runs after the dialog's own one.
async function openNewForm(page) {
  await page.locator("#tid-new").click();
  await page.evaluate(() => new Promise((resolve) => setTimeout(resolve, 0)));
  // Deliberately a snapshot, not toBeFocused(): a retrying check would also pass
  // when focus arrives late, which is exactly what this guard must not allow.
  expect(await page.evaluate(() => document.activeElement.id)).toBe("tid-dni");
}

async function attachShot(testInfo, name, page, options) {
  await testInfo.attach(name, { body: await page.screenshot(options), contentType: "image/png" });
}

const noOverflow = (page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);

test("the simulator stays unavailable without the explicit opt-in", async ({ page, launchApp }) => {
  test.setTimeout(120_000);
  // Proxy retirement deliberately makes the old peer-marker path fail closed.
  // Prove the real app remains closed without the explicit simulator opt-in;
  // do not manufacture legacy trusted headers to make a fixture look native.
  const closed = await launchApp({
    dbName: "sana_turnos_test_e2e_auth_closed",
    env: { TURNOS_AUTH_MODE: "renaper", TURNOS_RENAPER_PROVIDER: "stub", TURNOS_RENAPER_STUB_DEPLOYED: "0" },
  });
  const result = await page.goto(closed.baseUrl + "/auth/test");
  expect(result.status(), "the simulator must stay unavailable without explicit opt-in").toBe(404);
});

for (const width of [360, 390, 768, 1024, 1280]) {
  for (const succeeds of [true, false]) {
    test(`actual ${width}px draft survives an older save ${succeeds ? "success" : "failure"}`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      let release;
      const gate = new Promise((resolve) => { release = resolve; });
      const writes = [], errors = [];
      page.on("pageerror", (error) => errors.push(String(error)));
      await page.route("**/api/account/test-identities", async (route) => {
        if (route.request().method() !== "POST") return route.fulfill({ json: { identidades: [] } });
        writes.push(route.request().postDataJSON());
        await gate;
        return route.fulfill({ status: succeeds ? 200 : 422, json: succeeds ? {} : { error: "Old draft error" } });
      });
      async function draft(dni, name) {
        await openNewForm(page);
        await page.locator("#tid-dni").fill(dni);
        await page.locator("#tid-name").fill(name);
        await page.locator("#tid-surname").fill("Fixture");
        await page.locator("#tid-birth").fill("1990-01-01");
      }
      try {
        await page.goto("/auth/test");
        await draft("99000001", "First synthetic draft");
        await page.locator("#tid-save").click();
        await expect(page.locator("#tid-save")).toBeDisabled();
        // The real dialog close/new controls establish actual reachability.
        await page.locator("#tid-close").click();
        await draft("99000002", "Second synthetic draft");
        await expect(page.locator("#tid-save")).toBeDisabled();
        await page.locator("#tid-name").press("Enter");
        release();
        await expect(page.locator("#tid-save")).toBeEnabled();
        expect(writes).toHaveLength(1);
        expect(writes[0].identidad.nombre).toBe("First synthetic draft");
        expect(await page.locator("#tid-dialog").evaluate((node) => node.open)).toBe(true);
        await expect(page.locator("#tid-name")).toHaveValue("Second synthetic draft");
        expect(await page.locator("#tid-form-msg").textContent()).toBe("");
        expect(await page.locator("#tid-dialog").evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
        const compactControls = () => page.locator("#tid-dialog input:not([type=hidden]), #tid-dialog select, #tid-close")
          .evaluateAll((nodes) => nodes.filter((node) => { const r = node.getBoundingClientRect(); return r.height < 44 || r.width < 44; }).map((node) => node.id));
        expect(await compactControls()).toEqual([]);
        const oldSizing = await page.addStyleTag({ content: ".tid-in,.tid-link{min-height:0!important;min-width:0!important;}" });
        expect((await compactControls()).length, "old sizing must fail the actual geometry gate").toBeGreaterThan(0);
        await oldSizing.evaluate((node) => node.remove());
        expect(await compactControls()).toEqual([]);
        await attachShot(testInfo, `draft-${width}-${succeeds}.png`, page, { fullPage: true });
        expect(errors).toEqual([]);
      } finally { release(); }
    });
  }

  test(`actual ${width}px list ignores a stale initial read after random creation`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    let release, reads = 0;
    const gate = new Promise((resolve) => { release = resolve; });
    const person = { dni: "99000001", nombre: "New synthetic", apellido: "Fixture", nacimiento: "1990-01-01", sexo: "F" };
    await page.route("**/api/account/test-identities", async (route) => {
      if (++reads === 1) { await gate; return route.fulfill({ json: { identidades: [] } }); }
      return route.fulfill({ json: { identidades: [person] } });
    });
    await page.route("**/api/account/test-identities/random", (route) => route.fulfill({ json: { identidad: person } }));
    try {
      await page.goto("/auth/test", { waitUntil: "domcontentloaded" });
      await page.locator("#tid-random").click();
      await expect(page.locator(".tid-person")).toBeVisible();
      const oldRead = page.waitForResponse((response) => response.url().endsWith("/api/account/test-identities"));
      release(); await oldRead;
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      expect(await page.locator(".tid-person").count()).toBe(1);
      expect(await page.locator(".tid-person").textContent()).toMatch(/New synthetic/);
      await page.evaluate(() => scrollTo(0, 0));
      await attachShot(testInfo, `full-list-${width}.png`, page, { fullPage: true });
      expect(await noOverflow(page)).toBe(true);
    } finally { release(); }
  });
}

for (const width of [360, 390, 768, 1024, 1280]) {
  for (const lang of ["es", "en"]) {
    test(`actual ${lang} simulator uses canonical display formatting without overflow at ${width}px`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      const person = { dni: "99000001", nombre: "ANA", apellido: "DE LA CRUZ",
        nacimiento: "1990-01-01", contacto: "011 15 3547-7302", sexo: "F" };
      const pattern = /\/static\/platform\/auth-test\.[a-f0-9]{8}\.mjs$/;
      await page.route("**/api/account/test-identities", (route) => route.fulfill({ json: { identidades: [person] } }));
      await page.goto("/auth/test?lang=" + lang);
      const row = page.locator(".tid-person").first();
      await expect(row).toBeVisible();
      expect(await row.locator("td").nth(0).textContent()).toBe("99.000.001");
      expect(await row.locator("td").nth(1).textContent()).toBe("Ana");
      expect(await row.locator("td").nth(2).textContent()).toBe("De la Cruz");
      expect(await row.locator("td").nth(7).textContent()).toBe("+54 9 11 3547-7302");
      const birth = await row.locator("td").nth(5).textContent();
      expect(birth).toMatch(lang === "en" ? /January/ : /enero/);
      expect(birth).not.toMatch(/1990-01-01|01\/01\/1990/);
      expect(await noOverflow(page)).toBe(true);
      await row.locator(".tid-edit").click();
      await expect(page.locator("#tid-phone")).toHaveValue("+54 9 11 3547-7302");
      await expect(page.locator("#tid-dni")).toHaveValue("99.000.001");
      await page.locator("#tid-close").click();
      await page.evaluate(() => scrollTo(0, 0));
      await attachShot(testInfo, `formatted-${lang}-${width}.png`, page, { fullPage: true });
      // Actual browser negative control: bypass one formatter at the real
      // renderer call site. Canonical output must disappear, proving this
      // assertion is not merely checking that a shared import is present.
      await page.route(pattern, async (route) => {
        const response = await route.fetch();
        const source = await response.text();
        expect(source.includes("Format.telTexto(person.contacto)")).toBe(true);
        await route.fulfill({ response, body: source.replace("Format.telTexto(person.contacto)", "person.contacto") });
      });
      await page.reload(); await expect(row).toBeVisible();
      expect(await row.locator("td").nth(7).textContent()).toBe(person.contacto);
      expect(await row.locator("td").nth(7).textContent()).not.toBe("+54 9 11 3547-7302");
    });
  }
}

// Since 2026-11-16 the eight-second deadline only says the load is late (the
// shared loader, static/platform/screen-boot.mjs): the shared late line shows
// in #tid-load-slow while the failure box stays hidden, and the held module
// still brings the simulator up and takes the line down.
for (const dependency of ["auth-test", "i18n", "format", "dates"]) {
  test(`a ${dependency} arriving after the real deadline still brings the simulator up`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 900 });
    let release, reads = 0, firstRead;
    const gate = new Promise((resolve) => { release = resolve; });
    const listed = new Promise((resolve) => { firstRead = resolve; });
    const pattern = new RegExp("/static/platform/" + dependency + "\\.[a-f0-9]{8}\\.mjs$");
    const hold = async (route) => { await gate; await route.continue(); };
    await page.route(pattern, hold);
    await page.route("**/api/account/test-identities", (route) => {
      reads++;
      firstRead();
      return route.fulfill({ json: { identidades: [] } });
    });
    try {
      await page.goto("/auth/test", { waitUntil: "domcontentloaded" });
      const late = page.locator("#tid-load-slow");
      await expect(late).toBeVisible(BOOTSTRAP_WAIT);
      await expect(late.getByRole("status")).toHaveText("La carga tarda más de lo normal.");
      await expect(late.locator("[data-retry]")).toHaveText("Recargar");
      expect(await page.locator("#tid-load-error").isVisible(), "late is not failed").toBe(false);
      expect(reads, "nothing owned the page before the module").toBe(0);
      await attachShot(testInfo, `late-${dependency}.png`, page, { fullPage: true });
      release();
      await listed;
      await expect(page.locator("#tid-new")).toBeEnabled();
      await expect(late).toBeHidden();
      expect(reads).toBe(1);
      expect(await page.locator("#tid-load-error").isVisible()).toBe(false);
    } finally { release(); }
  });
}

for (const width of [360, 1280]) {
  test(`page-transition remount preserves one pending save and its draft at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const writes = [];
    await page.route("**/api/account/test-identities", async (route) => {
      if (route.request().method() !== "POST") return route.fulfill({ json: { identidades: [] } });
      writes.push(route.request().postDataJSON());
      if (writes.length === 1) await gate;
      return route.fulfill({ json: {} });
    });
    try {
      await page.goto("/auth/test");
      await openNewForm(page);
      for (const [selector, value] of Object.entries({ "#tid-dni": "99000001", "#tid-name": "Retained synthetic",
        "#tid-surname": "Fixture", "#tid-birth": "1990-01-01" })) await page.locator(selector).fill(value);
      const sent = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/api/account/test-identities"));
      await page.locator("#tid-save").click(); await sent;
      // This exercises real page listeners and renderer, not browser BFCache
      // eligibility/storage. The separate browser cache policy is not claimed.
      await page.evaluate(() => {
        for (let n = 0; n < 2; n++) {
          dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
          dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
        }
      });
      await expect(page.locator("#tid-save")).toBeDisabled();
      await page.locator("#tid-name").press("Enter");
      release();
      await expect(page.locator("#tid-save")).toBeEnabled();
      expect(writes).toHaveLength(1);
      expect(await page.locator("#tid-dialog").evaluate((node) => node.open)).toBe(true);
      await expect(page.locator("#tid-name")).toHaveValue("Retained synthetic");
      expect(await page.locator("#tid-form-msg").textContent(), "old saving status must retire with its owner").toBe("");
      await attachShot(testInfo, `remounted-${width}.png`, page, { fullPage: true });
      await page.locator("#tid-save").click();
      await expect(page.locator("#tid-dialog")).toHaveJSProperty("open", false);
      expect(writes, "one deliberate new save, no duplicated mount listeners").toHaveLength(2);
      expect(writes[1].identidad.nombre).toBe("Retained synthetic");
    } finally { release(); }
  });
}

for (const lang of ["es", "en"]) {
  for (const dependency of ["auth-test", "i18n", "format", "dates"]) {
    test(`actual ${lang} simulator recovers from rejected ${dependency} via reload`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width: 390, height: 900 });
      const pattern = new RegExp("/static/platform/" + dependency + "\\.[a-f0-9]{8}\\.mjs$");
      const reject = (route) => route.abort("failed");
      await page.route(pattern, reject);
      await page.route("**/api/account/test-identities", (route) => route.fulfill({ json: { identidades: [] } }));
      await page.goto("/auth/test?lang=" + lang);
      await expect(page.locator("html")).toHaveAttribute("lang", lang);
      expect(new URL(page.url()).searchParams.has("lang"), "the real language switch must redirect and persist its cookie").toBe(false);
      await expect(page.locator("#tid-load-error")).toBeVisible(BOOTSTRAP_WAIT);
      await expect(page.locator("#tid-new")).toBeDisabled();
      const expected = lang === "en" ? "Reload page" : "Recargar la página";
      expect(await page.locator("#tid-reload").textContent()).toBe(expected);
      await attachShot(testInfo, `recovery-${lang}-${dependency}.png`, page, { fullPage: true });
      await page.unroute(pattern, reject);
      await Promise.all([page.waitForEvent("load"), page.locator("#tid-reload").click()]);
      await expect(page.locator("#tid-new")).toBeEnabled();
      expect(await page.locator("#tid-load-error").isVisible()).toBe(false);
      await page.locator("#tid-new").click();
      expect(await page.locator("#tid-dialog").evaluate((node) => node.open)).toBe(true);
    });
  }
}

for (const width of [360, 390, 768, 1024, 1280]) {
  test(`actual simulator serializes mixed login activation and permits retry at ${width}px`, async ({ page, app }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    const errors = [], requests = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.addInitScript(() => {
      window.__loginRequests = 0;
      const realFetch = window.fetch;
      window.fetch = function (url, ...args) {
        if (url === "/api/account/test-login") window.__loginRequests++;
        return realFetch.call(this, url, ...args);
      };
    });
    await page.route("**/api/account/test-identities", (route) => route.fulfill({ json: {
      identidades: [1, 2].map((n) => ({ dni: "9900000" + n, nombre: "Synthetic", apellido: "Person " + n,
        nacimiento: "1990-01-01", sexo: "F", contacto: "", email: "", domicilio: "", localidad: "" })),
    } }));
    await page.route("**/api/account/test-login", async (route) => {
      requests.push(route.request().postDataJSON());
      if (requests.length === 1) {
        await held;
        await route.fulfill({ status: 503, json: { error: "Temporary test outage" } });
      } else await route.fulfill({ json: { usuario: { dni: "99000002" } } });
    });
    await page.route("**/account", (route) => route.fulfill({ contentType: "text/html", body: "<h1>Safe local return</h1>" }));
    try {
      const response = await page.goto("/auth/test?provider=renaper&return_to=https%3A%2F%2Fattacker.invalid%2F", { waitUntil: "load" });
      expect(response.status(), "the real local simulator route must be available").toBe(200);
      const rows = page.locator(".tid-person");
      await expect(rows.nth(1)).toBeVisible();
      await rows.first().locator(".tid-login").click();
      await expect.poll(() => page.evaluate(() => window.__loginRequests)).toBe(1);
      await rows.first().locator(".tid-person-name").first().click();
      await rows.first().focus();
      await page.keyboard.press("Enter");
      await page.keyboard.press("Space");
      await expect(rows.nth(1).locator(".tid-login")).toBeDisabled();
      await rows.nth(1).focus();
      await page.keyboard.press("Enter");
      expect(await page.evaluate(() => window.__loginRequests)).toBe(1);
      expect(await noOverflow(page)).toBe(true);
      await attachShot(testInfo, `pending-${width}.png`, page);
      release();
      await expect.poll(() => page.locator("#tid-msg").textContent()).toBe("Temporary test outage");
      expect(await rows.first().locator(".tid-login").isEnabled()).toBe(true);
      await rows.nth(1).locator(".tid-login").click();
      await page.waitForURL(app.baseUrl + "/account");
      expect(requests.map((r) => r.dni)).toEqual(["99000001", "99000002"]);
      expect(requests.every((r) => r.provider === "renaper")).toBe(true);
      expect(errors).toEqual([]);
    } finally {
      release();
    }
  });
}
