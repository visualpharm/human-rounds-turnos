// tests/e2e/admin.spec.mjs -- Tier-2 pre-move gate: admin LOGIN, against a
// real running server and a real headless browser. The legacy hash shell
// (admin.html, `#gate-wrap`/`#gate-user`/`#app` reached through bare
// `/admin`) was deleted in the native-migration final wave (2026-08-27) --
// `/admin` is now a pure role-aware 307 redirect (admin_panel/
// admin_root_screen.py). Every login proof here drives the direct,
// server-routed gate instead: `/admin/login` (admin_panel/
// admin_login_screen.py, templates/admin/login.html, static/platform/admin-login.mjs) --
// same markup ids (#gate-form/#gate-user/#gate-pass/#gate-err/#gate-2fa/
// #gate-code/#gate-btn), same admin_auth.login() API underneath, just no
// panel chrome (#app doesn't exist on the standalone page).
//
// Request-queue rendering and hash-navigation-between-sections coverage
// moved to tests/e2e/admin-screen.spec.mjs, which already proves
// `/admin/solicitudes` and `/admin/system` render server-side against the
// real screens -- this file owns ONLY the auth surface: wrong password,
// TOTP, and the direct-gate landing/continuation contract.
//
// Auth: logs in as the env-owner break-glass account (TURNOS_ADMIN_USER/
// _PASS set directly on the server subprocess by the harness -- see
// tests/e2e/lib/harness.mjs) with TOTP and Turnstile both off (empty
// secrets; Turnstile is also auto-off on a 127.0.0.1 dev host per
// turnstile.py). No operator-CRUD flow is exercised here -- that's
// covered by tests/test_admin_users.py and tests/admin-login-2fa.test.mjs.
//
// Port of tests/e2e/admin.e2e.mjs (6 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the same
//   `totp: true`); the runner's page replaces `browser.newPage()` and `state.fixture` is
//   `app.fixture`.
// - The file is `serial`: the landing-contract test (3) needs ADMIN_USER's stored
//   `nav.last_screen` preference NOT to have been set yet, and tests 4-6 set it, so the order
//   is part of the scenario (as the comments in the original explain). A failure skips the
//   rest; a retry re-runs the whole file on a fresh database.
// - Waits are web-first with the original timeouts (waitFor -> toBeVisible / toBeAttached,
//   the gate-error wait -> toHaveText(/\S/), the «lands on a direct screen» waitForFunction
//   -> waitForURL with a predicate, the System text wait -> toContainText with innerText).
//   `window.SANA_ADMIN_SCREEN` reads are expect.poll on the evaluated value (10 s).
//   `networkidle` before clearing the cookie in test 6 is kept as the original had it.
// - Locators left as CSS: the gate's ids (#gate-*), `#app`, `#sec-*`, `#adm-h1`, `#acct-*`,
//   `#lg-guardar` - the markup contract of the login screen and the screens it lands on.
import { test, expect, totpCode, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_admin", totp: true } });
test.describe.configure({ mode: "serial" });

// `totp: true` (Tier 3 slice 1, docs/roadmap/tier3-harness-design.md
// Area 1) additionally seeds an operator with 2FA enabled -- used by the
// TOTP login test below; the owner (env) account this file's other tests
// log in as is untouched (no TOTP secret set on it), same as before.

const onDirectScreen = (url) => url.pathname.startsWith("/admin/") && url.pathname !== "/admin/login";

// Tier 3 slice 1 (docs/roadmap/tier3-harness-design.md Area 1's "Login" gap)
// -- these two prove the direct gate correctly REJECTS a wrong password and
// correctly demands + verifies a second factor, both real admin_auth.py
// code paths a regression could silently break without any test noticing.
// Neither logs in as ADMIN_USER successfully (wrong-password never
// succeeds; TOTP uses the separate totp_user fixture account), so neither
// touches ADMIN_USER's stored `nav.last_screen` preference -- see the
// landing-contract tests below for why that ordering matters.

test("wrong password is rejected: stays on the gate with a clear error", async ({ page }) => {
  await page.goto("/admin/login", { waitUntil: "load", timeout: 15000 });
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 8000 });

  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", "definitely-not-the-real-password");
  await page.click("#gate-btn");

  // admin_auth.login()'s "credenciales" branch (admin-login.mjs): #gate-err
  // gets the human message and #gate-pass is cleared -- never a silent
  // no-op, never a pass-through past the gate.
  await expect(page.locator("#gate-err")).toHaveText(/\S/, { timeout: 8000 });
  await expect(page.locator("#gate-err")).toHaveText(/incorrect|no se pudo/i);
  expect(await page.locator("#gate-pass").inputValue()).toBe("");
  // The gate never hides on a rejected attempt; the standalone page has no
  // panel chrome to fall through into (#app doesn't exist here at all).
  expect(await page.locator("#gate-wrap").isVisible()).toBe(true);
  expect(await page.locator("#app").count()).toBe(0);
});

