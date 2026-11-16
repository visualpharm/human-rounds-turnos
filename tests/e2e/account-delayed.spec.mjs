// Delayed native Account module on /account, /account/reset, and the
// unauthenticated wizard auth step. Existing booking e2e is pre-auth and
// does not prove Account load, failed-import recovery, or slot preservation.
//
// Port of tests/e2e/account-delayed.e2e.mjs (8 tests, same titles, same order), plus two
// tests with no original: the session read and the Account module held past the page's
// eight-second deadline (TASK-42, 2026-11-15).
//
// Differences from the node:test original:
// - The app is the worker-scoped `app` fixture (same dbName, `account: true`). Pages and
//   contexts come from the runner (`page`, `newContext`).
// - Held module requests (Account, and the i18n module in the last test) use `holdGate`
//   (released on cleanup too) behind a shared `holdModule`, replacing the file-local
//   `deferred()`. The «module was not requested» guards (hand-made 8 s timers raced against
//   the route) are `expect.poll` on the URL the route saw, same messages and 8 s. The
//   `assert.match(pathname, MODULE_PATH)` that ran INSIDE the route handler runs in the
//   test body right after that poll.
// - The sleeps that were «nothing may happen» windows are `holdsFor` invariants over the same
//   window: 350 ms after a late Account module is released (no stale auth step, no revived
//   dependent form) and 100 ms after an early dependent submit (no POST). The 3.2 s sleep in
//   `enableEnglish` (the configuration reader caches for up to 3 s) is a poll of the
//   server-rendered `<html lang>` of an anonymous request until it is the saved language.
// - `context.newPage({ viewport })` in the original (labels profile page, the two dependent
//   tests) passed a viewport that BrowserContext.newPage ignores, so those pages ran at the
//   default 1280x720. The port keeps the default viewport there.
// - The tests run in file order on one app and the last one saves LANGUAGES/LANG_DEFAULT
//   (English) in that app's settings; nothing after it needs Spanish, as in the original.
// - Waits and assertions are web-first (`toBeVisible`, `toBeHidden`, `toHaveCount`,
//   `toHaveAttribute`, `toHaveText`) where the original waited and compared. Snapshots that
//   ARE the claim stay `page.evaluate` reads (what is painted while a module is held, the
//   recovery copy and colors, the focus after a label click).
// - Locators: ids/classes/data-* stay CSS (#sc-*, #wz-*, #dep-*, #cuenta-host,
//   .account-load-error): they are the form's markup contract; the labels test keeps
//   `getByLabel`/`getByRole`, which is its subject.
import {
  test, expect, ADMIN_USER, ADMIN_PASS, patientCookie, holdGate, holdsFor,
} from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_account_delayed", account: true } });

const MODULE_NAME = "platform/account-form.mjs";
const MODULE_PATH = /\/static\/platform\/account-form\.[a-f0-9]{8}\.mjs$/;
const CLASSIC = /(?:^|\/)account-form\.js$/;
const SERVICE_ID = "medicina-general-riverside-clinic";
const PEDIATRIC_SERVICE_ID = "pediatria-riverside-clinic";

async function holdModule(page, cleanup, pattern) {
  const gate = holdGate(cleanup);
  let moduleURL = "";
  await page.route(pattern, async (route) => {
    moduleURL = route.request().url();
    await gate.held;
    await route.continue();
  });
  return { releaseModule: gate.release, getURL: () => moduleURL };
}

const holdAccount = (page, cleanup) => holdModule(page, cleanup, "**/static/platform/account-form.*.mjs");

// Resolves once the held Account module has been requested (and is the fingerprinted native one).
async function accountRequested(held, message) {
  await expect.poll(held.getURL, { message, timeout: 8000 }).not.toBe("");
  expect(new URL(held.getURL()).pathname).toMatch(MODULE_PATH);
}

