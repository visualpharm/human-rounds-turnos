// Native Settings lifecycle acceptance. The application shell/template and
// controller are real; every Settings API response below is public, local and
// deliberately synthetic. Do not replace this with copied fixture HTML.
//
// Port of tests/e2e/admin-settings-lifecycle.e2e.mjs (21 tests, same titles, same order).
//
// Differences from the node:test original:
// - Every test is independent (the Settings API is answered by the test's own route, nothing is
//   stored), so the file is NOT serial. The page comes from the `adminPage` fixture (signed in through
//   the same /admin/login?next=/admin/account form; the Settings API routes are installed afterwards,
//   as in the original). The viewport is 1280x900 (`test.use`); the per-width and 390px tests set it
//   on the page before the first `/admin/settings` load (the original created the context with it).
// - Screenshots go through `saveShot` to the same files as before (artifactPath("human-rounds",
//   "settings-lifecycle-audit", "browser-<timestamp>")) and are attached to the report.
// - `page.waitForNavigation` (deprecated) is `page.waitForEvent("load")`, armed before the Reload click.
// - Waits and field checks are web-first (`toBeVisible`, `toHaveAttribute`, `toHaveValue`, `toHaveURL`,
//   `toBeEnabled`, `toHaveCount`); the exact-focus check after the direct link stays the original's
//   single read of `document.activeElement` once the input is visible (`toBeFocused()` would also
//   pass when focus arrives late). Checks that guard a moment stay point-in-time reads where the original took them: URL search strings,
//   request counters and the recorded POST bodies, a still-disabled save after card replacement,
//   an empty message, the checkbox rolled back after a refused save, the stale-owner text, no alert
//   after a session redirect.
// - The pending-promise helpers (`deferred()`) stay, and each held one is also released on cleanup,
//   so a failing test never leaves a route hanging (replaces the `finally { release() }` blocks).
// - The original's first statement of the delete-failure test registers an `unhandledrejection`
//   listener on the page that is still on /admin/account; the following `goto` discards it. It is
//   kept exactly as it was (see the report), the `pageerror` collection is the effective guard.
// - Locators that stay CSS: the Settings screen's own markup contract (`#sec-settings`, `.set-card`,
//   `[data-settings-view]`, `[data-campo=...]`, `[data-msg=...]`, `[data-retry]`, `.set-subnav`) is what
//   is driven and measured; alerts are `getByRole("alert")` inside `#sec-settings`.
import { join } from "node:path";
import {
  test, expect, saveShot, artifactPath,
} from "./lib/fixtures.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_settings_lifecycle" },
  viewport: { width: 1280, height: 900 },
});

const evidence = artifactPath("human-rounds", "settings-lifecycle-audit", `browser-${new Date().toISOString().replaceAll(":", "-")}`);

const FIXTURE = {
  lock: false,
  location: {
    cadena_actual: ["global", "example-town"],
    paquetes_disponibles: [
      { id: "global", name: "Global", level: 0, parent: "" },
      { id: "example-town", name: "Example Town", level: 3, parent: "global" },
    ],
    modo_paquetes: true,
    identidad: {
      hospital: "Example Hospital", city: "Example Town", province: "Example Province",
      network: "Example Network", network_long: "Example Public Health Network", emergency_number: "000",
      main_center: "example-hospital",
    },
    centros: [
      { slug: "example-clinic", nombre: "Example Clinic" },
      { slug: "example-hospital", nombre: "Example Hospital" },
    ],
  },
  languages: { disponibles: ["en", "es"], habilitados: ["en", "es"], default: "en", modo_switcher: "nav" },
  chat: { greetings: [], max_length: 1200 },
  turnos: { max_activos: 1 },
  seguridad: { ips_exentas: [] },
  connectors: { emr_sync: { hsi_enabled: false } },
  api_keys: [{
    id: "mailgun", titulo: "Mailgun", probar: "mailgun", campos: [
      { clave: "MAILGUN_DOMAIN", etiqueta: "Sending domain", tipo: "text", valor: "" },
      { clave: "MAILGUN_API_KEY", etiqueta: "API key", tipo: "secret", configurado: true, last4: "1234" },
    ],
  }],
};

