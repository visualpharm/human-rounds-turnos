// DB-free browser proof for the direct admin controllers. It serves the
// production JS under ADMIN_SOURCE_ROOT but fulfils every request locally.
//
// Port of tests/e2e/admin-simplification.e2e.mjs (8 tests, same titles, same order).
//
// Differences from the node:test original:
// - The runner owns the browser: fixture() takes a browser context and opens a page in it
//   (the original launched its own chromium per call and closed the whole browser at the
//   end). Tests that loop over screens open every iteration in a context of its own
//   (`newContext`) and close the page in `finally`, as the original closed its browser.
//   The synthetic page does not use the application: The file is `hermetic` (appOptions only names it for
//   tests/e2e-spec-isolation.test.mjs): no app or database is started for it.
// - `page.setDefaultTimeout(4000)` is kept (actions), and every `waitFor` / `waitForFunction`
//   of the original is a retrying expect / expect.poll with that same 4 s timeout.
// - The fixture's «50 ms after goto, fail fast on a page error» sleep is a 50 ms holdsFor()
//   window that throws the same `controller error (...)` as soon as a page error shows.
// - The hand-made hold of one API response (`started` / `ready` / `completed` promises)
//   is a holdGate(cleanup) gate wrapped by heldRequest(): it is released on cleanup too, so
//   a failing test never leaves a route hanging. The 2-frame wait before comparing the screen
//   after releasing an old response is unchanged.
// - Race guards stay point-in-time reads: what the screen shows after an old response is
//   released, the draft values after a remount, the number of requests, the `data-ready`
//   attribute right after destroy().
// - Locators left as CSS: the synthetic markup's ids / data attributes (#cnt-form, #kn-form,
//   #pr-en, [data-tab], [data-med-select], ...), which are the contract the controllers write.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test, expect, holdGate, holdsFor } from "./lib/fixtures.mjs";
import { platformDeps } from "./lib/static-deps.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_admin_simplification", hermetic: true } });

const root = process.env.ADMIN_SOURCE_ROOT || fileURLToPath(new URL("../../", import.meta.url));
const integrationData = { lock: false, location: {}, languages: {}, chat: { greetings: [] }, turnos: {}, seguridad: { ips_exentas: [] }, connectors: { emr_sync: { hsi_enabled: false } }, api_keys: [
  { id: "first", titulo: "First", campos: [{ clave: "FIRST", etiqueta: "First", tipo: "text", valor: "" }] },
  { id: "second", titulo: "Second", campos: [{ clave: "SECOND", etiqueta: "Second", tipo: "text", valor: "" }] },
] };
const doctors = ["Ana", "Beto", "Carla"].map((nombre, i) => ({ id: `doctor-${i}`, nombre, especialidad: "Clínica", centros: [] }));
const js = (path) => readFileSync(root + "/" + path, "utf8");
const phone = JSON.parse(js("packs/ar/country.json")).phone;
const modules = Object.fromEntries(["platform/hours.mjs", "platform/dates.mjs", "platform/format.mjs", "platform/admin-combo.mjs", "platform/admin-preferences.mjs", "platform/admin-session.mjs", "platform/islands.mjs", "platform/calendar-grid.mjs", "platform/admin-prompts.mjs", "platform/admin-legal.mjs", "platform/admin-knowledge.mjs", "platform/admin-table.mjs", "platform/prep.mjs"].map((name) => [name, "/" + name]));
modules["platform/i18n.mjs"] = "/platform/i18n.mjs";
modules["platform/search.mjs"] = "/platform/search.mjs";
modules["platform/admin-hours-editor.mjs"] = "/platform/admin-hours-editor.mjs";
// Genuine catalog-editor dependencies (admin-content.mjs resolves every
// import through window.SANA_STATIC, fail-closed on a missing key): the
// users module is an EAGER import on the medicos screen and the
// create-appointment helper is its on-demand sibling.
modules["platform/admin-users.mjs"] = "/platform/admin-users.mjs";
modules["platform/admin-create-appointment.mjs"] = "/platform/admin-create-appointment.mjs";
// Everything the mounted controllers import, read from their source (lib/static-deps.mjs):
// a new dependency of a controller lands in this map without editing the spec.
for (const name of platformDeps(root, ["admin-content", "admin-settings", "admin-preparations", "admin-knowledge", "admin-prompts", "admin-legal"]
  .map((controller) => `static/platform/${controller}.mjs`))) modules[name] ??= "/" + name;