async function enableEnglish(page, request, app) {
  await page.goto("/admin/login?next=%2Fadmin%2Fsettings", {
    waitUntil: "load", timeout: 15000,
  });
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 10000 });
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  await page.waitForURL(/\/admin\/(?!login)/, { timeout: 10000, waitUntil: "commit" });
  const saved = await page.evaluate(async () => {
    const response = await fetch("/api/admin/settings", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        grupo: "languages",
        valores: { LANGUAGES: "es,en", LANG_DEFAULT: "en", LANG_SWITCHER_MODE: "secret" },
      }),
    });
    return { ok: response.ok, body: await response.json() };
  });
  expect(saved.ok, `English bootstrap setup failed: ${JSON.stringify(saved.body)}`).toBe(true);
  // The configuration reader intentionally caches for up to three seconds. Wait until the
  // server can render the freshly saved setting, rather than asserting a
  // language that the prior request legitimately cached.
  await expect.poll(async () => {
    const html = await (await request.get(app.baseUrl + "/")).text();
    return /<html[^>]*\slang="([^"]*)"/.exec(html)?.[1];
  }, { message: "the server never started rendering the saved English default", timeout: 10000 }).toBe("en");
}

test("delayed Account on /account: no retired global, form appears after release, identity prefills", async ({ page, cleanup }) => {
  const requested = [];
  const failures = [];
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  page.on("pageerror", (error) => failures.push({ pageerror: String(error) }));
  await page.addInitScript(() => {
    localStorage.setItem("st_identity", JSON.stringify({
      dni: "30111222", nombre: "Ada", apellido: "Lovelace",
    }));
  });
  const held = await holdAccount(page, cleanup);
  const navigation = page.goto("/account?mode=login", {
    waitUntil: "domcontentloaded", timeout: 20000,
  });
  navigation.catch(() => {});
  await accountRequested(held, `${MODULE_NAME} was not requested from the manifest`);
  const whileDelayed = await page.evaluate(() => ({
    dni: !!document.getElementById("sc-dni"),
    legacyGlobal: typeof window.SanaAccount,
    manifestURL: window.SANA_STATIC?.["platform/account-form.mjs"] || "",
    classic: !!document.querySelector('script[src*="account-form.js"]'),
  }));
  expect(whileDelayed.dni, "the Account form must not paint while the module is held").toBe(false);
  expect(whileDelayed.legacyGlobal).toBe("undefined");
  expect(whileDelayed.manifestURL).toMatch(MODULE_PATH);
  expect(whileDelayed.classic).toBe(false);

  held.releaseModule();
  await navigation;
  await expect(page.locator("#sc-dni")).toBeVisible({ timeout: 15000 });
  const after = await page.evaluate(() => ({
    dni: (document.getElementById("sc-dni")?.value || "").replace(/\D/g, ""),
    nombre: document.getElementById("sc-nombre")?.value || "",
    legacyGlobal: typeof window.SanaAccount,
  }));
  expect(after.dni, "st_identity must prefill the delayed form").toBe("30111222");
  expect(after.legacyGlobal).toBe("undefined");
  expect(new URL(held.getURL()).pathname).toMatch(MODULE_PATH);
  expect(requested.some((path) => CLASSIC.test(path))).toBe(false);
  expect(failures).toEqual([]);
});

