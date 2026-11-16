// Real browser events against the production controller and shared styles.
// All requests are fulfilled locally: this suite cannot change clinic data.
//
// Differences from the node:test original (tests/e2e/intake-controls.e2e.mjs):
//   - The browser comes from the runner: the original launched a Chromium per test with
//     chromium.launch() and closed it in a finally; each test now takes the runner's `page`
//     (1440x900 through test.use({ viewport })). The scenario is still hermetic: every request of the
//     page is fulfilled by the test at http://intake.test/. The file is `hermetic`: no app or
//     database is started (appOptions only names the file for tests/e2e-spec-isolation.test.mjs).
//   - The held module (holdModule) is a plain promise released by the test, as in the original;
//     it is released in a finally, so a failing test never leaves a route hanging.
//   - settle(), the original's waitForFunction(no disabled checkbox), is
//     expect([data-effective-medico]:disabled).toHaveCount(0). The checkbox/count/href reads that are
//     positive final-state claims are web-first (toBeChecked, toHaveCount, toHaveAttribute); the
//     negative ones (unchecked after the rollback, still enabled, no row for a search with no match),
//     the "this is absent from the table" counts and the request-call lists stay point-in-time reads.
//   - holdsFor: "Storybook preview ... never POSTs" read the call list once; a POST would arrive a moment
//     after the click, so the empty list is checked continuously for 300 ms after the last click.
//   - The optional ADMIN_UI_ARTIFACT_DIR evidence (screenshot + HTML per width) is kept; the screenshot also
//     goes through saveShot (attached to the report).
//   - Locators: #ent-*, [data-effective-medico], [data-ent-*], tbody/thead structure stay CSS: they are
//     the panel's markup contract and several checks count rows/columns on purpose.
import { existsSync, readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test, expect, holdsFor, saveShot } from "./lib/fixtures.mjs";
import { platformDeps } from "./lib/static-deps.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_intake_controls", hermetic: true },
  viewport: { width: 1440, height: 900 },
});

const root = process.env.INTAKE_SOURCE_ROOT || fileURLToPath(new URL("../../", import.meta.url));
// a URL's file on disk: a root web asset sits in static/root/, a module asset inside the code package
const diskOf = (urlPath) => [urlPath.slice(1), "static/root" + urlPath, "app" + urlPath]
  .map((rel) => root + rel).find((file) => existsSync(file)) || root + urlPath.slice(1);
const names = ["Ana Alvarez", "Beatriz Benitez", "Carla Campos", "Dora Diaz", "Elena Estevez", "Florencia Flores"];
const seed = () => ({ enabled_system: true, medicos: names.map((nombre, i) => ({
  id: `doctor-${i}`, nombre, especialidad: i < 4 ? "Pediatría" : "Cardiología",
  efectivo: i === 5, override: i === 5 ? true : false,
  cartel_url: `/api/admin/intake/door/doctor-${i}`,
  pdf_url: i === 5 ? `/api/admin/intake/door/doctor-${i}?format=pdf` : null,
})) });

const MODULE_PATH = "/modules/intake/admin-entrevistas.mjs";
// The platform modules the controller imports, read from its source (lib/static-deps.mjs),
// so a new dependency is served without editing this whitelist.
const MODULE_DEPS = platformDeps(root, [diskOf(MODULE_PATH).slice(root.length)]).map((name) => "/static/" + name);

