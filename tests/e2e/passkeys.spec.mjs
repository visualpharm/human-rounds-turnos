// Full browser proof for the maintained WebAuthn ceremony: register through
// the account screen, sign out, use that credential at login, and reach a
// protected admin route. The virtual authenticator is Chromium's CDP feature,
// not a mocked navigator.credentials implementation.
//
// Port of tests/e2e/passkeys.e2e.mjs (1 test, same title).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }), with the
//   requests and TOTP seed as before); there is no requireOk()/skip path. The passkey
//   page is the runner's `page` (390x844 through test.use({ viewport })); the second
//   (admin) browser is a separate `newContext()` page, closed by the fixture instead of
//   `t.after(() => adminPage.close())`. The virtual authenticator is still created over
//   CDP (`context.newCDPSession`) and removed in the same finally block.
// - The password+TOTP login and the admin login are the same steps (goto, fill, click,
//   URL wait) with web-first waits (`toBeVisible`, `waitForURL`); the form waits and
//   the «something answered» wait for the key state (`waitForFunction` on a compound
//   condition) are kept as waits for that state, not sleeps.
// - The toast counter wait (`waitForFunction(n => toasts.length > n)`) is `expect.poll`.
//   The passkey-list-after-failed-read check stays «wait for any of the three states,
//   then assert it is the error text», so a wrong «Ninguna registrada» is still caught.
//   Every `request`, `pageerror` and status read is a point-in-time snapshot.
// - Locators: all CSS ids/attributes (`#gate-*`, `#acct-keys-*`, `#usr-keys-*`,
//   `[data-key-del]`) stay: they are the ids the ceremony under test is wired to.
import { test, expect, totpCode, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_passkeys", requests: true, totp: true },
  viewport: { width: 390, height: 844 },
});

async function loginWithPasswordAndTotp(page, baseUrl, fixture) {
  // WebAuthn permits localhost but not an IP-literal RP ID.  The harness
  // intentionally binds the server to 127.0.0.1; Chromium can still reach it
  // under localhost, which is the browser-visible origin this ceremony needs.
  await page.goto(baseUrl + "/admin/login?next=%2Fadmin%2Faccount", { waitUntil: "load", timeout: 15_000 });
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 10_000 });
  await page.fill("#gate-user", fixture.totp_user);
  await page.fill("#gate-pass", fixture.totp_pass);
  await page.click("#gate-btn");
  await expect(page.locator("#gate-2fa")).toBeVisible({ timeout: 10_000 });
  await page.fill("#gate-code", await totpCode(fixture.totp_secret));
  await page.click("#gate-btn");
  await page.waitForURL(/\/admin\/account(?:\?|$)/, { timeout: 15_000, waitUntil: "commit" });
}