test("a rejected Account import shows recovery copy and recovers on reload", async ({ page }) => {
  let failFirst = true;
  let aborted = 0;
  await page.route("**/static/platform/account-form.*.mjs", async (route) => {
    if (failFirst) {
      failFirst = false;
      aborted += 1;
      await route.abort("failed");
      return;
    }
    await route.continue();
  });

  await page.goto("/account?mode=register", {
    waitUntil: "domcontentloaded", timeout: 20000,
  });
  await expect(page.locator("#cuenta-host")).toHaveText(/No se pudo cargar el formulario|Recargá la página/i, { timeout: 10000 });
  expect(await page.evaluate(() => typeof window.SanaAccount)).toBe("undefined");
  expect(await page.evaluate(() => !!document.getElementById("sc-dni"))).toBe(false);

  expect(aborted, "exactly one module request is refused, the reload's is the recovery").toBe(1);

  // Wait for the module request the RELOAD makes (registered before the reload, so a fast
  // answer cannot be missed): under four parallel workers the form used to be asserted while
  // that request was still in flight.
  const recovered = page.waitForResponse((response) =>
    MODULE_PATH.test(new URL(response.url()).pathname), { timeout: 20000 });
  await page.reload({ waitUntil: "domcontentloaded", timeout: 20000 });
  expect((await recovered).ok(), "the reload must fetch the Account module successfully").toBe(true);
  await expect(page.locator("#sc-dni")).toBeVisible({ timeout: 15000 });
  expect(await page.evaluate(() => typeof window.SanaAccount)).toBe("undefined");
});

// Under load the reload above took longer than the page's eight-second deadline and stayed
// on the recovery copy for good (TASK-42): a late healthy answer must still end in the form.
for (const [what, pattern] of [
  ["a session read", "**/api/account/me"],
  ["an Account module", "**/static/platform/account-form.*.mjs"],
]) {
  test(`${what} held past the eight-second deadline still ends in the form`, async ({ page, cleanup }) => {
    const held = await holdModule(page, cleanup, pattern);
    await page.goto("/account?mode=register", { waitUntil: "domcontentloaded", timeout: 20000 });
    await expect(page.locator("#cuenta-host .account-load-error")).toBeVisible({ timeout: 12000 });
    expect(held.getURL(), `${what} was requested and is still held`).not.toBe("");
    held.releaseModule();
    await expect(page.locator("#sc-dni")).toBeVisible({ timeout: 15000 });
    await expect(page.locator("#cuenta-host .account-load-error")).toHaveCount(0);
  });
}