test("a 2FA-enabled operator needs a valid TOTP code to get in", async ({ page, app }) => {
  const { totp_user, totp_pass, totp_secret } = app.fixture;
  expect(totp_user && totp_pass && totp_secret, "fixture did not return a --totp operator").toBeTruthy();

  await page.goto("/admin/login", { waitUntil: "load", timeout: 15000 });
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 8000 });

  await page.fill("#gate-user", totp_user);
  await page.fill("#gate-pass", totp_pass);
  await page.click("#gate-btn");

  // Password alone is correct but this account has 2FA: admin_auth.login()
  // answers `need_totp` (401) instead of opening the session -- the gate
  // reveals the code field (#gate-2fa) without losing what was typed.
  await expect(page.locator("#gate-2fa")).toBeVisible({ timeout: 8000 });
  expect(await page.locator("#gate-code").inputValue()).toBe("");

  // Computed fresh right before typing it (never the `setup`-time
  // snapshot) so a slow page load can't race the 30s TOTP window -- see
  // harness.mjs's totpCode() docstring.
  const code = await totpCode(totp_secret);
  await page.fill("#gate-code", code);
  await page.click("#gate-btn");

  // Success carries the operator to a real direct screen, never back to
  // the login gate -- a different account (totp_user, role admisionista),
  // so its landing screen is not asserted here (see the ADMIN_USER
  // landing-contract test below for that).
  await page.waitForURL(onDirectScreen, { timeout: 10000 });
});

// Direct /admin/login screen (2026-08-31): the server-routed public twin of
// the gate above -- same markup, same shared admin-gate.css stylesheet, same
// admin-login.mjs controller. These are the BROWSER-path proofs of the
// landing contract: after a real password login in a real Chromium, the
// controller honors the server-validated data-next target first, else lands
// role-aware from the identity the login API returned (admin here ->
// /admin/solicitudes) -- never through the legacy /admin shell. A session
// that dies on a direct screen re-enters through /admin/login?next=<screen>.
// The controller-level twin of these assertions imports the same production
// native module (tests/admin-login-direct.test.mjs); the render-level twin pins
// the served HTML (tests/test_admin_login_screen.py).
test("direct /admin/login: password login lands role-aware on /admin/solicitudes", async ({ page }) => {
  await page.goto("/admin/login", { waitUntil: "load", timeout: 15000 });
  // The standalone gate renders with no panel chrome at all: the form is
  // there, #app is not (nothing to hide -- the page IS the gate).
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 8000 });
  expect(await page.locator("#app").count()).toBe(0);

  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");

  // Success: the login API returns the identity; the server's default
  // data-next="/admin" injection is not a chosen destination, so the
  // controller lands the admin role on /admin/solicitudes -- a real
  // server-routed screen, not the legacy hash shell.
  await page.waitForURL(/\/admin\/solicitudes(\?|#|$)/, { timeout: 15000 });
  await expect(page.locator("#sec-solicitudes")).toBeAttached({ timeout: 10000 });
  expect(await page.evaluate(() => window.SANA_ADMIN_SCREEN), "the tab is on the direct screen, which declares itself").toBe("solicitudes");
  const h1 = page.locator("#adm-h1");
  await expect(h1).toBeVisible({ timeout: 8000 });
  await expect(h1).toHaveText("Solicitudes de turno");
});

test("direct /admin/login?next=/admin/system: password login lands on the requested direct screen", async ({ page }) => {
  await page.goto("/admin/login?next=/admin/system", { waitUntil: "load", timeout: 15000 });
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 8000 });

  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");

  // The server-validated data-next=/admin/system is what the controller
  // redirects to -- a real server-routed screen, authenticated by the
  // session cookie the login just set.
  await page.waitForURL(/\/admin\/system/, { timeout: 15000 });
  await expect(page.locator("#sec-sistema")).toContainText(/integraciones/i, { timeout: 15000, useInnerText: true });
  const h1 = page.locator("#adm-h1");
  await expect(h1).toBeVisible({ timeout: 8000 });
  await expect(h1).toHaveText("Estado");
});