test("password plus TOTP registers a passkey, then passkey-only login reaches the protected route", async ({ page, context, app, newContext }) => {
  const requested = [];
  const pageErrors = [];
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  const baseUrl = app.baseUrl.replace("127.0.0.1", "localhost");
  const cdp = await context.newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  const { authenticatorId } = await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: {
      protocol: "ctap2", transport: "internal", hasResidentKey: true,
      hasUserVerification: true, isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  });
  try {
    const { totp_user, totp_pass, totp_secret } = app.fixture;
    expect(totp_user && totp_pass && totp_secret, "TOTP fixture is incomplete").toBeTruthy();
    await loginWithPasswordAndTotp(page, baseUrl, app.fixture);
    await expect(page.locator("#acct-keys-add")).toBeVisible({ timeout: 10_000 });
    await page.locator("#acct-keys-add").click();
    await page.waitForFunction(() => {
      const listed = document.querySelector("#acct-keys-list li:not(.acct-vacio)");
      const status = document.querySelector("#acct-keys-status");
      const add = document.querySelector("#acct-keys-add");
      return !!listed || (!!status?.textContent?.trim() && !add?.disabled);
    }, null, { timeout: 15_000 });
    const registrationStatus = page.locator("#acct-keys-status");
    expect(await registrationStatus.evaluate((el) => el.classList.contains("is-error")),
      await registrationStatus.textContent()).toBe(false);
    await expect(page.locator("#acct-keys-list li:not(.acct-vacio)")).toBeVisible({ timeout: 5_000 });
    expect(await page.locator("#acct-keys-list li:not(.acct-vacio)").count()).toBe(1);
    // The registered key is the stored list, not only the live one (TASK-3 form audit).
    await page.reload();
    await expect(page.locator("#acct-keys-list li:not(.acct-vacio)")).toHaveCount(1);

    // At 390 «Cerrar sesión» is an item of the «Menú» (one screen header, 2026-11-08).
    await page.locator("details.adm5-menu > summary").click();
    await Promise.all([
      page.waitForURL((url) => new URL(url).pathname === "/admin/login", { timeout: 15_000 }),
      page.locator("#adm-logout").click(),
    ]);
    // B9 (2026-09-10): cerrada la sesión, la PANTALLA /admin/account responde
    // 303 al login con ?next=; el 401 JSON quedó para las rutas de API.
    // Sin maxRedirects:0 Playwright sigue el 303 y devuelve el login con 200.
    const signedOut = await page.request.get(baseUrl + "/admin/account", { maxRedirects: 0 });
    expect(signedOut.status()).toBe(303);
    expect(new URLSearchParams((signedOut.headers().location || "").split("?")[1]).get("next")).toBe("/admin/account");

    await expect(page.locator("#gate-passkey")).toBeVisible({ timeout: 10_000 });
    expect(await page.locator("#gate-2fa").isVisible(),
      "passkey login must not expose or require the TOTP field").toBe(false);
    await page.locator("#gate-passkey").click();
    await page.waitForURL(/\/admin\/account(?:\?|$)/, { timeout: 15_000, waitUntil: "commit" });
    const protectedRoute = await page.request.get(baseUrl + "/admin/account");
    expect(protectedRoute.status()).toBe(200);
    expect(await protectedRoute.text()).toMatch(/id="sec-cuenta"/);

    expect(await page.evaluate(() => window.SanaPasskeys)).toBeUndefined();
    const moduleUrl = await page.evaluate(() => window.SANA_STATIC?.["platform/passkeys.mjs"] || "");
    expect(moduleUrl).toMatch(/\/static\/platform\/passkeys\.[a-f0-9]{8}\.mjs$/);
    expect(requested.some((pathname) => /\/static\/platform\/passkeys\.[a-f0-9]{8}\.mjs$/.test(pathname)),
      "login and Account must request the canonical fingerprinted module").toBe(true);
    expect(requested.some((pathname) => pathname.endsWith("/passkeys.js")),
      "the retired classic passkey script must never be requested").toBe(false);
    expect(pageErrors).toEqual([]);

    const adminPage = await (await newContext()).newPage();
    await adminPage.goto(baseUrl + "/admin/login");
    await adminPage.fill("#gate-user", ADMIN_USER);
    await adminPage.fill("#gate-pass", ADMIN_PASS);
    await adminPage.click("#gate-btn");
    await adminPage.waitForURL((url) => url.pathname !== "/admin/login");
    const keysState = adminPage.locator("#usr-keys-estado");
    for (const [status, body] of [[500, '{"passkeys":[]}'], [200, "{}"], [200, "not-json"]]) {
      await adminPage.route("**/api/admin/passkeys?usuario=*", (route) => route.fulfill({ status, body, contentType: "application/json" }), { times: 1 });
      await adminPage.goto(baseUrl + "/admin/users?user=" + encodeURIComponent(totp_user));
      await expect(keysState).toHaveText(/No se pudo consultar|Ninguna registrada|Registradas:/);
      expect(await keysState.innerText(), "failed read never claims no keys exist").toMatch(/No se pudo consultar/);
    }
    await adminPage.goto(baseUrl + "/admin/users?user=" + encodeURIComponent(totp_user));
    await expect(adminPage.locator("#usr-keys-quitar")).toBeVisible();
    await adminPage.evaluate(() => {
      window.__keyToasts = []; const original = window.toast;
      window.toast = (message) => { window.__keyToasts.push(message); if (original) original(message); };
    });
    for (const [status, body] of [[500, '{"ok":true}'], [200, '{"ok":"true"}'], [200, "not-json"]]) {
      const count = await adminPage.evaluate(() => window.__keyToasts.length);
      await adminPage.route("**/api/admin/passkeys/reset", (route) => route.fulfill({ status, body, contentType: "application/json" }), { times: 1 });
      await adminPage.click("#usr-keys-quitar");
      await expect.poll(() => adminPage.evaluate(() => window.__keyToasts.length)).toBeGreaterThan(count);
      expect(await adminPage.evaluate(() => window.__keyToasts.at(-1))).toMatch(/No se pudo quitar/);
      expect(await adminPage.locator("#usr-keys-quitar").isVisible()).toBe(true);
      expect((await (await page.request.get(baseUrl + "/api/admin/passkeys")).json()).passkeys.length).toBe(1);
    }

    for (const width of [360, 390, 768, 1024, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      const overflow = await page.evaluate(() =>
        document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `Account overflows ${width}px viewport by ${overflow}px`).toBeLessThanOrEqual(1);
    }
    const remove = page.locator("[data-key-del]");
    await expect(remove).toBeVisible();
    const deleted = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/admin/passkeys/delete");
    await remove.click();
    expect((await deleted).status()).toBe(200);
    await expect(remove).toHaveCount(0);
    await page.reload();
    // No key: the row is its «Agregar una passkey» button alone.
    await expect(page.locator("#acct-keys-add")).toBeVisible();
    await expect(page.locator("#acct-keys-list li")).toHaveCount(0);
    const storedKeys = await (await page.request.get(baseUrl + "/api/admin/passkeys")).json();
    expect(storedKeys.passkeys.length).toBe(0);
    await page.click("#acct-keys-add");
    await expect(page.locator("#acct-keys-list li:not(.acct-vacio)")).toBeVisible();
    await adminPage.reload();
    await expect(adminPage.locator("#usr-keys-quitar")).toBeVisible();
    const reset = adminPage.waitForResponse((response) => new URL(response.url()).pathname === "/api/admin/passkeys/reset");
    await adminPage.click("#usr-keys-quitar");
    expect((await reset).status()).toBe(200);
    await expect(adminPage.locator("#usr-keys-quitar")).toBeHidden();
    await page.reload();
    await expect(page.locator("#acct-keys-add")).toBeVisible();
    await expect(page.locator("#acct-keys-list li")).toHaveCount(0);
    expect((await (await page.request.get(baseUrl + "/api/admin/passkeys")).json()).passkeys.length).toBe(0);
  } finally {
    await cdp.send("WebAuthn.removeVirtualAuthenticator", { authenticatorId }).catch(() => {});
    await cdp.send("WebAuthn.disable").catch(() => {});
  }
});