test("native Account labels name and focus every direct control across register, login, reset, and profile", async ({ page, app, newContext }) => {
  async function assertNativeLabel(page, name, id, { dismissPicker = false } = {}) {
    const control = page.getByLabel(name, { exact: true });
    await expect(control).toBeVisible({ timeout: 10000 });
    await expect(control, `${name} must name #${id}`).toHaveAttribute("id", id);
    await page.locator(`label[for="${id}"]`).click();
    expect(await page.evaluate(() => document.activeElement?.id || ""),
      `clicking the visible ${name} label must focus #${id}`).toBe(id);
    if (dismissPicker) {
      const picker = page.locator(`#${id}-list`);
      await expect(picker).toBeVisible({ timeout: 3000 });
      await page.keyboard.press("Escape");
      await expect(picker).toBeHidden({ timeout: 3000 });
      expect(await page.evaluate(() => document.activeElement?.id || ""),
        `dismissing the ${name} picker must not lose the input focus`).toBe(id);
    }
  }

  await page.setViewportSize({ width: 360, height: 844 });
  await page.goto("/account?mode=register", { waitUntil: "load", timeout: 20000 });
  for (const [name, id, options] of [
    ["DNI", "sc-dni"], ["Nombre", "sc-nombre"], ["Apellido", "sc-apellido"],
    ["Nacimiento", "sc-nac"], ["Domicilio", "sc-dom-calle"],
    ["Localidad", "sc-dom-loc", { dismissPicker: true }], ["Código postal", "sc-dom-cp"],
    ["Teléfono", "sc-contacto"], ["Email", "sc-email"], ["Contraseña", "sc-pass"],
  ]) await assertNativeLabel(page, name, id, options);

  const sex = page.getByRole("radiogroup", { name: /Sexo/ });
  await expect(sex).toHaveAttribute("id", "sc-sexo-seg");
  expect(await page.locator('label[for="sc-sexo-seg"]').count(),
    "the composite radiogroup must use aria-labelledby, never a label for a non-control").toBe(0);
  const generator = page.locator("#sc-genbtn");
  const box = await stableBox(generator, "password generator");
  expect(box && box.height, "password generator must be at least a 44px phone target").toBeGreaterThanOrEqual(44);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
    "the 360px registration form must not overflow horizontally").toBe(false);

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
    "the 390px registration form must not overflow horizontally").toBe(false);

  await page.locator("#sc-swap").click();
  // Login accepts a DNI or a staff email (2026-10-13): the field is named for both.
  await assertNativeLabel(page, "DNI o email", "sc-dni");
  await assertNativeLabel(page, "Contraseña", "sc-pass");

  const resetContext = await newContext({ viewport: { width: 1280, height: 900 } });
  const reset = await resetContext.newPage();
  await reset.goto("/account/reset?token=e2e-synthetic-token", { waitUntil: "load", timeout: 20000 });
  await assertNativeLabel(reset, "Contraseña nueva", "sc-pass");
  expect(await reset.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);

  const context = await newContext();
  await context.addCookies([patientCookie(app.baseUrl, app.fixture.account_session)]);
  const profile = await context.newPage();
  await profile.goto("/account#datos", { waitUntil: "load", timeout: 20000 });
  await assertNativeLabel(profile, "Nombre", "sc-nombre");
  await assertNativeLabel(profile, "Apellido", "sc-apellido");
  await assertNativeLabel(profile, "Nacimiento", "sc-nac");
  await assertNativeLabel(profile, "Domicilio", "sc-dom-calle");
  await assertNativeLabel(profile, "Localidad", "sc-dom-loc", { dismissPicker: true });
  await assertNativeLabel(profile, "Código postal", "sc-dom-cp");
  await expect(profile.getByRole("radiogroup", { name: /Sexo/ })).toHaveAttribute("id", "sc-sexo-seg");
  await profile.getByRole("button", { name: "Contacto y avisos", exact: true }).click();
  expect(new URL(profile.url()).hash).toBe("#contacto");
  await assertNativeLabel(profile, "WhatsApp", "sc-contacto");
  await assertNativeLabel(profile, "Email", "sc-email");
});

test("wizard keeps the chosen slot visible while Account is delayed, then paints auth; failed import keeps the slot and the /account link", async ({ page, newContext, cleanup }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const held = await holdAccount(page, cleanup);
  await page.goto(`/service/${SERVICE_ID}/book`, {
    waitUntil: "load", timeout: 20000,
  });
  const slotButtons = page.locator(".wz-seg-opt");
  await expect(slotButtons.first()).toBeVisible({ timeout: 15000 });
  await slotButtons.first().click();

  await accountRequested(held, `${MODULE_NAME} was not requested at mountAuth`);
  await expect(page.locator("#wz-auth-change-time")).toBeVisible({ timeout: 10000 });

  const whileDelayed = await page.evaluate(() => ({
    summary: (document.querySelector(".wz-booking-summary")?.innerText || "").trim(),
    dni: !!document.getElementById("sc-dni"),
    legacyGlobal: typeof window.SanaAccount,
  }));
  expect(whileDelayed.summary).toMatch(/Medicina general/);
  expect(whileDelayed.summary.length, "the chosen slot summary must stay visible while Account is held").toBeGreaterThan(0);
  expect(whileDelayed.dni).toBe(false);
  expect(whileDelayed.legacyGlobal).toBe("undefined");

  held.releaseModule();
  await expect(page.locator("#sc-dni")).toBeVisible({ timeout: 15000 });
  const after = await page.evaluate(() => ({
    summary: (document.querySelector(".wz-booking-summary")?.innerText || "").trim(),
    dni: !!document.getElementById("sc-dni"),
    legacyGlobal: typeof window.SanaAccount,
  }));
  expect(after.summary).toMatch(/Medicina general/);
  expect(after.dni).toBe(true);
  expect(after.legacyGlobal).toBe("undefined");

  const failContext = await newContext({ viewport: { width: 1280, height: 900 } });
  const failPage = await failContext.newPage();
  await failPage.route("**/static/platform/account-form.*.mjs", async (route) => {
    await route.abort("failed");
  });
  await failPage.goto(`/service/${SERVICE_ID}/book`, {
    waitUntil: "load", timeout: 20000,
  });
  await expect(failPage.locator(".wz-seg-opt").first()).toBeVisible({ timeout: 15000 });
  await failPage.locator(".wz-seg-opt").first().click();
  await expect(failPage.locator('#wz-auth a[href="/account"]')).toBeAttached({ timeout: 10000 });
  const failed = await failPage.evaluate(() => ({
    summary: (document.querySelector(".wz-booking-summary")?.innerText || "").trim(),
    href: document.querySelector('#wz-auth a[href="/account"]')?.getAttribute("href") || "",
    dni: !!document.getElementById("sc-dni"),
    legacyGlobal: typeof window.SanaAccount,
  }));
  expect(failed.summary).toMatch(/Medicina general/);
  expect(failed.href).toBe("/account");
  expect(failed.dni).toBe(false);
  expect(failed.legacyGlobal).toBe("undefined");
});