// The expiry half of the contract, on a real screen with the real fetch: a
// session that dies while the operator sits on /admin/account goes back to
// the DIRECT login carrying that exact screen as ?next= — never through the
// legacy /admin shell (which would render nothing useful for a dead session
// anyway). Uses the visible Account save action so the 401 is produced by
// the real API through the real wiring, not a retired global or stubbed fetch.
test("direct /admin/account with a dead session re-enters through /admin/login?next=/admin/account", async ({ page }) => {
  await page.goto("/admin/login", { waitUntil: "load", timeout: 15000 });
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 8000 });
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  // Just a sync point for "logged in" -- NOT the default-landing screen
  // (that's the dedicated test above; ADMIN_USER's stored nav.last_screen
  // preference is not fresh here, since an earlier test in this file
  // already navigated it to /admin/system).
  await page.waitForURL(onDirectScreen, { timeout: 15000 });

  // Land on the direct account screen, then kill the session cookie: the
  // next real profile save answers 401 and the native Account controller's
  // shared session boundary must carry the tab to the login screen.
  await page.goto("/admin/account", { waitUntil: "load", timeout: 15000 });
  await expect.poll(() => page.evaluate(() => window.SANA_ADMIN_SCREEN), { timeout: 10000 }).toBe("cuenta");
  await expect(page.locator("#acct-email-edit")).toBeVisible({ timeout: 12000 });
  await page.click("#acct-email-edit");
  await page.context().clearCookies();
  await page.fill("#acct-email", "expired-session@example.invalid");
  await Promise.all([
    page.waitForResponse((response) =>
      response.url().endsWith("/api/admin/account/profile")
        && response.request().method() === "POST" && response.status() === 401),
    page.locator('#acct-email-form button[type="submit"]').click(),
  ]);

  await page.waitForURL(
    /\/admin\/login\?next=(\/|%2F)admin(\/|%2F)account/,
    { timeout: 15000 },
  );
  // And the login screen offers to return exactly there: the server
  // validated the ?next and injected it as the form's data-next.
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 8000 });
  await expect(page.locator("#gate-form")).toHaveAttribute("data-next", "/admin/account");
});

test("direct deep state survives session expiry and a full login round-trip", async ({ page }) => {
  const target = "/admin/legal?doc=privacidad";
  await page.goto("/admin/login", { waitUntil: "load", timeout: 15000 });
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  // Sync point only -- same caveat as the account-expiry test above.
  await page.waitForURL(onDirectScreen, { timeout: 15000 });

  await page.goto(target, { waitUntil: "load", timeout: 15000 });
  await expect.poll(
    () => page.evaluate(() => window.SANA_ADMIN_SCREEN === "legal" && location.search === "?doc=privacidad"),
    { timeout: 10000 },
  ).toBe(true);
  // Let the shell's own first reads (alerts count, preferences, the legal
  // list) finish while the session is valid. Under gate load one of them
  // could otherwise land after the cookie is cleared, get its 401 and send
  // the page to login before the save below ever runs.
  await expect(page.locator("#lg-guardar")).toBeVisible({ timeout: 10000 });
  await page.waitForLoadState("networkidle", { timeout: 15000 });
  await page.context().clearCookies();
  // The editor has already cached the legal document, so re-routing it
  // would not cross the API boundary. Saving the unchanged text does: with
  // the cookie gone the real PUT is rejected before any mutation and the
  // controller must preserve the current direct URL through re-login.
  const legal401 = page.waitForResponse((response) =>
    response.url().includes("/api/admin/legal/") && response.status() === 401);
  await page.click("#lg-guardar");
  await legal401;

  await page.waitForURL(/\/admin\/login\?next=/, { timeout: 15000 });
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 8000 });
  await expect(page.locator("#gate-form")).toHaveAttribute("data-next", target);

  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  await page.waitForURL(/\/admin\/legal\?doc=privacidad$/, { timeout: 15000 });
  await expect.poll(
    () => page.evaluate(() => window.SANA_ADMIN_SCREEN === "legal" && location.search === "?doc=privacidad"),
    { timeout: 10000 },
  ).toBe(true);
});