// Timeout of the original page.setDefaultTimeout(4000), also used for its waitFor calls.
const WAIT = { timeout: 4000 };

async function fixture(context, screen, pathname, options = {}) {
  const page = await context.newPage();
  page.setDefaultTimeout(4000);
  const errors = [], calls = [], requestCounts = Object.create(null);
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/*", async (route) => {
    const req = route.request(), url = new URL(req.url());
    requestCounts[url.pathname] = (requestCounts[url.pathname] || 0) + 1;
    const json = (body) => route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    if (options.hold && url.pathname === options.hold.path && !options.hold.used) {
      options.hold.used = true;
      options.hold.startedResolve();
      await options.hold.ready;
      if (options.hold.reject) await route.abort("failed");
      else await json(options.hold.body);
      options.hold.completedResolve();
      return;
    }
    if (Object.hasOwn(options.apiBodies || {}, url.pathname)) return json(options.apiBodies[url.pathname]);
    if (url.pathname === "/api/admin/content/services") return json({ items: [] });
    if (url.pathname === "/api/admin/content/centers") return json({ items: [] });
    if (url.pathname === "/api/admin/content/doctors") return json({ items: doctors });
    if (url.pathname === "/api/admin/doctors/stats") return json({});
    if (url.pathname === "/data/especialidades.json") return json({ specialties: [] });
    if (url.pathname === "/data/clusters.json") return json({ clusters: [] });
    if (url.pathname === "/api/admin/content/preparations") return json({ items: [] });
    if (url.pathname === "/api/admin/content/knowledge") return json((options.apiBodies || {})[url.pathname] || { items: [] });
    if (url.pathname === "/api/admin/prompts") return json((options.apiBodies || {})[url.pathname] || { items: [] });
    if (url.pathname === "/api/admin/legal") return json((options.apiBodies || {})[url.pathname] || { items: [] });
    if (url.pathname === "/api/admin/intake/settings") {
      const body = req.postDataJSON(); calls.push(body);
      return json({ ok: true, ...body });
    }
    if (url.pathname === "/api/admin/settings") {
      if (req.method() !== "GET") calls.push(req.postDataJSON());
      return json(req.method() === "GET" ? integrationData : { ok: true, api_keys: integrationData.api_keys });
    }
    if (url.pathname === new URL(pathname, "http://admin.fixture").pathname) {
      const catalogScreen = screen === "servicios" || screen === "centros" || screen === "medicos";
      const controller = screen === "settings" ? "platform/admin-settings.mjs" : screen === "preparaciones" ? "platform/admin-preparations.mjs" : screen === "knowledge" ? "platform/admin-knowledge.mjs" : screen === "prompts" ? "platform/admin-prompts.mjs" : screen === "legal" ? "platform/admin-legal.mjs" : "platform/admin-content.mjs";
      // The native preparations controller requires its host section and
      // resolves every dependency through window.SANA_STATIC.
      const controllerTag = screen === "settings"
        ? `<script type="module">import("/${controller}").then(m=>m.mount(document.getElementById("sec-settings")).ready)</script>`
        : screen === "preparaciones"
          ? `<script type="module">import("/${controller}").then(m=>{window.__editorialController=m;return m.mount(document.getElementById("sec-preparaciones"))})</script>`
          : catalogScreen
            // The catalog editor owns the whole document, mounted exactly as
            // templates/admin/content.html mounts it (import -> mount(document)
            // -> ready). The disposer's readiness stays observable: a rejected
            // dependency import rejects the chain and lands in this fixture's
            // pageerror collector instead of degrading silently.
            ? `<script type="module">import("/${controller}").then(m=>m.mount(document).ready)</script>`
          : `<script type="module">import("/${controller}").then(m=>{window.__editorialController=m;return m.mount()})</script>`;
      return route.fulfill({ contentType: "text/html; charset=utf-8", body: `<!doctype html><html><head><meta charset="utf-8"><script>
        window.SANA_ADMIN_SCREEN=${JSON.stringify(screen)};window.CURRENT_ROLE="admin";window.SANA_COUNTRY_PHONE=${JSON.stringify(phone)};        window.SANA_STATIC=${JSON.stringify(modules)};window.sanaImportStatic=n=>window.SANA_STATIC[n]?import(window.SANA_STATIC[n]):Promise.reject(new Error("Missing static manifest key: "+n));window.toast=()=>{};window.confirm=()=>true;
        window.__toasts=[];window.toast=message=>window.__toasts.push(message);
      </script></head><body><h1 id="adm-h1"></h1><p id="adm-sub"></p><nav id="adm-nav"></nav><main class="adm-content-wrap"><section id="sec-${screen}"></section></main>${controllerTag}</body></html>` });
    }
    if (url.pathname === "/platform/i18n.mjs") return route.fulfill({ contentType: "text/javascript", body: js("static/platform/i18n.mjs") });
    if (url.pathname === "/platform/search.mjs") return route.fulfill({ contentType: "text/javascript", body: js("static/platform/search.mjs") });
    if (url.pathname === "/platform/admin-hours-editor.mjs") return route.fulfill({ contentType: "text/javascript", body: js("static/platform/admin-hours-editor.mjs") });
    if (url.pathname.startsWith("/platform/") && url.pathname.endsWith(".mjs")) {
      return route.fulfill({ contentType: "text/javascript", body: js("static" + url.pathname) });
    }
    if (url.pathname.startsWith("/api/admin/preferences")) return json({ items: {} });
    if (/^\/[a-z0-9-]+\.(mjs|js)$/.test(url.pathname)) return route.fulfill({ contentType: "text/javascript", body: js(url.pathname.slice(1)) });
    return route.fulfill({ status: 404, body: "unexpected synthetic request: " + url.pathname });
  });
  await page.goto("http://admin.fixture" + pathname);
  try {
    await holdsFor(() => {
      if (errors.length) throw new Error(`controller error (${screen}): ${errors.join(" | ")}`);
    }, { during: 50, every: 10 });
  } catch (error) { await page.close(); throw error; }
  return { page, errors, calls, requestCounts, close: () => page.close() };
}

/** A held API response: `started` when the route got it, `ready` gates it, `completed` when answered. */
function heldRequest(cleanup, { path, body, reject = false }) {
  const gate = holdGate(cleanup);
  let completedResolve;
  const completed = new Promise((resolve) => { completedResolve = resolve; });
  return {
    path, body, reject,
    ready: gate.held, started: gate.arrived, startedResolve: gate.arrive,
    completed, completedResolve, release: gate.release,
  };
}

test("synthetic admin: empty services, preparations, and wiki open their production forms", async ({ newContext }) => {
  for (const [screen, path, selector] of [["servicios", "/admin/services", "#cnt-form"], ["preparaciones", "/admin/preparations", "#pa-nombre"], ["knowledge", "/admin/knowledge", "#kn-form [name=content]"]]) {
    const h = await fixture(await newContext(), screen, path);
    try {
      try {
        await expect(h.page.locator(selector)).toBeVisible(WAIT);
      } catch (error) { throw new Error(screen + ": " + await h.page.locator("body").innerText() + "\n" + h.errors.join("|") + "\n" + error.message); }
      if (screen === "servicios") await expect(h.page.locator("#sec-servicios")).toHaveAttribute("data-admin-empty-editable", "true");
      expect(h.errors).toEqual([]);
    } finally { await h.close(); }
  }
});

test("synthetic knowledge: destroy/remount restores one live native controller", async ({ context }) => {
  const h = await fixture(context, "knowledge", "/admin/knowledge");
  await expect(h.page.locator("#kn-form")).toBeVisible(WAIT);
  await h.page.evaluate(() => window.__editorialController.destroy());
  expect(await h.page.locator("#sec-knowledge").getAttribute("data-ready")).toBeNull();
  await h.page.evaluate(() => window.__editorialController.mount());
  await expect(h.page.locator("#kn-form")).toBeVisible(WAIT);
  expect(h.errors).toEqual([]);
});

test("synthetic editorial controllers isolate old success and rejection after remount", async ({ newContext, cleanup }) => {
  const cases = [
    ["prompts", "/admin/prompts", "/api/admin/prompts"],
    ["legal", "/admin/legal", "/api/admin/legal"],
    ["knowledge", "/admin/knowledge", "/api/admin/content/knowledge"],
  ];
  for (const [screen, path, apiPath] of cases) {
    for (const reject of [false, true]) {
      const title = `Fresh ${screen}`;
      const body = screen === "prompts"
        ? { items: [{ clave: "fresh", titulo: title, que_hace: "Fresh behavior", donde: "Fresh place" }] }
        : screen === "legal"
          ? { items: [{ id: "fresh", titulo: title, pagina: "/legal", editado: false, cuerpo: "Fresh" }] }
          : { items: [{ id: "fresh", type: "articulo", title, summary: "Fresh", content: "Fresh", tags: [], status: "publicado" }] };
      const hold = heldRequest(cleanup, {
        path: apiPath, body: JSON.parse(JSON.stringify(body).replaceAll("Fresh", "Stale").replaceAll("fresh", "stale")), reject,
      });
      const h = await fixture(await newContext(), screen, path, { hold, apiBodies: { [apiPath]: body } });
      try {
        await expect.poll(() => h.page.evaluate(() => !!window.__editorialController && document.readyState === "complete"), WAIT).toBe(true);
        await hold.started;
        expect(hold.used, `${screen} issued its production request`).toBe(true);
        await h.page.evaluate(() => window.__editorialController.destroy());
        await h.page.evaluate(() => window.__editorialController.mount());
        await expect.poll(() => h.page.evaluate(([screenName, expected]) => {
          const root = document.querySelector(`#sec-${screenName}`);
          return !!root && root.textContent.includes(expected) && !root.textContent.includes("Cargando…");
        }, [screen, title]), WAIT).toBe(true);
        expect(h.requestCounts[apiPath], `${screen} uses exactly one fresh request after remount`).toBe(2);
        const fresh = await h.page.locator(`#sec-${screen}`).innerText();
        const settled = h.page.waitForEvent(reject ? "requestfailed" : "requestfinished", (request) => new URL(request.url()).pathname === apiPath);
        hold.release();
        await hold.completed;
        await settled;
        await h.page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        expect(await h.page.locator(`#sec-${screen}`).innerText(),
          `${screen} ignores an old ${reject ? "rejection" : "success"} after remount`).toBe(fresh);
        expect(h.errors, `${screen} remount has no duplicate lifecycle error`).toEqual([]);
      } finally { await h.close(); }
    }
  }
});

test("synthetic editorial: remount preserves unsaved prompt languages", async ({ context }) => {
  const h = await fixture(context, "prompts", "/admin/prompts?prompt=draft", { apiBodies: {
    "/api/admin/prompts": { items: [{ clave: "draft", titulo: "Draft", texto_en: "Saved English", texto_es: "Guardado", alineado: true }] },
  } });
  await h.page.locator("#pr-es").fill("Unsaved Spanish");
  await h.page.locator('[data-tab="en"]').click();
  await h.page.locator("#pr-en").fill("Unsaved English");
  await h.page.evaluate(async () => { window.__editorialController.destroy(); await window.__editorialController.mount(); });
  await expect(h.page.locator("#pr-en")).toBeVisible(WAIT);
  expect(await h.page.locator("#pr-en").inputValue()).toBe("Unsaved English");
  expect(await h.page.locator("#pr-es").inputValue()).toBe("Unsaved Spanish");
  expect(await h.page.locator('[data-tab="en"]').getAttribute("aria-selected")).toBe("true");
  expect(h.errors).toEqual([]);
});

test("synthetic editorial: remount preserves unsaved legal draft", async ({ context }) => {
  const legal = await fixture(context, "legal", "/admin/legal?doc=draft", { apiBodies: {
    "/api/admin/legal": { items: [{ id: "draft", titulo: "Draft", pagina: "/terms", cuerpo: "Saved", editado: false }] },
  } });
  await legal.page.locator("#lg-cuerpo").fill("Unsaved legal draft");
  await legal.page.evaluate(async () => { window.__editorialController.destroy(); await window.__editorialController.mount(); });
  await expect(legal.page.locator("#lg-cuerpo")).toBeVisible(WAIT);
  expect(await legal.page.locator("#lg-cuerpo").inputValue()).toBe("Unsaved legal draft");
  expect(legal.errors).toEqual([]);
});

test("synthetic prompts: remount replaces pending translation without an old response overwriting it", async ({ context, cleanup }) => {
  const api = "/api/admin/prompts/draft/traduccion";
  const item = { clave: "draft", titulo: "Draft", texto_en: "Saved English", texto_es: "", alineado: false };
  const hold = heldRequest(cleanup, { path: api, body: { ...item, texto_es: "Stale translation", alineado: true } });
  const h = await fixture(context, "prompts", "/admin/prompts?prompt=draft", { hold, apiBodies: {
    "/api/admin/prompts": { items: [item] },
    [api]: { ...item, texto_es: "Fresh translation", alineado: true },
  } });
  await hold.started;
  await h.page.locator('[data-tab="en"]').click();
  await h.page.locator("#pr-en").fill("Unsaved English");
  await h.page.evaluate(async () => { window.__editorialController.destroy(); await window.__editorialController.mount(); });
  await expect(h.page.locator("#pr-es")).toHaveValue("Fresh translation", WAIT);
  expect(h.requestCounts[api]).toBe(2);
  expect(await h.page.locator("#pr-es").evaluate((el) => el.readOnly)).toBe(false);
  const settled = h.page.waitForEvent("requestfinished", (request) => new URL(request.url()).pathname === api);
  hold.release();
  await hold.completed;
  await settled;
  await h.page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect(await h.page.locator("#pr-es").inputValue()).toBe("Fresh translation");
  expect(await h.page.locator("#pr-en").inputValue()).toBe("Unsaved English");
  expect(h.errors).toEqual([]);
});

test("synthetic admin: saving one integration card leaves a sibling draft intact", async ({ context }) => {
  const h = await fixture(context, "settings", "/admin/settings?view=integrations");
  const second = h.page.locator('[data-integracion-card="second"] [data-key="SECOND"]');
  await second.fill("keep this draft");
  await h.page.locator('[data-integracion-card="first"] .set-guardar').click();
  await expect(h.page.locator('[data-msg="api_keys-first"]')).toContainText("Guardado", WAIT);
  expect(h.calls.length).toBe(1);
  expect(await second.inputValue()).toBe("keep this draft");
  expect(h.errors).toEqual([]);
});

test("synthetic admin: doctor Shift selection remains anchored to the visible list", async ({ context }) => {
  const h = await fixture(context, "medicos", "/admin/doctors");
  const boxes = h.page.locator("[data-med-select]");
  await expect(boxes.first()).toBeVisible(WAIT);
  await boxes.nth(0).click(); await boxes.nth(2).click({ modifiers: ["Shift"] });
  expect(await boxes.evaluateAll((nodes) => nodes.map((node) => node.checked))).toEqual([true, true, true]);
  expect(await h.page.locator("#med-bulk").innerText()).toMatch(/3/);
  await h.page.locator("#med-bulk-action").selectOption("enable-intake");
  await h.page.locator("[data-med-bulk-apply]").click();
  await expect.poll(() => h.page.evaluate(() => window.__toasts.includes("3 completados.")), WAIT).toBe(true);
  expect(h.calls).toEqual([{ medico_ids: ["doctor-0", "doctor-1", "doctor-2"], enabled: true }]);
  expect(h.errors).toEqual([]);
});