test("changing the time while Account is delayed cannot resurrect a stale auth step", async ({ page, cleanup }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const held = await holdAccount(page, cleanup);
  await page.goto(`/service/${SERVICE_ID}/book`, { waitUntil: "load", timeout: 20000 });
  await expect(page.locator(".wz-seg-opt").first()).toBeVisible({ timeout: 15000 });
  await page.locator(".wz-seg-opt").first().click();
  await accountRequested(held, "Account was not requested at mountAuth");
  await expect(page.locator("#wz-auth-change-time")).toBeVisible({ timeout: 10000 });
  await page.locator("#wz-auth-change-time").click();
  await expect(page.locator("#wz-auth")).toHaveCount(0, { timeout: 10000 });
  held.releaseModule();
  await holdsFor(async () => {
    expect(await page.locator("#wz-auth").count(), "a detached auth host must not be repainted after changing time").toBe(0);
    expect(await page.locator("#sc-dni").count()).toBe(0);
  }, { during: 350 });
  const title = (await page.locator("#svc-title").textContent() || "").trim();
  expect(title, "the wizard must remain on the time-selection route").toMatch(/Elegí/);
});

async function openDependentFormWithHeldAccount(page, cleanup) {
  const held = await holdAccount(page, cleanup);
  await page.goto(`/service/${PEDIATRIC_SERVICE_ID}/book`, { waitUntil: "load", timeout: 20000 });
  await expect(page.locator(".wz-seg-opt").first()).toBeVisible({ timeout: 15000 });
  await page.locator(".wz-seg-opt").first().click();
  await expect(page.locator("#dep-form")).toBeVisible({ timeout: 15000 });
  return held;
}

test("dependent DOB cannot submit before the native Account control is ready, and cancel stays final", async ({ app, newContext, cleanup }) => {
  expect(app.fixture.account_session, "fixture did not return the authenticated patient session").toBeTruthy();
  const context = await newContext();
  await context.addCookies([patientCookie(app.baseUrl, app.fixture.account_session)]);
  const page = await context.newPage();
  let dependentPosts = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/account/dependents") dependentPosts += 1;
  });
  const held = await openDependentFormWithHeldAccount(page, cleanup);
  const loading = await page.evaluate(() => ({
    saveDisabled: document.querySelector(".dep-save")?.disabled || false,
    calendarDisabled: document.getElementById("dep-calbtn")?.disabled || false,
    status: document.getElementById("dep-msg")?.textContent?.trim() || "",
  }));
  expect(loading.saveDisabled).toBe(true);
  expect(loading.calendarDisabled).toBe(true);
  expect(loading.status).toMatch(/Preparando el control de fecha/i);
  await page.evaluate(() => document.getElementById("dep-form")?.dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  ));
  await holdsFor(async () => {
    expect(dependentPosts, "an early submit must never use a private fallback parser or call the API").toBe(0);
  }, { during: 100 });
  await page.locator(".dep-cancel").click();
  await expect(page.locator("#dep-form")).toHaveCount(0, { timeout: 10000 });
  held.releaseModule();
  await holdsFor(async () => {
    expect(await page.locator("#dep-form").count(), "a late native module must not revive a cancelled form").toBe(0);
    expect(dependentPosts).toBe(0);
  }, { during: 350 });
});

