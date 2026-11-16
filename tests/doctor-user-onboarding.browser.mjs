// Deterministic rendered QA of the actual component. No live accounts or emails.
// Run: node tests/doctor-user-onboarding.browser.mjs
import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const out = process.env.DOCTOR_USER_QA_DIR;
if (out) fs.mkdirSync(out, { recursive: true });
const read = file => fs.readFileSync(path.join(root, file), "utf8");
const contentSource = read("static/platform/admin-content.mjs");
const formStyleStart = contentSource.indexOf("  function injectContentFormStyles()");
const formStyleEnd = contentSource.indexOf("    var tag = document.createElement(\"style\");", formStyleStart);
const formCss = vm.runInNewContext(contentSource.slice(formStyleStart, formStyleEnd) + "return css; } injectContentFormStyles();");
const css = ["tokens.css", "human-rounds.css", "admin-gate.css", "admin-shell.css", "admin-direct.css"]
  .map(name => read("static/root/" + name)).join("\n") + formCss;
// Canonical Spanish pages intentionally omit the catalog: exercise real fallback
// rendering rather than masking untranslated fallbacks with a fixture dictionary.
const translations = {};
const countryPhone = JSON.parse(read("packs/ar/country.json")).phone;
const doctor = { id: "diego-arturo-rivera", nombre: "Diego Arturo Rivera" };
let items = [
  { usuario: "owner", es_dueno: true, rol: "admin" },
  { usuario: "diego.rivera", rol: "admin", activo: true },
  { usuario: "rosa.rivero", rol: "admin", activo: true },
  { usuario: "other.doctor", rol: "medico", medico_id: "other" },
];
let failure = false, requests = [];
const html = `<!doctype html><html lang="es" data-theme="pinamar"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}</style></head>
<body><div class="adm5-shell"><aside class="adm5-side"><a class="adm5-brand" href="/">Pinamar Turnos</a><details class="adm5-menu"><summary>Menú</summary><nav class="adm5-nav"><a href="/admin/requests">Solicitudes</a><a class="on" href="/admin/doctors">Médicos</a><a href="/admin/users">Usuarios</a></nav></details></aside>
<main class="adm5-main"><header class="adm-topbar"><div class="adm-h1row"><h1 class="adm-h1">Médicos / Diego Arturo Rivera</h1></div></header><div class="adm-content-wrap"><section><form id="cnt-form" class="cnt-form"><div role="tablist" class="cnt-tabs"><button type="button" class="cnt-tab">Horarios de atención</button><button type="button" class="cnt-tab">Calendario</button><button type="button" class="cnt-tab">Información básica</button><button type="button" class="cnt-tab">Síntomas</button><button type="button" class="cnt-tab on">Usuario</button></div><div id="med-user-panel"></div></form></section></div></main></div></body></html>`;
const browser = await chromium.launch();
try {
  const context = await browser.newContext({ permissions: ["clipboard-read", "clipboard-write"], viewport: { width: 1440, height: 900 } });
  context.setDefaultTimeout(8000);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("https://doctor-fixture.test/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/admin/users") return route.fulfill({ json: { items } });
    if (url.pathname === "/api/admin/preferences") return route.fulfill({ json: { items: {} } });
    if (url.pathname.startsWith("/api/admin/users/actions/")) {
      const body = route.request().postDataJSON();
      requests.push({ action: url.pathname.split("/").at(-1), ...body });
      // Gives the browser a real in-flight interval to exercise double-submit.
      await new Promise(resolve => setTimeout(resolve, 80));
      if (failure) return route.fulfill({ status: 409, json: { error: "Ese usuario ya existe." } });
      if (url.pathname.endsWith("/link")) {
        // Linked as an account already in use, so the summary carries its
        // whole action row (password, 2FA, calendar address).
        items = items.map(item => item.usuario === body.usuario ? { ...item, rol: "medico", medico_id: doctor.id, cuenta_estado: "perfil_completo" } : item);
        return route.fulfill({ json: { ok: true, resultados: [{ ok: true, usuario: body.usuario }] } });
      }
      items.push({ usuario: body.usuario, rol: "medico", medico_id: doctor.id });
      return route.fulfill({ json: { ok: true, resultados: [{ ok: true, usuario: body.usuario, password: "fixture-access" }] } });
    }
    const files = {
      "/static/platform/admin-users.mjs": "static/platform/admin-users.mjs",
      "/static/platform/dates.mjs": "static/platform/dates.mjs",
      "/static/platform/format.mjs": "static/platform/format.mjs",
      "/static/platform/admin-preferences.mjs": "static/platform/admin-preferences.mjs",
      "/static/platform/admin-session.mjs": "static/platform/admin-session.mjs",
      "/static/platform/admin-table.mjs": "static/platform/admin-table.mjs",
      "/static/platform/i18n.mjs": "static/platform/i18n.mjs",
      "/static/platform/admin-filter-controls.mjs": "static/platform/admin-filter-controls.mjs",
    };
    if (files[url.pathname]) {
      return route.fulfill({ contentType: "text/javascript; charset=utf-8", body: read(files[url.pathname]) });
    }
    if (url.pathname === "/") return route.fulfill({ contentType: "text/html", body: html });
    return route.fulfill({ status: 404, body: "" });
  });
  async function render() {
    await page.goto("https://doctor-fixture.test/");
    await page.evaluate(({ countryPhone }) => {
      window.SANA_ADMIN_SCREEN = "medicos";
      window.SANA_COUNTRY_PHONE = countryPhone;
      window.SANA_STATIC = {
        "platform/dates.mjs": "/static/platform/dates.mjs",
        "platform/format.mjs": "/static/platform/format.mjs",
        "platform/admin-preferences.mjs": "/static/platform/admin-preferences.mjs",
        "platform/admin-session.mjs": "/static/platform/admin-session.mjs",
        "platform/admin-table.mjs": "/static/platform/admin-table.mjs",
        "platform/i18n.mjs": "/static/platform/i18n.mjs",
        "platform/admin-filter-controls.mjs": "/static/platform/admin-filter-controls.mjs",
        "platform/admin-users.mjs": "/static/platform/admin-users.mjs",
      };
      window.sanaImportStatic = name => import(window.SANA_STATIC[name]);
      window.outerSubmissions = 0;
      document.querySelector("#cnt-form").addEventListener("submit", event => { event.preventDefault(); window.outerSubmissions++; });
    }, { countryPhone });
    await page.evaluate(async doctor => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      await Users.renderDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
    }, doctor);
  }
  await render();
  assert.equal(await page.inputValue("#doctor-user-existing"), "diego.rivera");
  assert.equal(await page.inputValue("#doctor-user-new"), "diego.rivera2");
  assert.equal(await page.locator("#doctor-user-link-form").count(), 1);
  assert.equal(await page.locator("#doctor-user-create-form").count(), 1);
  assert.equal(await page.locator("#med-user-panel form").count(), 0);
  // Linking an existing user is a legacy repair, folded behind «Reparar vínculo».
  assert.equal(await page.locator("#doctor-user-link-form").isVisible(), false);
  await page.click("#doctor-user-repair");
  assert.deepEqual(await page.locator("#doctor-user-existing option").evaluateAll(options => options.map(option => option.value)), ["", "diego.rivera", "rosa.rivero"]);
  for (const width of [360, 390, 768, 834, 1024, 1280, 1440]) {
    await page.setViewportSize({ width, height: width < 500 ? 844 : 900 });
    const overflow = await page.evaluate(() => {
      const vw = document.documentElement.clientWidth;
      return [...document.querySelectorAll("body *")].filter(el => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.right > vw + 1;
      }).map(el => el.className);
    });
    assert.deepEqual(overflow, [], `overflow at ${width}`);
    const createFields = await page.locator('#doctor-user-create-form .cnt-in').evaluateAll(fields => fields.map(field => {
      const rect = field.getBoundingClientRect();
      const container = field.closest('.cnt-field').getBoundingClientRect();
      return { left: rect.left, right: rect.right, width: rect.width, containerLeft: container.left, containerRight: container.right };
    }));
    assert.equal(createFields.length, 2); // email is the record's, read-only here
    for (const field of createFields) {
      assert.ok(field.left >= field.containerLeft - 1 && field.right <= field.containerRight + 1, `field contained at ${width}`);
      assert.ok(Math.abs(field.left - createFields[0].left) <= 1, `aligned creation fields at ${width}`);
      assert.ok(Math.abs(field.width - createFields[0].width) <= 1, `matching creation field widths at ${width}`);
    }
    const controls = await page.locator('.doctor-user-link-form').evaluateAll(groups => groups.map(group => {
      const button = group.querySelector('button').getBoundingClientRect();
      const field = group.querySelector('input,select').getBoundingClientRect();
      return { buttonWidth: button.width, buttonHeight: button.height, buttonLeft: button.left, fieldWidth: field.width, fieldLeft: field.left };
    }));
    assert.ok(Math.abs(controls[0].fieldWidth - controls[1].fieldWidth) <= 1, `aligned field widths at ${width}`);
    for (const control of controls) {
      assert.ok(control.buttonWidth < control.fieldWidth, `natural button width at ${width}`);
      assert.ok(control.buttonHeight >= 44, `tap target at ${width}`);
      if (width < 500) assert.ok(Math.abs(control.buttonLeft - control.fieldLeft) <= 1, `left aligned mobile action at ${width}`);
    }
    if (out && [390, 1440].includes(width)) {
      await page.screenshot({ path: path.join(out, `doctor-access-${width}.png`), fullPage: true });
      fs.writeFileSync(path.join(out, `doctor-access-${width}.html`), await page.locator("#med-user-panel").evaluate(el => el.outerHTML));
    }
  }
  await page.click('#doctor-user-link-form button');
  await page.waitForSelector(".doctor-user-summary");
  // The account's actions are one group: one row on a wide screen, one
  // column with one left edge on a phone — never a ragged wrap.
  const actionBoxes = () => page.locator('.doctor-user-actions button').evaluateAll(buttons => buttons.map(button => {
    const r = button.getBoundingClientRect();
    return { left: r.left, top: r.top, width: r.width };
  }));
  const wide = await actionBoxes();
  assert.ok(wide.length >= 3, `the in-use account shows its actions (${wide.length})`);
  assert.ok(wide.every(box => Math.abs(box.top - wide[0].top) <= 1), "one row at 1440");
  await page.setViewportSize({ width: 390, height: 844 });
  const narrow = await actionBoxes();
  assert.ok(narrow.every(box => box.width < 300), "natural-width buttons at 390");
  assert.ok(narrow.every(box => Math.abs(box.left - narrow[0].left) <= 1), "one left edge at 390");
  assert.ok(narrow.every((box, i) => i === 0 || box.top > narrow[i - 1].top), "one column at 390");
  assert.equal(requests.at(-1).usuario, "diego.rivera");
  items = items.filter(item => item.usuario !== "diego.rivera");
  await render();
  assert.equal(await page.inputValue("#doctor-user-existing"), "");
  assert.equal(await page.inputValue("#doctor-user-new"), "diego.rivera");
  await page.locator("#doctor-user-create-form button").evaluate(button => { button.click(); button.click(); });
  await page.waitForSelector("#doctor-user-password");
  assert.ok(await page.locator('#doctor-user-copy').evaluate(button => button.getBoundingClientRect().width < 200));
  await page.waitForFunction(() => document.querySelector("#doctor-user-copy-status").textContent.includes("copiados"));
  assert.equal(requests.filter(r => r.action === "create").length, 1);
  // No email in «Información básica»: none travels (one email per doctor).
  assert.equal(requests.find(r => r.action === 'create').email, '');
  assert.equal(await page.evaluate(() => window.outerSubmissions), 0);
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), "Usuario: diego.rivera\nContraseña: fixture-access");
  if (out) await page.screenshot({ path: path.join(out, "doctor-access-created.png"), fullPage: true });
  items = items.filter(item => item.usuario !== "diego.rivera");
  await render();
  await page.evaluate(() => { navigator.clipboard.write = () => Promise.reject(new Error("denied")); });
  await page.locator('#doctor-user-new').press('Enter');
  await page.waitForSelector("#doctor-user-password");
  assert.equal(await page.inputValue("#doctor-user-password"), "fixture-access");
  await page.click("#doctor-user-copy");
  await page.waitForFunction(() => document.querySelector("#doctor-user-copy-status").textContent.includes("bloqueado"));
  assert.equal(requests.filter(r => r.action === "create").length, 2, "copy retry must not recreate account");
  assert.equal(await page.evaluate(() => window.outerSubmissions), 0);
  items = items.filter(item => item.usuario !== "diego.rivera");
  failure = true;
  await render();
  await page.click('#doctor-user-create-form button');
  await page.waitForSelector("#doctor-user-create-error:not([hidden])");
  assert.equal(await page.inputValue("#doctor-user-new"), "diego.rivera");
  assert.equal(await page.locator('#doctor-user-create-form button').isEnabled(), true);
  assert.equal(await page.locator('#doctor-user-link-form button').isEnabled(), true);
  assert.deepEqual(errors, []);

  // The real doctor-screen consumer must not leave its Usuario tab loading
  // forever when its fingerprinted Users import rejects.  This mounts the
  // production platform/admin-content.mjs through the same owner bootstrap
  // templates/admin/content.html uses (import -> mount(document) -> ready),
  // rather than calling renderDoctorUser itself.
  const consumer = await context.newPage();
  console.log("Checking actual doctor-screen consumer recovery");
  const consumerErrors = [];
  consumer.on("pageerror", error => consumerErrors.push(error.message));
  let usersMode = "reject";
  const consumerDoc = { id: doctor.id, nombre: doctor.nombre, especialidad: "Clínica médica", centros: [{ centro: "hospital", franjas: [], excepciones: [] }], activo: true };
  const consumerHtml = `<!doctype html><html><body><nav id="adm-nav"></nav><h1 id="adm-h1"></h1><p id="adm-sub"></p><div id="sec-medicos"></div><p id="content-status"></p><script>window.SANA_ADMIN_SCREEN="medicos";window.CURRENT_ROLE="admin";window.SANA_COUNTRY_PHONE=${JSON.stringify(countryPhone)};window.SANA_STATIC=${JSON.stringify({ "platform/hours.mjs": "/static/platform/hours.mjs", "platform/dates.mjs": "/static/platform/dates.mjs", "platform/format.mjs": "/static/platform/format.mjs", "platform/admin-combo.mjs": "/static/platform/admin-combo.mjs", "platform/admin-preferences.mjs": "/static/platform/admin-preferences.mjs", "platform/admin-session.mjs": "/static/platform/admin-session.mjs", "platform/admin-table.mjs": "/static/platform/admin-table.mjs", "platform/i18n.mjs": "/static/platform/i18n.mjs", "platform/search.mjs": "/static/platform/search.mjs", "platform/admin-hours-editor.mjs": "/static/platform/admin-hours-editor.mjs", "platform/admin-filter-controls.mjs": "/static/platform/admin-filter-controls.mjs", "platform/admin-users.mjs": "/static/platform/admin-users.mjs", "platform/islands.mjs": "/static/platform/islands.mjs", "platform/calendar-grid.mjs": "/static/platform/calendar-grid.mjs" })};window.sanaImportStatic=n=>import(window.SANA_STATIC[n]);window.SANA_ISLAND_URLS={};</script><script type="module">import("/static/platform/admin-content.mjs").then(m=>m.mount(document).ready)</script></body></html>`;
  await consumer.route("https://doctor-consumer.test/**", route => {
    const url = new URL(route.request().url()), pathname = url.pathname;
    const consumerFiles = { "/static/platform/admin-content.mjs": "static/platform/admin-content.mjs", "/static/platform/hours.mjs": "static/platform/hours.mjs", "/static/platform/dates.mjs": "static/platform/dates.mjs", "/static/platform/format.mjs": "static/platform/format.mjs", "/static/platform/admin-combo.mjs": "static/platform/admin-combo.mjs", "/static/platform/admin-preferences.mjs": "static/platform/admin-preferences.mjs", "/static/platform/admin-session.mjs": "static/platform/admin-session.mjs", "/static/platform/admin-table.mjs": "static/platform/admin-table.mjs", "/static/platform/i18n.mjs": "static/platform/i18n.mjs", "/static/platform/search.mjs": "static/platform/search.mjs", "/static/platform/admin-hours-editor.mjs": "static/platform/admin-hours-editor.mjs", "/static/platform/admin-filter-controls.mjs": "static/platform/admin-filter-controls.mjs", "/static/platform/admin-users.mjs": "static/platform/admin-users.mjs", "/static/platform/islands.mjs": "static/platform/islands.mjs", "/static/platform/calendar-grid.mjs": "static/platform/calendar-grid.mjs" };
    if (pathname === "/admin/doctors") return route.fulfill({ contentType: "text/html", body: consumerHtml });
    if (pathname === "/static/platform/admin-users.mjs" && usersMode === "reject") return route.abort("failed");
    if (consumerFiles[pathname]) return route.fulfill({ contentType: "text/javascript; charset=utf-8", body: read(consumerFiles[pathname]) });
    if (pathname === "/api/i18n/es.json") return route.fulfill({ json: {} });
    if (pathname === "/api/admin/preferences") return route.fulfill({ json: { items: {} } });
    if (pathname === "/api/admin/users") return route.fulfill({ json: { items } });
    if (pathname === "/api/admin/content/doctors") return route.fulfill({ json: { items: [consumerDoc] } });
    if (pathname === "/api/admin/content/centers") return route.fulfill({ json: { items: [{ slug: "hospital", name: "Hospital" }] } });
    if (pathname.includes("compatible-services") || pathname.includes("/intake/") || pathname.includes("/stats") || pathname.includes("special") || pathname.includes("cluster")) return route.fulfill({ json: { items: [] } });
    return route.fulfill({ json: { items: [] } });
  });
  await consumer.goto("https://doctor-consumer.test/admin/doctors?item=diego-arturo-rivera");
  await consumer.locator('[data-tab="usuario"]').click();
  assert.equal(consumerErrors.length, 0, "admin-content consumer runtime errors: " + consumerErrors.join(" | "));
  await consumer.locator('#med-user-panel [role="alert"]').waitFor({ state: "visible" });
  assert.equal(await consumer.locator("#med-user-panel [role=alert]").count(), 1,
    "consumer did not render recovery: " + await consumer.locator("body").innerText());
  assert.match(await consumer.locator("#med-user-panel").innerText(), /No se pudo cargar el acceso del médico/);
  assert.equal(await consumer.locator("#cnt-form").count(), 1, "failed user import must not remove doctor form");
  usersMode = "success";
  // The module's owned recovery reloads the whole page (location.reload(),
  // not an in-place retry).  Mark the departing document and wait for the
  // fresh one before re-entering the Usuario tab, so the tab click can never
  // land on the pre-reload DOM.
  await consumer.evaluate(() => { window.__preUserReload = true; });
  await consumer.locator("[data-med-user-reload]").click();
  await consumer.waitForFunction(() => !window.__preUserReload && document.readyState === "complete");
  await consumer.locator('[data-tab="usuario"]').click();
  await consumer.locator("#doctor-user-new").waitFor({ timeout: 5000 });
  assert.equal(await consumer.locator("#cnt-form").count(), 1);
  await consumer.close();
  console.log("PASS: matching, seven widths, linking, one-click create + clipboard, denied clipboard recovery, duplicate submit, API failure");
} finally {
  await browser.close();
}
