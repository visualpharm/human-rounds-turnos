// Delayed native admin-login controller on the real /admin/login gate.
// A source scan cannot prove an early submit is held until the controller
// resolves, then processed without the browser's default form navigation.
//
// Port of tests/e2e/admin-login-delayed.e2e.mjs (6 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions })); the runner's
//   page replaces `browser.newPage()`. The last test enables English for the whole app through
//   the settings API (as before it is the last of the file, so the Spanish-text tests before it
//   are unaffected); it signs in on a context of its own (`newContext`, closed straight after)
//   with the shared loginAdmin() and posts with page.request instead of an in-page fetch.
// - The hand-made deferred() objects are holdGate(cleanup) gates (also released on cleanup, so
//   a failing test never leaves a route hanging) plus plain flags set by the route handlers.
//   The route handler of test 1 no longer asserts the module path inside the handler (an
//   exception there is not a test failure): it records the URL and the test asserts it with
//   the module-URL check that was already there. `route.continue()` tolerates a closed page.
// - «Was the held module requested within 8 s» is expect.poll on a flag (same 8 s) instead of
//   a Promise.race against a timer; the other "was requested" waits (no timeout in the
//   original) are expect.poll with the config's default timeout.
// - The three 300 ms sleeps before «nothing has been posted yet» (queued
//   credentials, post-bind credentials, English catalog) are 300 ms holdsFor() windows over
//   the same assertion: the absence over time is the claim.
// - Waits are web-first (gate error text -> toHaveText with the same regexes, 2FA block
//   -> expect.poll on the same inline-style predicate). The state read right after an early
//   submit (URL, query string, error text, legacy global) stays a point-in-time snapshot.
// - Locators left as CSS: the gate's ids (#gate-form, #gate-user, #gate-pass, #gate-btn,
//   #gate-err, #gate-2fa, #gate-intro, #gate-code) - the markup contract of the login screen.
import { test, expect, loginAdmin, holdGate, holdsFor, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_admin_login_delayed" } });

const MODULE_NAME = "platform/admin-login.mjs";
const MODULE_PATH = /\/static\/platform\/admin-login\.[a-f0-9]{8}\.mjs$/;

async function enableEnglish(page, app) {
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/settings");
  const saved = await page.request.post("/api/admin/settings", { data: {
    grupo: "languages",
    valores: { LANGUAGES: "es,en", LANG_DEFAULT: "en", LANG_SWITCHER_MODE: "secret" },
  } });
  expect(saved.ok(), `English bootstrap setup failed: ${await saved.text()}`).toBe(true);
}

test("early submit waits for the native login module; never the retired global or classic script", async ({ page, cleanup }) => {
  const gate = holdGate(cleanup);
  let moduleRequested = false;
  let moduleURL = "";
  const failures = [];
  page.on("pageerror", (error) => failures.push({ pageerror: String(error) }));
  await page.route("**/static/platform/admin-login.*.mjs", async (route) => {
    moduleURL = route.request().url();
    moduleRequested = true;
    await gate.held;
    await route.continue().catch(() => {});
  });

  const navigation = page.goto("/admin/login", { waitUntil: "domcontentloaded", timeout: 20000 });
  await expect.poll(() => moduleRequested, {
    timeout: 8000, message: `${MODULE_NAME} was not requested from the manifest`,
  }).toBe(true);

  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 8000 });
  const whileDelayed = await page.evaluate(() => ({
    legacyGlobal: typeof window.SanaAdminLogin,
    manifestURL: window.SANA_STATIC?.["platform/admin-login.mjs"] || "",
    screen: window.SANA_ADMIN_SCREEN || "",
  }));
  expect(whileDelayed.legacyGlobal).toBe("undefined");
  expect(whileDelayed.manifestURL).toMatch(MODULE_PATH);
  expect(whileDelayed.screen).toBe("login");

  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn", { noWaitAfter: true });

  const held = await page.evaluate(() => ({
    path: location.pathname,
    search: location.search,
    visible: !!document.getElementById("gate-form"),
    err: (document.getElementById("gate-err")?.textContent || "").trim(),
    legacyGlobal: typeof window.SanaAdminLogin,
  }));
  expect(held.path, "early submit must not navigate away").toBe("/admin/login");
  expect(held.search.indexOf("password"), "password must not leak onto the query string").toBe(-1);
  expect(held.visible).toBe(true);
  expect(held.err, "untranslated errors must not paint before i18n").toBe("");
  expect(held.legacyGlobal).toBe("undefined");

  gate.release();
  await navigation;
  await page.waitForURL(/\/admin\/solicitudes(\?|#|$)/, { timeout: 15000 });
  expect(new URL(moduleURL).pathname).toMatch(MODULE_PATH);
  const after = await page.evaluate(() => ({
    legacyGlobal: typeof window.SanaAdminLogin,
    manifestURL: window.SANA_STATIC?.["platform/admin-login.mjs"] || "",
  }));
  expect(after.legacyGlobal).toBe("undefined");
  expect(failures).toEqual([]);
});

test("a failed native controller keeps the password out of the URL and recovers on reload", async ({ page }) => {
  let failFirstController = true;
  const failures = [];
  page.on("pageerror", (error) => failures.push(String(error)));
  await page.route("**/static/platform/admin-login.*.mjs", async (route) => {
    if (failFirstController) {
      failFirstController = false;
      await route.abort("failed");
      return;
    }
    await route.continue();
  });

  await page.goto("/admin/login", { waitUntil: "domcontentloaded", timeout: 20000 });
  await expect(page.locator("#gate-err")).toHaveText(/cargar el acceso|sign-in screen couldn't load/i);

  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn", { noWaitAfter: true });
  const held = await page.evaluate(() => ({ path: location.pathname, search: location.search }));
  expect(held.path).toBe("/admin/login");
  expect(held.search.includes("password"), "failed controller must not expose the password").toBe(false);
  expect(await page.evaluate(() => typeof window.SanaAdminLogin)).toBe("undefined");
  expect(failures, "the rejected native import is handled by the standalone bootstrap").toEqual([]);

  await page.reload({ waitUntil: "domcontentloaded", timeout: 20000 });
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  await page.waitForURL(/\/admin\/solicitudes(\?|#|$)/, { timeout: 15000 });
});

test("a rejected i18n module falls back without blocking password login", async ({ page }) => {
  let i18nRequests = 0;
  await page.route("**/static/platform/i18n.*.mjs", async (route) => {
    i18nRequests += 1;
    await route.abort("failed");
  });
  await page.route("**/api/admin/login", async (route) => {
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ error: "credenciales" }),
    });
  });

  await page.goto("/admin/login", { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  await expect(page.locator("#gate-err")).toHaveText(/Usuario o contraseña incorrectos/);

  expect(i18nRequests, "the real fingerprinted i18n module was rejected").toBeGreaterThanOrEqual(1);
  expect(await page.evaluate(() => location.pathname)).toBe("/admin/login");
  expect(await page.evaluate(() => typeof window.SanaAdminLogin)).toBe("undefined");
});

test("queued submit waits for delayed configuration before posting credentials", async ({ page, cleanup }) => {
  const moduleGate = holdGate(cleanup);
  const configGate = holdGate(cleanup);
  let moduleRequested = false;
  let configRequested = false;
  let loginPosts = 0;
  await page.route("**/static/platform/admin-login.*.mjs", async (route) => {
    moduleRequested = true;
    await moduleGate.held;
    await route.continue().catch(() => {});
  });
  await page.route("**/api/admin/config", async (route) => {
    configRequested = true;
    await configGate.held;
    await route.continue().catch(() => {});
  });
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/admin/login") loginPosts += 1;
  });

  const navigation = page.goto("/admin/login", { waitUntil: "domcontentloaded", timeout: 20000 });
  await expect.poll(() => moduleRequested).toBe(true);
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn", { noWaitAfter: true });

  moduleGate.release();
  await expect.poll(() => configRequested).toBe(true);
  await holdsFor(() => {
    expect(loginPosts, "queued credentials must wait for the Turnstile configuration response").toBe(0);
  }, { during: 300 });

  configGate.release();
  await navigation;
  await page.waitForURL(/\/admin\/solicitudes(\?|#|$)/, { timeout: 15000 });
  expect(loginPosts).toBe(1);
});

test("an ordinary post-bind submit waits for delayed Turnstile configuration", async ({ page, cleanup }) => {
  const configGate = holdGate(cleanup);
  let configRequested = false;
  const loginPayloads = [];
  await page.addInitScript(() => {
    window.turnstile = {
      render: () => 1,
      getResponse: () => "turnstile-token",
      reset: () => {},
    };
  });
  await page.route("**/turnstile/v0/api.js*", async (route) => {
    await route.fulfill({ contentType: "application/javascript", body: "" });
  });
  await page.route("**/api/admin/config", async (route) => {
    configRequested = true;
    await configGate.held;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ turnstile_sitekey: "test-sitekey", totp: false, usuario: "admin" }),
    }).catch(() => {});
  });
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/admin/login") {
      loginPayloads.push(JSON.parse(request.postData() || "{}"));
    }
  });

  await page.goto("/admin/login", { waitUntil: "domcontentloaded", timeout: 20000 });
  await expect.poll(() => configRequested).toBe(true);
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn", { noWaitAfter: true });
  await holdsFor(() => {
    expect(loginPayloads.length, "post-bind credentials must wait for the configuration response").toBe(0);
  }, { during: 300 });
  expect(await page.locator("#gate-btn").isDisabled(), "the in-flight readiness wait prevents duplicate submits").toBe(true);

  configGate.release();
  await page.waitForURL(/\/admin\/solicitudes(\?|#|$)/, { timeout: 15000 });
  expect(loginPayloads).toEqual([{
    usuario: ADMIN_USER,
    password: ADMIN_PASS,
    codigo: "",
    turnstile_token: "turnstile-token",
  }]);
});