test("a rejected native Account dependency leaves dependent DOB visibly unavailable and non-submittable", async ({ app, newContext }) => {
  const context = await newContext();
  await context.addCookies([patientCookie(app.baseUrl, app.fixture.account_session)]);
  const page = await context.newPage();
  let dependentPosts = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/account/dependents") dependentPosts += 1;
  });
  await page.route("**/static/platform/account-form.*.mjs", async (route) => route.abort("failed"));
  await page.goto(`/service/${PEDIATRIC_SERVICE_ID}/book`, { waitUntil: "load", timeout: 20000 });
  await expect(page.locator(".wz-seg-opt").first()).toBeVisible({ timeout: 15000 });
  await page.locator(".wz-seg-opt").first().click();
  await expect(page.locator("#dep-form")).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#dep-msg")).toHaveText(/No pudimos cargar el control de fecha/i, { timeout: 10000 });
  const failure = await page.evaluate(() => ({
    saveDisabled: document.querySelector(".dep-save")?.disabled || false,
    calendarDisabled: document.getElementById("dep-calbtn")?.disabled || false,
    text: document.getElementById("dep-msg")?.textContent?.trim() || "",
    reload: document.querySelector("#dep-msg a")?.getAttribute("href") || "",
    color: getComputedStyle(document.getElementById("dep-msg")).color,
    accent: (() => {
      const probe = document.createElement("span");
      probe.style.color = "var(--accent)";
      document.body.appendChild(probe);
      const color = getComputedStyle(probe).color;
      probe.remove();
      return color;
    })(),
  }));
  expect(failure.saveDisabled).toBe(true);
  expect(failure.calendarDisabled).toBe(true);
  expect(failure.text).toMatch(/Recargá la página/i);
  expect(failure.reload, "the recovery action must be a real link").toBeTruthy();
  expect(failure.color, "non-link error text must use danger, not accent green").not.toBe(failure.accent);
  await page.evaluate(() => document.getElementById("dep-form")?.dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  ));
  await holdsFor(async () => {
    expect(dependentPosts).toBe(0);
  }, { during: 100 });
  const afterEarlySubmit = await page.evaluate(() => ({
    text: document.getElementById("dep-msg")?.textContent?.trim() || "",
    reload: document.querySelector("#dep-msg a")?.getAttribute("href") || "",
  }));
  expect(afterEarlySubmit.text,
    "a rejected dependency must retain the explanatory recovery state after Enter").toMatch(/Recargá la página/i);
  expect(afterEarlySubmit.reload,
    "an early submit after rejection must preserve the real reload link").toBeTruthy();
});