async function harness(page, { failLoad = false, holdModule = false, rejectModule = false } = {}) {
  const calls = [], errors = [];
  let rejectNext = false;
  let loadFailure = failLoad;
  let releaseModule = () => {};
  const moduleHeld = holdModule ? new Promise((resolve) => { releaseModule = resolve; }) : Promise.resolve();
  const data = seed();
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname === "/api/admin/intake/settings") {
      if (request.method() === "POST") {
        const body = request.postDataJSON();
        calls.push(body);
        if (rejectNext) {
          rejectNext = false;
          return route.fulfill({ status: 500, json: { error: "Synthetic save failure" } });
        }
        for (const d of data.medicos) if (body.medico_ids.includes(d.id)) {
          d.efectivo = body.enabled;
          d.pdf_url = body.enabled ? d.cartel_url + "?format=pdf" : null;
        }
        return route.fulfill({ json: { ok: true, medico_ids: body.medico_ids, enabled: body.enabled } });
      }
      if (loadFailure) { loadFailure = false; return route.fulfill({ status: 503, json: { error: "Synthetic loading failure" } }); }
      return route.fulfill({ json: data });
    }
    if (url.pathname === "/") {
      const country = JSON.parse(readFileSync(root + "packs/ar/country.json", "utf8"));
      return route.fulfill({ contentType: "text/html; charset=utf-8", body: `<!doctype html><html lang="es" data-theme="pinamar"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <link rel="stylesheet" href="/human-rounds.css"><link rel="stylesheet" href="/admin-gate.css">
        <link rel="stylesheet" href="/admin-shell.css"><link rel="stylesheet" href="/admin-direct.css">
        <script>window.SANA_COUNTRY_PHONE=${JSON.stringify(country.phone)};window.sanaImportStatic=(name)=>import(name.indexOf("/")>=0?"/static/"+name:"/"+name);</script>
        </head><body><main class="adm5-main"><header class="adm-topbar"><h1 class="adm-h1" id="adm-h1">Captura de síntomas</h1></header>
        <div class="adm-content-wrap"><section id="sec-entrevistas" data-load-error="No se pudo cargar." data-retry="Retry"></section></div></main>
        <script type="module" src="/static/platform/admin-control-fit.mjs"></script>
        <script type="module">
          (function () {
            var host = document.getElementById("sec-entrevistas");
            var url = "${MODULE_PATH}";
            function showLoadError() {
              if (!host) return;
              host.innerHTML = '<p class="adm-empty">' + (host.getAttribute("data-load-error") || "") +
                '</p><button class="btn" type="button" data-ent-retry>' + (host.getAttribute("data-retry") || "") + "</button>";
              var retry = host.querySelector("[data-ent-retry]");
              if (retry) retry.addEventListener("click", function () { location.reload(); });
            }
            if (!url) { showLoadError(); return; }
            import(url).then(function (m) { return m.boot(); }).catch(showLoadError);
          })();
        </script></body></html>` });
    }
    if (url.pathname === MODULE_PATH) {
      if (rejectModule) return route.abort("failed");
      await moduleHeld;
    }
    const allowed = ["/tokens.css", "/human-rounds.css", "/admin-shell.css", "/admin-gate.css", "/admin-direct.css", "/static/platform/format.mjs", "/static/platform/admin-session.mjs", "/static/platform/admin-control-fit.mjs", "/static/platform/admin-preferences.mjs", "/static/platform/i18n.mjs", ...MODULE_DEPS, MODULE_PATH];
    if (allowed.includes(url.pathname)) return route.fulfill({
      contentType: url.pathname.endsWith("css") ? "text/css; charset=utf-8" : "text/javascript; charset=utf-8",
      body: readFileSync(diskOf(url.pathname), "utf8"),
    });
    return route.fulfill({ status: 404, body: "Not part of the synthetic fixture" });
  });
  const navigation = page.goto("http://intake.test/");
  if (!holdModule && !rejectModule) {
    await navigation;
    await expect(page.locator(failLoad ? "[data-ent-retry]" : "[data-effective-medico]").first()).toBeVisible();
  }
  const box = i => page.locator(`[data-effective-medico="doctor-${i}"]`);
  const settle = async () => {
    await expect(page.locator("[data-effective-medico]:disabled")).toHaveCount(0);
  };
  return { page, box, calls, errors, settle, navigation, releaseModule, reject: () => { rejectNext = true; } };
}