test("a delayed native i18n module keeps English password and TOTP feedback English", async ({ page, newContext, app, cleanup }) => {
  const gate = holdGate(cleanup);
  let i18nRequested = false;
  let loginAttempts = 0;
  const setupContext = await newContext();
  const setup = await setupContext.newPage();
  await enableEnglish(setup, app);
  await setupContext.close();
  await page.route("**/static/platform/i18n.*.mjs", async (route) => {
    i18nRequested = true;
    await gate.held;
    await route.continue().catch(() => {});
  });
  await page.route("**/api/admin/login", async (route) => {
    loginAttempts += 1;
    const body = loginAttempts === 1
      ? { error: "credenciales" }
      : loginAttempts === 2
        ? { need_totp: true }
        : { need_totp: true, error: "codigo" };
    await route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/admin/login", { waitUntil: "domcontentloaded", timeout: 20000 });
  await expect.poll(() => i18nRequested).toBe(true);
  expect(await page.evaluate(() => document.documentElement.lang)).toBe("en");
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn", { noWaitAfter: true });
  await holdsFor(() => {
    expect(loginAttempts, "feedback waits for the active English catalog").toBe(0);
  }, { during: 300 });

  gate.release();
  await expect(page.locator("#gate-err")).toHaveText(/Incorrect username or password\./);
  expect(await page.locator("#gate-err").textContent()).not.toMatch(/Usuario o contraseña incorrectos/);

  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  await expect.poll(() => page.evaluate(() => document.getElementById("gate-2fa")?.style.display !== "none")).toBe(true);
  await expect(page.locator("#gate-intro")).toHaveText(/Enter the 6-digit code/);

  await page.fill("#gate-code", "123456");
  await page.click("#gate-btn");
  await expect(page.locator("#gate-err")).toHaveText(/Incorrect code\. Try again\./);
  expect(loginAttempts).toBe(3);
});