test("English first paint of Account waits for i18n; delayed reset token still reaches renderReset", async ({ page, request, app, newContext, cleanup }) => {
  await enableEnglish(page, request, app);
  await page.goto("/?lang=en", { waitUntil: "load", timeout: 15000 });

  // A module-load failure happens before account-form.mjs can inject its own
  // styles. Prove the two real English pages show translated, accessible
  // recovery copy in the shared danger color -- never link-green text that
  // looks actionable but is not a link.
  for (const path of ["/account?mode=login", "/account/reset?token=e2e-synthetic-token"]) {
    const englishContext = await newContext();
    await englishContext.addCookies([{ name: "lang", value: "en", url: app.baseUrl }]);
    const failedPage = await englishContext.newPage();
    // The account pages resolve language from the patient session cookie.
    // This is the same real cookie the secret language route emits.
    await failedPage.route("**/static/platform/account-form.*.mjs", async (route) => {
      await route.abort("failed");
    });
    await failedPage.goto(path, { waitUntil: "domcontentloaded", timeout: 20000 });
    await expect(failedPage.locator(".account-load-error")).toBeVisible({ timeout: 10000 });
    const recovery = await failedPage.evaluate(() => {
      const alert = document.querySelector(".account-load-error");
      const colorFor = (value) => {
        const probe = document.createElement("span");
        probe.style.color = value;
        document.body.appendChild(probe);
        const color = getComputedStyle(probe).color;
        probe.remove();
        return color;
      };
      return {
        text: alert?.textContent?.trim() || "",
        role: alert?.getAttribute("role") || "",
        color: alert ? getComputedStyle(alert).color : "",
        danger: colorFor("var(--danger)"),
        accent: colorFor("var(--accent)"),
      };
    });
    expect(recovery.text).toBe("The form couldn't load. Reload the page.");
    expect(recovery.role).toBe("alert");
    expect(recovery.color).toBe(recovery.danger);
    expect(recovery.color).not.toBe(recovery.accent);
    await englishContext.close();
  }

  const registerContext = await newContext();
  const registerPage = await registerContext.newPage();
  const i18n = await holdModule(registerPage, cleanup, "**/static/platform/i18n.*.mjs");
  const navigation = registerPage.goto("/account?mode=register", {
    waitUntil: "domcontentloaded", timeout: 20000,
  });
  navigation.catch(() => {});
  await expect.poll(i18n.getURL, { message: "platform/i18n.mjs was not requested", timeout: 8000 }).not.toBe("");
  const held = await registerPage.evaluate(() => ({
    form: !!document.getElementById("sc-form"),
    dni: !!document.getElementById("sc-dni"),
    email: !!document.getElementById("sc-email"),
    legacyGlobal: typeof window.SanaAccount,
  }));
  expect(held.form, "Account TLA must not paint any form while i18n is held").toBe(false);
  expect(held.dni, "Account TLA must wait on i18n before first paint").toBe(false);
  expect(held.email, "Account TLA must not paint an English email form before i18n resolves").toBe(false);
  expect(held.legacyGlobal).toBe("undefined");

  i18n.releaseModule();
  await navigation;
  await expect(registerPage.locator("#sc-email")).toBeVisible({ timeout: 15000 });
  const html = await registerPage.locator("#cuenta-host").innerHTML();
  expect(html).toMatch(/>Create account</);
  expect(html).not.toMatch(/>Crear cuenta</);
  expect(await registerPage.locator("#sc-dni").count(),
    "English email registration must not render a DNI field").toBe(0);
  await expect(registerPage.locator("#sc-email"),
    "English email registration must require its identity field").toHaveAttribute("required", "");

  const resetContext = await newContext();
  const resetPage = await resetContext.newPage();
  const accountHeld = await holdAccount(resetPage, cleanup);
  const resetNavigation = resetPage.goto("/account/reset?token=e2e-synthetic-token", {
    waitUntil: "domcontentloaded", timeout: 20000,
  });
  resetNavigation.catch(() => {});
  await accountRequested(accountHeld, "Account was not requested on /account/reset");
  expect(await resetPage.evaluate(() => typeof window.SanaAccount)).toBe("undefined");
  accountHeld.releaseModule();
  await resetNavigation;
  await expect(resetPage.locator("#sc-pass")).toBeVisible({ timeout: 15000 });
  expect(await resetPage.evaluate(() => typeof window.SanaAccount)).toBe("undefined");
});