function copy(value) { return JSON.parse(JSON.stringify(value)); }
function deferred(cleanup) {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  cleanup(() => resolve());
  return { promise, resolve };
}
function json(route, status, body) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}
function settingsGet(route, body = FIXTURE) { return json(route, 200, body); }
async function render(page, suffix = "") {
  await page.goto("/admin/settings" + suffix, { waitUntil: "load" });
  await expect(page.locator("#sec-settings .set-card").first()).toBeVisible({ timeout: 10000 });
}
async function expectFit(page, width) {
  const geometry = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
    controls: [...document.querySelectorAll("#sec-settings input, #sec-settings select, #sec-settings textarea")]
      .map((node) => Number.parseFloat(getComputedStyle(node).fontSize)),
  }));
  expect(geometry.content, `${width}px Settings overflow: ${JSON.stringify(geometry)}`).toBeLessThanOrEqual(geometry.viewport + 1);
  expect(Math.min(...geometry.controls), `${width}px Settings control text is undersized`).toBeGreaterThanOrEqual(16);
}
async function restore(page) {
  await page.evaluate(() => {
    dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
  });
}

for (const width of [360, 390, 768, 1024, 1280]) {
  test(`Settings ${width}px renders the actual direct screen without overflow`, async ({ adminPage: page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/admin/settings", (route) => settingsGet(route));
    await render(page);
    await expect(page.locator('[data-settings-view="general"]')).toHaveAttribute("aria-current", "page");
    await expectFit(page, width);
    await saveShot(testInfo, page, join(evidence, `settings-${width}.png`), { fullPage: true });
  });
}

test("Settings query navigation owns direct links, history, and exact field focus", async ({ adminPage: page }) => {
  await page.route("**/api/admin/settings", (route) => settingsGet(route));
  await render(page, "?view=integrations");
  await expect(page.locator('[data-integracion-card="mailgun"]')).toBeVisible();
  expect(new URL(page.url()).search).toBe("?view=integrations");
  await page.locator('[data-settings-view="general"]').click();
  await expect(page.locator('[data-campo="SITE_ID_CITY"] input')).toBeVisible();
  expect(new URL(page.url()).search).toBe("");
  await page.goBack({ waitUntil: "load" });
  await expect(page.locator('[data-integracion-card="mailgun"]')).toBeVisible();
  await page.goto("/admin/settings?view=general&field=MAILGUN_DOMAIN", { waitUntil: "load" });
  const domain = page.locator('[data-campo="MAILGUN_DOMAIN"] input');
  await expect(domain).toBeVisible();
  expect(new URL(page.url()).search).toBe("?field=MAILGUN_DOMAIN");
  expect(await domain.evaluate((node) => node === document.activeElement)).toBe(true);
});

test("Settings saves exactly its location POST and preserves an unsaved sibling-card draft", async ({ adminPage: page }) => {
  const received = [];
  await page.route("**/api/admin/settings", async (route) => {
    if (route.request().method() === "GET") return settingsGet(route);
    const request = route.request().postDataJSON();
    received.push(request);
    return json(route, 200, { ok: true, location: copy(FIXTURE.location) });
  });
  await render(page);
  expect(await page.locator('[data-key="PACK_CHAIN"] option:checked').evaluateAll((options) => options.map((option) => option.value))).toEqual(["global", "example-town"]);
  await page.locator('[data-campo="SITE_ID_HOSPITAL"] input').fill("Saved Example Hospital");
  // «Centro principal» offers the installation's own centers, current one selected.
  const mainCenter = page.locator('[data-campo="SITE_ID_MAIN_CENTER"] select');
  await expect(mainCenter).toHaveValue("example-hospital");
  await mainCenter.selectOption("example-clinic");
  await page.locator('[data-campo="IPS_EXENTAS"] textarea').fill("127.0.0.1\n::1");
  const response = page.waitForResponse((response) => response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/admin/settings");
  await page.locator('.set-card[data-grupo="location"] .set-guardar').click();
  expect((await response).ok(), "the originating save POST acknowledges successfully").toBe(true);
  expect(received, "save belongs only to Location: it must omit the unsaved Security-card draft and all secret fields").toEqual([{
    grupo: "location",
    valores: {
      PACK_CHAIN: "global,example-town", SITE_ID_HOSPITAL: "Saved Example Hospital",
      SITE_ID_CITY: "Example Town", SITE_ID_PROVINCE: "Example Province",
      SITE_ID_NETWORK: "Example Network", SITE_ID_NETWORK_LONG: "Example Public Health Network",
      SITE_ID_EMERGENCY_NUMBER: "000", SITE_ID_MAIN_CENTER: "example-clinic",
    },
  }]);
  await expect(page.locator('[data-msg="location"]').getByText("Guardado.")).toBeVisible();
  expect(received.length, "one click creates one owned mutation").toBe(1);
  await expect(page.locator('[data-campo="IPS_EXENTAS"] textarea')).toHaveValue("127.0.0.1\n::1");
});

for (const failure of ["main-import", "dependency-import", "api-403", "api-500", "offline"]) {
  test(`Settings ${failure} renders recovery and a fresh control path`, async ({ adminPage: page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 900 });
    const asset = failure === "main-import" ? "**/static/platform/admin-settings.*.mjs"
      : "**/static/platform/i18n.*.mjs";
    if (failure.endsWith("import")) await page.route(asset, (route) => route.abort("failed"));
    else await page.route("**/api/admin/settings", (route) => {
      if (failure === "offline") return route.abort("failed");
      return json(route, failure === "api-403" ? 403 : 500, { error: "synthetic_failure" });
    });
    await page.goto("/admin/settings", { waitUntil: "load" });
    await expect(page.locator("#sec-settings").getByRole("alert")).toBeVisible({ timeout: 12000 });
    expect(new URL(page.url()).pathname).toBe("/admin/settings");
    const recoveryNav = await page.locator(".set-subnav").evaluate((nav) => {
      const links = Array.from(nav.querySelectorAll("a"), (a) => a.getBoundingClientRect());
      return { display: getComputedStyle(nav).display,
        gaps: links.slice(1).map((r, i) => r.top === links[i].top ? r.left - links[i].right : r.top - links[i].bottom) };
    });
    expect(recoveryNav.display, "server-owned navigation survives a rejected controller import").toBe("flex");
    expect(recoveryNav.gaps.every((gap) => gap >= 8), "recovery navigation links remain visibly separated").toBe(true);
    await saveShot(testInfo, page, join(evidence, `recovery-${failure}-390.png`), { fullPage: true });
    if (failure.endsWith("import")) await page.unroute(asset);
    else await page.unroute("**/api/admin/settings");
    await page.route("**/api/admin/settings", (route) => settingsGet(route));
    const retry = page.locator("#sec-settings [data-retry]");
    await expect(retry, "every failed Settings owner exposes its actual retry control").toHaveCount(1);
    const reloaded = page.waitForEvent("load");
    await retry.click();
    await reloaded;
    await expect(page.locator("#sec-settings .set-card").first()).toBeVisible({ timeout: 10000 });
    await page.locator('[data-campo="SITE_ID_CITY"] input').fill("Fresh after failure");
    await expect(page.locator('[data-campo="SITE_ID_CITY"] input')).toHaveValue("Fresh after failure");
  });
}

test("Settings confirmed 401 redirects only after shared whoami confirmation", async ({ adminPage: page }) => {
  await page.route("**/api/admin/settings", (route) => json(route, 401, { error: "no_autenticado" }));
  await page.route("**/api/admin/whoami", (route) => json(route, 401, { error: "no_autenticado" }));
  await page.goto("/admin/settings", { waitUntil: "load" });
  await expect(page).toHaveURL(/\/admin\/login\?next=%2Fadmin%2Fsettings/);
  expect(await page.locator("#sec-settings").getByRole("alert").count()).toBe(0);
});

test("Settings confirmed 2FA contract redirects to the repair surface", async ({ adminPage: page }) => {
  await page.route("**/api/admin/settings", (route) => json(route, 403, { error: "2fa_requerido" }));
  await page.goto("/admin/settings", { waitUntil: "load" });
  await expect(page).toHaveURL(/\/admin\/account\?setup=2fa/);
  expect(await page.locator("#sec-settings").getByRole("alert").count()).toBe(0);
});

test("Settings pagehide/pageshow rejects an old read and paints only the fresh owner", async ({ adminPage: page, cleanup }) => {
  const oldHeld = deferred(cleanup);
  const oldStarted = deferred(cleanup);
  const oldProcessed = deferred(cleanup);
  const freshReady = deferred(cleanup);
  let calls = 0;
  await page.route("**/api/admin/settings", async (route) => {
    calls += 1;
    if (calls === 1) {
      oldStarted.resolve();
      await oldHeld.promise;
      try { await settingsGet(route, { ...copy(FIXTURE), lock: true }); }
      finally { oldProcessed.resolve(); }
      return;
    }
    await settingsGet(route);
    freshReady.resolve();
  });
  await page.goto("/admin/settings", { waitUntil: "load" });
  await oldStarted.promise;
  await restore(page);
  await expect(page.locator("#sec-settings .set-card").first()).toBeVisible();
  await freshReady.promise;
  expect(calls, "pageshow creates exactly one fresh owner read before the old reply is released").toBe(2);
  oldHeld.resolve();
  await oldProcessed.promise;
  await expect(page.locator('[data-campo="SITE_ID_HOSPITAL"] input')).toHaveValue("Example Hospital");
  expect(new URL(page.url()).pathname).toBe("/admin/settings");
  expect(await page.locator("#sec-settings").innerText()).not.toMatch(/La configuración está bloqueada/);
});

test("Settings stale confirmed expiry after pagehide cannot redirect or repaint the fresh owner", async ({ adminPage: page, cleanup }) => {
  const releaseWhoami = deferred(cleanup);
  const whoamiStarted = deferred(cleanup);
  const whoamiProcessed = deferred(cleanup);
  const freshReady = deferred(cleanup);
  let reads = 0;
  let posts = 0;
  await page.route("**/api/admin/settings", async (route) => {
    if (route.request().method() === "GET") {
      reads += 1;
      await settingsGet(route);
      if (reads === 2) freshReady.resolve();
      return;
    }
    posts += 1;
    return json(route, 401, { error: "no_autenticado" });
  });
  await page.route("**/api/admin/whoami", async (route) => {
    whoamiStarted.resolve();
    await releaseWhoami.promise;
    try { await json(route, 401, { error: "no_autenticado" }); }
    finally { whoamiProcessed.resolve(); }
  });
  await render(page);
  await page.locator('[data-campo="SITE_ID_CITY"] input').fill("Held mutation");
  await page.locator('.set-card[data-grupo="location"] .set-guardar').click();
  await expect(page.locator('.set-card[data-grupo="location"] .set-guardar')).toBeDisabled();
  await whoamiStarted.promise;
  await restore(page);
  await expect(page.locator("#sec-settings .set-card").first()).toBeVisible();
  await freshReady.promise;
  releaseWhoami.resolve();
  await whoamiProcessed.promise;
  await expect(page.locator('[data-campo="SITE_ID_CITY"] input')).toHaveValue("Held mutation");
  expect(new URL(page.url()).pathname).toBe("/admin/settings");
  expect(await page.locator('[data-msg="location"]').textContent()).toBe("");
  expect(posts).toBe(1);
});

test("Settings early navigation before the first API response retains the requested view", async ({ adminPage: page, cleanup }) => {
  const held = deferred(cleanup);
  await page.route("**/api/admin/settings", async (route) => { await held.promise; return settingsGet(route); });
  await page.goto("/admin/settings", { waitUntil: "domcontentloaded" });
  await page.locator('[data-settings-view="integrations"]').click();
  held.resolve();
  await expect(page.locator('[data-integracion-card="mailgun"]')).toBeVisible();
  expect(new URL(page.url()).search).toBe("?view=integrations");
});

test("pending Settings writes stay single across local navigation and retain newer input", async ({ adminPage: page, cleanup }) => {
  const held = deferred(cleanup);
  let posts = 0;
  await page.route("**/api/admin/settings", async (route) => {
    if (route.request().method() === "GET") return settingsGet(route);
    posts++;
    await held.promise;
    return json(route, 200, { ok: true, location: copy(FIXTURE.location) });
  });
  await render(page);
  await page.locator('[data-campo="SITE_ID_CITY"] input').fill("First edit");
  await page.locator('.set-card[data-grupo="location"] .set-guardar').click();
  await page.locator('[data-settings-view="connectors"]').click();
  await page.locator('[data-settings-view="general"]').click();
  const save = page.locator('.set-card[data-grupo="location"] .set-guardar');
  expect(await save.isDisabled(), "same operation remains pending after card replacement").toBe(true);
  await page.locator('[data-campo="SITE_ID_CITY"] input').fill("Newer edit");
  held.resolve();
  await expect(save).toBeEnabled();
  expect(posts).toBe(1);
  await expect(page.locator('[data-campo="SITE_ID_CITY"] input')).toHaveValue("Newer edit");
  expect(await page.locator('[data-msg="location"]').textContent(), "obsolete view may not claim its old save acknowledged current input").toBe("");
});

test("connector save needs its own acknowledgement before refreshing or displaying success", async ({ adminPage: page }) => {
  let gets = 0, posts = 0;
  const data = copy(FIXTURE);
  await page.route("**/api/admin/settings", (route) => {
    if (route.request().method() === "GET") { gets++; return settingsGet(route, data); }
    posts++;
    if (posts === 1) return json(route, 200, {});
    data.connectors.emr_sync.hsi_enabled = true;
    return json(route, 200, { ok: true, connectors: data.connectors });
  });
  await render(page, "?view=connectors");
  // click(), not check(): the save fires on the click and the page rolls a
  // refused save back at once — check() re-reads the box afterwards and,
  // when the rollback wins that race, reports "did not change its state".
  // The states that matter are asserted explicitly below.
  await page.locator("[data-connector-hsi]").click();
  await expect(page.locator('[data-msg="connectors"].err')).toBeVisible();
  expect(gets, "malformed POST acknowledgement cannot cause a success-looking refresh").toBe(1);
  expect(await page.locator("[data-connector-hsi]").isChecked()).toBe(false);
  await page.locator("[data-connector-hsi]").click();
  await expect(page.locator('[data-msg="connectors"]').getByText("Guardado.")).toBeVisible();
  expect(gets).toBe(2);
  expect(posts).toBe(2);
  expect(await page.locator("[data-connector-hsi]").isChecked()).toBe(true);
});

test("Settings remount keeps non-secret drafts without retaining secret draft values", async ({ adminPage: page }) => {
  await page.route("**/api/admin/settings", (route) => settingsGet(route));
  await render(page, "?view=integrations");
  await page.locator('[data-campo="MAILGUN_DOMAIN"] input').fill("draft.example");
  await page.locator('[data-campo="MAILGUN_API_KEY"] input').fill("synthetic-not-a-real-key");
  await restore(page);
  await expect(page.locator('[data-campo="MAILGUN_API_KEY"] input')).toHaveValue("");
  await expect(page.locator('[data-campo="MAILGUN_DOMAIN"] input')).toHaveValue("draft.example");
  expect(await page.evaluate(() => Object.values(document.getElementById("sec-settings").__settingsDraft || {}).includes("synthetic-not-a-real-key"))).toBe(false);
});

test("Settings delete failure is visible, handles no rejection, and leaves a fresh control", async ({ adminPage: page }) => {
  const errors = [];
  await page.evaluate(() => addEventListener("unhandledrejection", (event) => {
    window.__settingsUnhandled = (window.__settingsUnhandled || 0) + 1;
  }));
  await page.route("**/api/admin/settings", (route) => {
    if (route.request().method() === "GET") return settingsGet(route);
    return json(route, 500, { error: "synthetic delete failure" });
  });
  page.on("pageerror", (error) => errors.push(error.message));
  await render(page, "?view=integrations");
  const remove = page.locator('[data-campo="MAILGUN_API_KEY"] .set-borrar');
  await remove.click();
  await expect(page.locator('[data-msg="api_keys-mailgun"]').getByText("synthetic delete failure")).toBeVisible();
  expect(await remove.isDisabled(), "failed delete re-enables its original button").toBe(false);
  expect(await page.evaluate(() => window.__settingsUnhandled || 0)).toBe(0);
  expect(errors).toEqual([]);
  await page.locator('[data-campo="MAILGUN_DOMAIN"] input').fill("fresh-control.example");
  await expect(page.locator('[data-campo="MAILGUN_DOMAIN"] input')).toHaveValue("fresh-control.example");
});