test("intake: real Shift range, filter scope, rollback, and responsive rows", async ({ page }, testInfo) => {
  const h = await harness(page);
  const { box, calls, settle } = h;
  expect(await page.locator("tbody select").count()).toBe(0);
  expect(await page.locator("tbody a").count()).toBe(1);
  expect(await page.locator("tbody tr").first().locator("a").count()).toBe(0);
  expect(await page.locator("thead th").count()).toBe(await page.locator("tbody tr").first().locator("td").count());
  await box(0).click(); await settle();
  await box(3).click({ modifiers: ["Shift"] }); await settle();
  expect(calls.at(-1)).toEqual({ medico_ids: ["doctor-0", "doctor-1", "doctor-2", "doctor-3"], enabled: true });
  for (let i = 0; i < 4; i++) await expect(box(i)).toBeChecked();
  await box(1).click({ modifiers: ["Shift"] }); await settle();
  expect(calls.at(-1)).toEqual({ medico_ids: ["doctor-1", "doctor-2", "doctor-3"], enabled: false });
  await expect(box(0)).toBeChecked();

  await page.locator("#ent-search").click();
  await page.locator("#ent-search").pressSequentially("Cardiología");
  await expect(page.locator("#ent-search")).toHaveValue("Cardiología");
  await expect(page.locator("[data-effective-medico]")).toHaveCount(2);
  await box(4).click({ modifiers: ["Shift"] }); await settle();
  expect(calls.at(-1)).toEqual({ medico_ids: ["doctor-4"], enabled: true });
  await page.locator("#ent-all").click(); await settle();
  expect(calls.at(-1).medico_ids).toEqual(["doctor-4", "doctor-5"]);
  expect(calls.at(-1).enabled).toBe(false);
  h.reject();
  await box(4).click(); await settle();
  expect(await box(4).isChecked()).toBe(false);
  await expect(page.getByText("Synthetic save failure", { exact: true })).toBeVisible();
  await box(4).click(); await settle();
  await expect(box(4)).toBeChecked();

  await page.locator("#ent-search").fill("");
  for (const width of [360, 390, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const measure = await page.evaluate(() => ({
      viewport: innerWidth, width: document.documentElement.scrollWidth,
      oversized: Array.from(document.querySelectorAll("main,section,table,col,.ent-table-wrap,.adm-content-wrap")).filter(e => e.getBoundingClientRect().right > innerWidth + 1).map(e => ({ tag:e.tagName, cls:e.className, style:e.getAttribute("style"), width:e.getBoundingClientRect().width })),
      labels: Array.from(document.querySelectorAll('tbody tr')).filter(row => !row.querySelector('input').checked)
        .map(row => row.querySelector('[data-col="cartel"]'))
        .filter(Boolean).map(cell => ({ width: cell.getBoundingClientRect().width, text: cell.innerText.trim() })),
    }));
    expect(measure.width <= measure.viewport + 1, `horizontal overflow at ${width}: ${JSON.stringify(measure)}`).toBe(true);
    expect(measure.labels.every(cell => !cell.text), `inactive PDF text at ${width}`).toBe(true);
    if (width <= 390) expect(measure.labels.every(cell => !cell.width), "inactive mobile PDF property should be absent").toBe(true);
    expect(await page.locator("#ent-all:visible,#ent-all-mobile:visible").count(), `bulk checkbox unavailable at ${width}`).toBe(1);
    if (process.env.ADMIN_UI_ARTIFACT_DIR) {
      mkdirSync(process.env.ADMIN_UI_ARTIFACT_DIR, { recursive: true });
      await saveShot(testInfo, page, `${process.env.ADMIN_UI_ARTIFACT_DIR}/synthetic-intake-${width}.png`, { fullPage: true });
      writeFileSync(`${process.env.ADMIN_UI_ARTIFACT_DIR}/synthetic-intake-${width}.html`, await page.content());
    }
  }
  expect(h.errors).toEqual([]);
});

test("intake: Storybook preview stays interactive and never POSTs", async ({ page }) => {
  const h = await harness(page);
  await h.page.evaluate(async data => {
    const { preview } = await import("/modules/intake/admin-entrevistas.mjs");
    await preview(document.querySelector("#sec-entrevistas"), data);
  }, seed());
  await h.box(0).click();
  expect(await h.box(0).isEnabled()).toBe(true);
  await h.box(3).click({ modifiers: ["Shift"] });
  await expect(h.box(1)).toBeChecked();
  await holdsFor(async () => { expect(h.calls.length).toBe(0); }, { during: 300 });
});

test("intake: recoverable empty/search/error states and phone bulk action", async ({ page }) => {
  const h = await harness(page, { failLoad: true });
  await h.page.locator("[data-ent-retry]").click();
  await expect(h.box(0)).toBeVisible();
  await h.page.locator("#ent-search").fill("no-doctor-matches-this");
  expect(await h.page.locator("[data-effective-medico]").count()).toBe(0);
  await h.page.locator("[data-ent-clear]").click();
  await expect(h.page.locator("[data-effective-medico]")).toHaveCount(6);
  await h.page.setViewportSize({ width: 360, height: 844 });
  await h.page.locator("#ent-all-mobile").click(); await h.settle();
  expect(h.calls.at(-1).medico_ids.length).toBe(6);
  expect(h.calls.at(-1).enabled).toBe(true);
  await h.page.evaluate(async () => {
    const { preview } = await import("/modules/intake/admin-entrevistas.mjs");
    await preview(document.querySelector("#sec-entrevistas"), {medicos: []});
  });
  // The empty state's one action is the shared create button (499e770b): it
  // carries its destination in data-href, the module's click handler goes there.
  await expect(h.page.locator("button[data-admin-create]")).toHaveAttribute("data-href", "/admin/doctors?item=new");
  expect(await h.page.locator("[data-ent-clear]").count()).toBe(0);
});

test("intake: delayed native module holds the empty host and paints after release", async ({ page }) => {
  const h = await harness(page, { holdModule: true });
  try {
    const going = h.navigation;
    await expect(h.page.locator("#sec-entrevistas")).toBeAttached();
    const whileHeld = await h.page.evaluate(() => ({
      html: document.getElementById("sec-entrevistas")?.innerHTML || "",
      legacy: typeof window.SanaEntrevistas,
      routeHook: typeof window.sanaRouteEntrevistas,
      path: location.pathname,
    }));
    expect(whileHeld.html).toBe("");
    expect(whileHeld.legacy).toBe("undefined");
    expect(whileHeld.routeHook).toBe("undefined");
    h.releaseModule();
    await going;
    await expect(h.box(0)).toBeVisible();
    expect(await h.page.evaluate(() => typeof window.SanaEntrevistas)).toBe("undefined");
    expect(h.errors).toEqual([]);
  } finally { h.releaseModule(); }
});

test("intake: rejected native module shows the existing retry recovery", async ({ page }) => {
  const h = await harness(page, { rejectModule: true });
  await h.navigation;
  await expect(h.page.locator("[data-ent-retry]")).toBeVisible();
  await expect(h.page.locator(".adm-empty")).toHaveText(/No se pudo cargar/, { useInnerText: true });
  expect(await h.page.evaluate(() => typeof window.SanaEntrevistas)).toBe("undefined");
  expect(await h.page.evaluate(() => typeof window.sanaRouteEntrevistas)).toBe("undefined");
});
