// Delayed/rejected import and synthetic-save proofs for the canonical desk
// overlay. The Requests and Doctors screens boot their real classic
// controllers; the Day Queue screen boots the REAL native module exactly the
// way templates/admin/cola.html does — document-owned stylesheet, fingerprinted
// import, mount(document) with awaited readiness — so every queue interaction
// below drives the module's own rendered master list, detail pane and
// free-slot actions, never a synthetic action node. Boundary HTTP is synthetic
// on purpose: this fixture proves controller behavior, not the ASGI/template
// surface.
import { test, ceil, hookOptions, within } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { hoyIsoInstalacion } from "../static/platform/dates.mjs";
import { stays, until } from "./helpers/wait.mjs";
import { stableBox } from "./helpers/box.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (file) => fs.readFileSync(path.join(ROOT, file), "utf8");
// Same URL shape core/static_fingerprint.py's url() serves: the first 8 hex
// chars of the asset's own bytes, inserted before the extension.
const fingerprintUrl = (file) => {
  const hash = createHash("sha256").update(fs.readFileSync(path.join(ROOT, file))).digest("hex").slice(0, 8);
  const dot = file.lastIndexOf(".");
  return "/" + file.slice(0, dot) + "." + hash + file.slice(dot);
};
const COUNTRY_PHONE = JSON.parse(read("packs/ar/country.json")).phone;
const ORIGIN = "https://create-appt-native.test";
const MODULE_PATH = "/static/platform/admin-create-appointment.aaaaaaaa.mjs";
const MODULE_RETRY_PATH = "/static/platform/admin-create-appointment.bbbbbbbb.mjs";
const DAY = "2026-09-08";
const HOUR = "08:30";
const DOCTOR_A = "Ana Pérez";
const DOCTOR_B = "Bruno Díaz";
const DOCTOR_NO_POOL = "Carlos Núñez";
const DNI_PAYLOAD = "00512345678@PEREZ@ANA MARIA@F@12345678@01/02/1990@15/03/2020";
const DNI_SCAN_URL = fingerprintUrl("static/platform/dni-scan.mjs");
const DNI_BARCODE_URL = fingerprintUrl("static/platform/dni-barcode.mjs");
// The real native Day Queue controller and the stylesheet its document owns —
// both at the fingerprinted URLs the serving side would compute for these
// exact bytes. Not published through window.SANA_STATIC: in production the
// template imports the module directly, and the manifest is only for the
// dependencies the module itself resolves at runtime.
const QUEUE_MODULE_URL = fingerprintUrl("static/platform/admin-queue.mjs");
const QUEUE_CSS_URL = fingerprintUrl("static/platform/admin-queue.css");
// The synthetic day the queue screen owns: two doctors with one approved
// turno and one free slot each. The native module renders the master list,
// the doctor detail pane and both "Dar este turno" actions from this data,
// exactly as it does from a real /api/admin/day-queue response.
const QUEUE_DAY = {
  dia: DAY, hoy: false,
  grupos: [
    {
      medico_id: "ana", medico: DOCTOR_A, especialidad: "Cardiología", centro: "Hospital",
      turnos: [{ id: "turno-ana", hora: "07:00", paciente: "Rosa Alustiza", servicio: "Cardiología", centro: "Hospital", elegible_asistencia: true }],
      libres: [HOUR],
    },
    {
      medico_id: "bruno", medico: DOCTOR_B, especialidad: "Cardiología", centro: "Hospital",
      turnos: [{ id: "turno-bruno", hora: "07:30", paciente: "Hugo Montes", servicio: "Cardiología", centro: "Hospital", elegible_asistencia: true }],
      libres: ["09:00"],
    },
  ],
};
const ZXING_READER_URL = "/vendor/zxing-wasm/es/reader/index.js";
const SERVICE = {
  id: "cardiologia-hospital",
  name: "Cardiología",
  nombre: "Cardiología",
  modo: "solicitud",
  cluster: "cardiologia",
  centro: "hospital",
};
const FILES = {
  [MODULE_PATH]: "static/platform/admin-create-appointment.mjs",
  [MODULE_RETRY_PATH]: "static/platform/admin-create-appointment.mjs",
  "/admin-app.js": "static/root/admin-app.js",
  "/static/platform/admin-content.mjs": "static/platform/admin-content.mjs",
  "/static/platform/dates.mjs": "static/platform/dates.mjs",
  "/static/platform/format.mjs": "static/platform/format.mjs",
  "/static/platform/admin-filter-controls.mjs": "static/platform/admin-filter-controls.mjs",
  "/static/platform/attendance-forecast.mjs": "static/platform/attendance-forecast.mjs",
  "/static/platform/admin-combo.mjs": "static/platform/admin-combo.mjs",
  "/static/platform/admin-preferences.mjs": "static/platform/admin-preferences.mjs",
  "/static/platform/admin-session.mjs": "static/platform/admin-session.mjs",
  "/static/platform/admin-table.mjs": "static/platform/admin-table.mjs",
  "/static/platform/hours.mjs": "static/platform/hours.mjs",
  "/static/platform/calendar-grid.mjs": "static/platform/calendar-grid.mjs",
  "/static/platform/islands.mjs": "static/platform/islands.mjs",
  [QUEUE_MODULE_URL]: "static/platform/admin-queue.mjs",
  [QUEUE_CSS_URL]: "static/platform/admin-queue.css",
  [DNI_SCAN_URL]: "static/platform/dni-scan.mjs",
  [DNI_BARCODE_URL]: "static/platform/dni-barcode.mjs",
  "/static/platform/account-form.mjs": "static/platform/account-form.mjs",
  "/static/platform/i18n.mjs": "static/platform/i18n.mjs",
  "/static/platform/search.mjs": "static/platform/search.mjs",
  "/static/platform/admin-solicitudes.mjs": "static/platform/admin-solicitudes.mjs",
  "/static/platform/admin-hours-editor.mjs": "static/platform/admin-hours-editor.mjs",
  "/static/platform/admin-users.mjs": "static/platform/admin-users.mjs",
  "/static/platform/hsi-sync.mjs": "static/platform/hsi-sync.mjs",
};
const STATIC = {
  "platform/dates.mjs": "/static/platform/dates.mjs",
  "platform/format.mjs": "/static/platform/format.mjs",
  "platform/admin-filter-controls.mjs": "/static/platform/admin-filter-controls.mjs",
  "platform/attendance-forecast.mjs": "/static/platform/attendance-forecast.mjs",
  "platform/admin-combo.mjs": "/static/platform/admin-combo.mjs",
  "platform/admin-preferences.mjs": "/static/platform/admin-preferences.mjs",
  "platform/admin-session.mjs": "/static/platform/admin-session.mjs",
  "platform/admin-table.mjs": "/static/platform/admin-table.mjs",
  "platform/hours.mjs": "/static/platform/hours.mjs",
  "platform/calendar-grid.mjs": "/static/platform/calendar-grid.mjs",
  "platform/islands.mjs": "/static/platform/islands.mjs",
  "platform/dni-scan.mjs": DNI_SCAN_URL,
  "platform/dni-barcode.mjs": DNI_BARCODE_URL,
  "platform/account-form.mjs": "/static/platform/account-form.mjs",
  "platform/i18n.mjs": "/static/platform/i18n.mjs",
  "platform/search.mjs": "/static/platform/search.mjs",
  "platform/admin-solicitudes.mjs": "/static/platform/admin-solicitudes.mjs",
  "platform/admin-hours-editor.mjs": "/static/platform/admin-hours-editor.mjs",
  "platform/admin-users.mjs": "/static/platform/admin-users.mjs",
  "platform/hsi-sync.mjs": "/static/platform/hsi-sync.mjs",
  "platform/admin-create-appointment.mjs": MODULE_PATH,
};

function deferred() {
  let release;
  let done = false;
  const promise = new Promise((resolve) => { release = resolve; });
  return { promise, release() { if (!done) { done = true; release(); } } };
}

function pageHtml(kind) {
  // The queue screen mirrors templates/admin/cola.html's ownership: the
  // DOCUMENT owns the native stylesheet (a <link>, not controller-injected
  // CSS), and an inline module script imports the fingerprinted controller
  // and awaits mount()'s readiness. No classic script tag is involved.
  const queueHead = kind === "queue"
    ? `<link id="cola-css" rel="stylesheet" href="${QUEUE_CSS_URL}">`
    : "";
  const boot = kind === "queue"
    ? `<script type="module">
const queue = await import(${JSON.stringify(QUEUE_MODULE_URL)});
const owner = queue.mount(document);
await owner.ready;
</script>`
    : kind === "doctors"
      ? `<script type="module">const content = await import('/static/platform/admin-content.mjs'); const owner = content.mount(document); window.__catalogOwner = owner; await owner.ready;</script>`
      : `<script src="/admin-app.js"></script>`;
  const screen = kind === "queue" ? "cola" : kind === "doctors" ? "medicos" : "solicitudes";
  const extra = kind === "queue"
    ? `<div id="sec-cola"><p class="adm-empty">Cola del ${DAY}</p></div>
       <script id="queue-initial-data" type="application/json">${JSON.stringify(QUEUE_DAY)}</script>`
    : kind === "doctors"
      ? `<div class="adm-content-wrap"><div id="sec-medicos"></div></div>`
      : `<div id="sec-solicitudes">
           <input id="q" type="search">
           <select id="f-estado"></select>
           <select id="f-medico"></select>
           <select id="f-especialidad"></select>
           <select id="f-servicio"></select>
           <select id="f-centro"></select>
           <select id="f-verdict"></select>
           <select id="f-sug"></select>
           <a id="f-clear"></a>
           <div id="list"></div>
         </div>`;
  return `<!doctype html><html lang="es"><head><meta charset="utf-8">
${queueHead}
<script>
window.SANA_ADMIN_SCREEN=${JSON.stringify(screen)};
window.CURRENT_ROLE="admin";
window.SANA_COUNTRY_PHONE=${JSON.stringify(COUNTRY_PHONE)};
window.SANA_I18N={lang:"es",catalog:{}};
window.SANA_STATIC=${JSON.stringify(STATIC)};
window.sanaImportStatic=function(name){
  var u=window.SANA_STATIC[name];
  return u?import(u):Promise.reject(new Error("[static] "+name+" is not in window.SANA_STATIC"));
};
window.toast=function(msg){ window.__toasts=(window.__toasts||[]).concat([String(msg)]); };
</script></head><body>
<nav id="adm-nav"><a data-nav="${screen}" href="/admin/${kind === "queue" ? "cola" : kind === "doctors" ? "doctors" : "solicitudes"}">Nav</a></nav>
<header class="adm-topbar"><div class="adm-h1row">
  <h1 id="adm-h1" class="adm-h1"></h1>
  <button type="button" class="btn btn-accent" id="adm-crear-turno">Crear turno</button>
</div></header>
${extra}
${boot}
</body></html>`;
}

function jsonOk(route, body) {
  return route.fulfill({ json: body });
}

async function fulfill(route, opts) {
  const url = new URL(route.request().url());
  const pathname = url.pathname;
  if (opts.requestedPaths) opts.requestedPaths.push(pathname);
  if (opts.onRequest) opts.onRequest(pathname);
  const hold = opts.holdModule;
  if (opts.abortModule && pathname === MODULE_PATH) return route.abort("failed");
  if (hold && pathname === MODULE_PATH) {
    hold.requested.release();
    await hold.release.promise;
  }
  if (pathname === "/api/admin/whoami") {
    return jsonOk(route, { usuario: "mesa", rol: "admin" });
  }
  // Free hours are hidden until «Mostrar horarios libres» (2026-10-27); this desk
  // has it ticked, which is what offers «Dar este turno».
  if (pathname === "/api/admin/preferences") return jsonOk(route, { items: { "day_queue.filters": { verLibres: true } } });
  if (pathname === "/api/admin/requests") return jsonOk(route, { solicitudes: opts.requestRows || [] });
  if (pathname === "/api/admin/day-queue") {
    if (opts.queueReads) opts.queueReads.push(url.searchParams.get("dia"));
    if (opts.savedQueue && opts.posts.length) return jsonOk(route, opts.savedQueue);
    return jsonOk(route, QUEUE_DAY);
  }
  if (pathname === "/api/admin/day-queue/calendar" && opts.calendar) return jsonOk(route, opts.calendar);
  if (pathname === "/api/admin/day-queue/events") {
    return route.fulfill({ status: 200, contentType: "text/event-stream", body: ":\n\n" });
  }
  if (pathname === "/api/admin/patients" && route.request().method() === "POST") {
    const alta = route.request().postDataJSON();
    if (opts.altas) opts.altas.push(alta);
    if (opts.altaRefused) return route.fulfill({ status: opts.altaRefused.status, json: opts.altaRefused.body });
    return jsonOk(route, { ok: true, item: Object.assign({ activo: true }, alta), password_temporal: "temporal-123" });
  }
  if (pathname === "/api/admin/patients") return jsonOk(route, { items: opts.patients || [] });
  if (pathname === "/api/admin/users") return jsonOk(route, { items: [] });
  if (pathname === "/api/admin/intake/settings") return jsonOk(route, { medicos: [] });
  if (pathname.endsWith("/compatible-services")) return jsonOk(route, { items: [] });
  if (pathname.endsWith("/photo") && pathname !== "/api/admin/doctors/created-doctor/photo") return route.fulfill({ status: 404, body: "" });
  if (pathname === "/api/admin/content/services" || pathname === "/api/services") {
    return jsonOk(route, { items: [SERVICE], servicios: [SERVICE] });
  }
  if (pathname === "/api/admin/content/centers" || pathname === "/api/centers") {
    return jsonOk(route, { items: [{ slug: "hospital", name: "Hospital" }], centros: [{ slug: "hospital", name: "Hospital" }] });
  }
  if ((pathname === "/api/admin/content/doctors" || pathname === "/api/doctors") && route.request().method() === "GET") {
    const docs = [
      { id: "ana", nombre: opts.catalogMutation?.savedName || DOCTOR_A, cluster: "cardiologia", centro: "hospital", centros: [{ centro: "hospital" }], _servicios_pool: ["cardiologia-hospital"] },
      { id: "bruno", nombre: DOCTOR_B, cluster: "cardiologia", centro: "hospital", centros: [{ centro: "hospital" }], _servicios_pool: ["cardiologia-hospital"] },
      // same cluster + center as the service, but the server's pool leaves it out
      { id: "carlos", nombre: DOCTOR_NO_POOL, cluster: "cardiologia", centro: "hospital", centros: [{ centro: "hospital" }], _servicios_pool: [] },
    ];
    return jsonOk(route, { items: docs, medicos: docs });
  }
  if (pathname === "/api/admin/appointment-slots" && opts.slotsBody) return jsonOk(route, opts.slotsBody);
  if (pathname === "/api/admin/appointment-slots") {
    const medico = url.searchParams.get("medico") || DOCTOR_A;
    return jsonOk(route, {
      libres: [
        { dia: DAY, hora: HOUR, medico },
        { dia: DAY, hora: "09:00", medico },
      ],
      sobreturnos: [],
    });
  }
  if (pathname === "/api/admin/content/doctors/ana" && route.request().method() === "PUT" && opts.catalogMutation) {
    const state = opts.catalogMutation;
    const payload = route.request().postDataJSON();
    state.posts.push(payload);
    state.requested.release();
    await state.release.promise;
    if (state.failure) return route.fulfill({ status: 409, json: { ok: false, error: "synthetic_conflict" } });
    state.savedName = `${payload.nombre_pila} ${payload.apellido}`.trim();
    return jsonOk(route, { ok: true, item: { id: "ana", ...payload } });
  }
  if (pathname === "/api/admin/content/doctors" && route.request().method() === "POST" && opts.catalogCreate) {
    const state = opts.catalogCreate;
    const payload = route.request().postDataJSON();
    state.posts.push(payload);
    state.requested.release();
    await state.release.promise;
    return jsonOk(route, { ok: true, item: { id: "created-doctor", ...payload } });
  }
  if (pathname === "/api/admin/doctors/created-doctor/photo" && route.request().method() === "POST" && opts.catalogCreate) {
    opts.catalogCreate.photos.push(route.request().postDataJSON());
    return jsonOk(route, { ok: true });
  }
  if (pathname === "/api/admin/appointments" && route.request().method() === "POST") {
    opts.posts.push(route.request().postDataJSON());
    if (opts.holdSave) {
      opts.holdSave.requested.release();
      await opts.holdSave.release.promise;
    }
    if (opts.abortSave) return route.abort("failed");
    if (opts.saveFailure) return route.fulfill({ status: 409, json: { error: "turno_tomado" } });
    // Custom status/body for the create acknowledgement: an object is served
    // as JSON, a string as raw bytes (malformed-JSON fixtures). The backend
    // contract is {ok:true} only after persistence, so these are the bodies
    // the overlay must NOT read as success.
    if (opts.saveResponse) {
      const spec = opts.saveResponse;
      return typeof spec.body === "string"
        ? route.fulfill({ status: spec.status || 200, contentType: "application/json", body: spec.body })
        : route.fulfill({ status: spec.status || 200, json: spec.body });
    }
    return jsonOk(route, { ok: true, id: 91 });
  }
  if (pathname === "/api/admin/doctors/stats") return jsonOk(route, {});
  if (/^\/api\/admin\/doctors\/(ana|bruno)\/appointments$/.test(pathname)) {
    if (opts.doctorReads) opts.doctorReads.push(pathname);
    return jsonOk(route, {
      total: 1,
      dias: 30,
      dias_detalle: [{ dia: DAY, capacidad: 8, ocupados: 1 }],
      turnos: [{ id: "request-1", dia_iso: DAY, hora: "07:30", paciente: pathname.includes("/bruno/") ? "Paciente Bruno" : "Paciente", dni: "1", servicio: "Cardiología", centro: "Hospital" },
        ...Array.from({ length: opts.manyDoctorRows ? 11 : 0 }, (_, i) => ({ id: `extra-${i}`, dia_iso: DAY, hora: "08:00", paciente: `Synthetic ${i}`, dni: String(i + 2), servicio: "Cardiología", centro: "Hospital" })),
        ...(opts.posts.length ? [{ dia_iso: DAY, hora: HOUR, paciente: "Mesa", dni: "", servicio: "Cardiología", centro: "Hospital" }] : [])],
    });
  }
  if (pathname === "/data/especialidades.json") return jsonOk(route, { specialties: [] });
  if (pathname === "/data/clusters.json") return jsonOk(route, { clusters: [] });
  if (pathname === "/data/medicos.json") return jsonOk(route, { medicos: [] });
  if (pathname === ZXING_READER_URL) {
    return route.fulfill({
      contentType: "text/javascript; charset=utf-8",
      body: "export const setZXingModuleOverrides=()=>{};export const readBarcodes=async()=>" + JSON.stringify(opts.barcodeResults || []) + ";",
    });
  }
  if (FILES[pathname]) {
    const file = FILES[pathname];
    return route.fulfill({
      contentType: file.endsWith(".css") ? "text/css; charset=utf-8" : "text/javascript; charset=utf-8",
      body: read(file) + (pathname === MODULE_PATH
        ? "\nwindow.__createAppointmentModuleEvaluated = true;\n"
        : ""),
    });
  }
  if (pathname === "/admin/solicitudes" || pathname === "/admin/cola"
      || pathname === "/admin/doctors" || pathname.startsWith("/admin/doctors")) {
    const kind = pathname.includes("cola") ? "queue"
      : pathname.includes("doctors") ? "doctors" : "app";
    return route.fulfill({ contentType: "text/html; charset=utf-8", body: pageHtml(kind) });
  }
  return route.fulfill({ status: 404, body: "" });
}

// `now` freezes the page clock (Date/Intl in the browser, timers keep running):
// «today» is the installation's day, which near midnight differs from the
// browser's own, so a test about «today» pins the instant instead of reading
// the wall clock.
//
// One Chromium for the whole file, a fresh context per page: a context has its
// own storage, routes, clock and init scripts, so each page starts as clean as
// with its own browser. Launching Chromium per page was most of the file's
// time — under load a launch alone took seconds, and the tests that open
// several pages ran into their 30 s.
let browser;
test.before(async () => { browser = await chromium.launch(); }, hookOptions());
test.after(async () => { await browser?.close(); });

async function withPage(run, { now } = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  try {
    const page = await context.newPage();
    if (now) await page.clock.setFixedTime(now);
    ceil(page);
    const failures = [];
    page.on("pageerror", (error) => failures.push(String(error)));
    await run(page, failures);
    assert.deepEqual(
      failures.filter((msg) => !/failed to fetch|Failed to fetch|Load failed|error loading dynamically imported module/i.test(msg)),
      [],
    );
  } finally {
    await context.close();
  }
}

// The day is one flat list (2026-11-08): a doctor is picked with the link in
// its row's Médico cell. A list already narrowed to one doctor has no Médico
// column, so «Ver todos los médicos» comes first; picking the same doctor
// again that way re-renders the list.
async function pickDoctor(page, key) {
  const link = page.locator('button.queue-doctor-link[data-cola-doctor="' + key + '"]').first();
  await page.locator("button.queue-doctor-link, button.queue-doctor-all").first().waitFor({ state: "visible" });
  if (!(await link.isVisible())) {
    await page.locator("button.queue-doctor-all").first().click();
  }
  await link.click();
  await page.locator('[data-cola-elegido="' + key + '"]').waitFor({ state: "visible" });
}

test("Queue refreshes its native owner after a saved appointment without SSE", async () => {
  await withPage(async (page) => {
    const posts = [];
    const queueReads = [];
    const savedQueue = structuredClone(QUEUE_DAY);
    savedQueue.grupos[0].turnos.push({ id: "new-91", hora: HOUR, paciente: "Mesa", servicio: "Cardiología", centro: "Hospital", elegible_asistencia: true });
    savedQueue.grupos[0].libres = [];
    await page.addInitScript(() => { window.EventSource = undefined; });
    await page.route(`${ORIGIN}/**`, (route) => fulfill(route, { posts, queueReads, savedQueue }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await pickDoctor(page, "ana");
    queueReads.length = 0;
    await page.click('button.queue-give[data-cola-dar="08:30"]');
    await page.fill("#ct-pac", "Mesa");
    await page.click('#ct-pac-sug li[data-nota]');
    await page.locator("#ct-ok:not([disabled])").click();
    await page.locator("#sec-cola tr.cnt-tr").filter({ hasText: "Mesa" }).waitFor({ state: "visible" });
    assert.equal(posts.length, 1);
    assert.equal(posts[0].dia, DAY);
    assert.equal(posts[0].hora, HOUR);
    assert.equal(posts[0].medico, DOCTOR_A);
    assert.deepEqual(queueReads, [DAY]);
    assert.equal(await page.locator("#ct-back").count(), 0);
    assert.equal(await page.evaluate(() => typeof window.sanaRouteCola), "undefined");
  });
});

// Lira 13.10.2026: the month calendar lights the days a doctor attends even
// with nobody booked, apart from the days that already have turnos.
test("The month calendar rings the doctor's attending days that have no turno yet", async () => {
  await withPage(async (page) => {
    const calendar = { dia: DAY, dias: ["2026-09-10"], conteos: { "2026-09-10": 2 }, atencion: ["2026-09-14", "2026-09-21"], anterior: null, siguiente: "2026-09-10" };
    await page.addInitScript(() => { window.EventSource = undefined; });
    await page.route(`${ORIGIN}/**`, (route) => fulfill(route, { posts: [], calendar }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await page.click("[data-cola-calendar-toggle]");
    const cell = (day) => page.locator('[data-cola-calendar-date="' + day + '"]');
    await cell("2026-09-14").waitFor();
    assert.match(await cell("2026-09-14").getAttribute("class"), /queue-calendar-attends/);
    assert.match(await cell("2026-09-21").getAttribute("class"), /queue-calendar-attends/);
    assert.match(await cell("2026-09-10").getAttribute("class"), /queue-calendar-occupied/);
    assert.doesNotMatch(await cell("2026-09-10").getAttribute("class"), /queue-calendar-attends/);
    assert.doesNotMatch(await cell("2026-09-15").getAttribute("class"), /queue-calendar-(attends|occupied)/);
    // The ring explains itself on the day; no counter line under the grid.
    assert.equal(await cell("2026-09-14").getAttribute("title"), "Atiende, sin turnos");
    assert.equal(await cell("2026-09-10").getAttribute("title"), null);
    assert.equal(await page.locator(".queue-month-calendar p").count(), 0);
  });
});

test("A saved appointment cannot refresh a disposed queue owner", async () => {
  await withPage(async (page) => {
    const posts = [], queueReads = [];
    const requested = deferred(), release = deferred();
    await page.addInitScript(() => { window.EventSource = undefined; });
    await page.route(`${ORIGIN}/**`, (route) => fulfill(route, { posts, queueReads, holdSave: { requested, release } }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await pickDoctor(page, "ana");
    await page.click('button.queue-give[data-cola-dar="08:30"]');
    await page.fill("#ct-pac", "Mesa");
    await page.click('#ct-pac-sug li[data-nota]');
    await page.locator("#ct-ok:not([disabled])").click();
    await requested.promise;
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide")));
    queueReads.length = 0;
    const afterSave = page.waitForResponse(r => r.url().endsWith("/api/admin/appointments"));
    release.release();
    await afterSave;
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.deepEqual(queueReads, []);
    assert.equal(posts.length, 1);
    assert.equal(await page.locator("#ct-back").count(), 0);
  });
});

// Launch plan P12: the server now refuses a named doctor's turno outside
// their agenda with the approval's own codes; the desk reads them as words.
test("A turno outside the doctor's agenda reads why, never the server's code", async () => {
  for (const [code, expected] of [
    ["turno_medico_fuera_de_agenda", /fuera de la agenda del médico/],
    ["turno_medico_sin_agenda", /no tiene agenda cargada/],
    ["turno_medico_horario_cerrado", /no atiende/],
    ["turno_medico_horario_bloqueado", /bloqueado/],
  ]) {
    await withPage(async (page) => {
      const posts = [];
      await page.addInitScript(() => { window.EventSource = undefined; });
      await page.route(`${ORIGIN}/**`, (route) => fulfill(route, { posts, saveResponse: { status: 409, body: { error: code } } }));
      await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
      await pickDoctor(page, "ana");
      await page.click('button.queue-give[data-cola-dar="08:30"]');
      await page.fill("#ct-pac", "Mesa");
      await page.click('#ct-pac-sug li[data-nota]');
      await page.locator("#ct-ok:not([disabled])").click();
      await page.locator("#ct-err:not([hidden])").waitFor();
      const shown = await page.locator("#ct-err").innerText();
      assert.equal(posts.length, 1);
      assert.ok(!shown.includes(code), `${code} must not reach the desk as is: ${shown}`);
      assert.match(shown, expected, code);
    });
  }
});

// A 401 the session check does not confirm (site Basic Auth, a proxy; whoami
// still answers live) is a save that did not happen: the desk is told so, the
// overlay is not closed without a word.
test("An unconfirmed 401 on the save keeps the overlay and says it was not confirmed", async () => {
  await withPage(async (page) => {
    const posts = [];
    await page.addInitScript(() => { window.EventSource = undefined; });
    await page.route(`${ORIGIN}/**`, (route) => fulfill(route, { posts, saveResponse: { status: 401, body: { error: "no_autenticado" } } }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await pickDoctor(page, "ana");
    await page.click('button.queue-give[data-cola-dar="08:30"]');
    await page.fill("#ct-pac", "Mesa");
    await page.click('#ct-pac-sug li[data-nota]');
    await page.locator("#ct-ok:not([disabled])").click();
    await page.locator("#ct-err:not([hidden])").waitFor();
    assert.equal(posts.length, 1);
    assert.match(await page.locator("#ct-err").innerText(), /No se pudo confirmar el turno/);
  });
});

test("A late failed save cannot reopen the overlay after queue disposal", async () => {
  await withPage(async (page) => {
    const posts = [];
    const requested = deferred(), release = deferred();
    await page.addInitScript(() => { window.EventSource = undefined; });
    await page.route(`${ORIGIN}/**`, route => fulfill(route, { posts, saveFailure: true, holdSave: { requested, release } }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await pickDoctor(page, "ana");
    await page.click('button.queue-give[data-cola-dar="08:30"]');
    await page.fill("#ct-pac", "Mesa");
    await page.click('#ct-pac-sug li[data-nota]');
    await page.locator("#ct-ok:not([disabled])").click();
    await requested.promise;
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide")));
    const afterSave = page.waitForResponse(r => r.url().endsWith("/api/admin/appointments"));
    release.release();
    await afterSave;
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(posts.length, 1);
    assert.equal(await page.locator("#ct-back").count(), 0, "disposed owner cannot restore its modal on a late 409");
  });
});

// The doctor↔service link is the server's pool (`_servicios_pool`, belongs):
// a pinned doctor with no service in it gets a visible reason and the way out,
// never the whole catalog; the pinned doctor's own services are the pool.
test("A pinned doctor outside every service's pool is told so, never offered the whole catalog", async () => {
  await withPage(async page => {
    await page.route(`${ORIGIN}/**`, route => fulfill(route, { posts: [] }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await page.locator('button.queue-doctor-link[data-cola-doctor="ana"]').first().waitFor();
    await page.evaluate(async ({ url, doctor }) => {
      const overlay = await import(url);
      overlay.abrir({ medico: doctor });
    }, { url: MODULE_PATH, doctor: DOCTOR_NO_POOL });
    await page.fill("#ct-pac", "Mesa");
    await page.click('#ct-pac-sug li[data-nota]');
    await page.locator("#ct-sin-servicios").waitFor({ state: "visible" });
    assert.match(await page.locator("#ct-sin-servicios").innerText(), /Carlos Núñez.*Quitá al médico/);
    assert.equal(await page.locator("#ct-svc").count(), 0, "no service search while the pinned doctor has no services");
    await page.click('[data-cambiar="medico"]');
    await page.locator("#ct-svc").waitFor({ state: "visible" });
    await page.fill("#ct-svc", "cardio");
    await page.locator('#ct-svc-sug li[data-svc="cardiologia-hospital"]').waitFor({ state: "visible" });
  });
});

// Launch plan P1: a service that owns its schedule offers minutes that name
// nobody; the server sends the service's doctors (`medicos`) and the desk
// chooses one — before, the turno went out with no doctor («Médico no asignado»).
// Four separate page loads (two doctors, one doctor, «A asignar», no list): alone it
// takes ~29.5 s on the dev machine, so the 30 s budget of a one-load test timed it out
// whenever the machine had another suite running.
test("Minutes with no doctor ask which of the service's doctors gives the turno", async () => {
  const unnamed = (medicos) => ({
    libres: [{ dia: DAY, hora: HOUR, medico: "" }, { dia: DAY, hora: "09:00", medico: "" }],
    sobreturnos: [], medicos,
  });
  async function book(page, slotsBody) {
    const posts = [];
    await page.route(`${ORIGIN}/**`, (route) => fulfill(route, { posts, slotsBody }));
    await page.goto(ORIGIN + "/admin/solicitudes", { waitUntil: "domcontentloaded" });
    await page.click("#adm-crear-turno");
    await page.fill("#ct-pac", "Mesa");
    await page.click("#ct-pac-sug li[data-nota]");
    await page.fill("#ct-svc", "cardio");
    await page.click('#ct-svc-sug li[data-svc]');
    await page.locator(".ct-prop").waitFor({ state: "visible" });
    return posts;
  }
  async function crear(page) {
    const saved = page.waitForResponse((r) => r.url().endsWith("/api/admin/appointments"));
    await page.locator("#ct-ok:not([disabled])").click();
    await saved;
  }
  // two doctors: nothing is booked until one is chosen; the button says so
  // next to it, and a press leads to the choice instead of doing nothing
  await withPage(async (page) => {
    const posts = await book(page, unnamed([DOCTOR_A, DOCTOR_B]));
    await page.locator("#ct-med-turno").waitFor({ state: "visible" });
    assert.equal(await page.locator("#ct-ok").getAttribute("aria-disabled"), "true", "«Crear turno» waits for the doctor");
    assert.match(await page.locator("#ct-falta-medico").innerText(), /Elegí el médico para crear el turno/);
    // aria-disabled, not disabled: a person can press it (Playwright would wait
    // for «enabled» forever, hence force)
    await page.click("#ct-ok", { force: true });
    assert.equal(posts.length, 0, "a press without a doctor books nothing");
    assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.id), "ct-med-turno",
      "the press takes the desk to the doctor choice");
    assert.match(await page.locator("#ct-med-turno").getAttribute("class"), /ct-falta/);
    const options = await page.locator("#ct-med-turno option").allInnerTexts();
    assert.ok(options.includes(DOCTOR_A) && options.includes(DOCTOR_B), options.join(" | "));
    await page.selectOption("#ct-med-turno", DOCTOR_B);
    assert.equal(await page.locator("#ct-ok").getAttribute("aria-disabled"), null);
    assert.equal(await page.locator("#ct-falta-medico").count(), 0);
    await crear(page);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].medico, DOCTOR_B);
  });
  // one doctor: that doctor gives the turno, already chosen
  await withPage(async (page) => {
    const posts = await book(page, unnamed([DOCTOR_A]));
    await page.locator("#ct-med-turno").waitFor({ state: "visible" });
    assert.equal(await page.locator("#ct-med-turno").inputValue(), DOCTOR_A);
    await crear(page);
    assert.equal(posts[0].medico, DOCTOR_A);
  });
  // «A asignar» stays a deliberate choice (the server allows no doctor)
  await withPage(async (page) => {
    const posts = await book(page, unnamed([DOCTOR_A, DOCTOR_B]));
    await page.selectOption("#ct-med-turno", "-");
    await crear(page);
    assert.equal(posts[0].medico, "");
  });
  // no list (a lab, or minutes that already name their doctor): nothing asked
  await withPage(async (page) => {
    const posts = await book(page, unnamed([]));
    assert.equal(await page.locator("#ct-med-turno").count(), 0);
    await crear(page);
    assert.equal(posts[0].medico, "");
  });
});

// TASK-54 (26.10, persona Karla): with today full the dialog proposed
// tomorrow's first slot in silence. The proposal says so in a line; with a
// free slot today, no line.
// The instants straddle the Buenos Aires midnight: 01:30 UTC is still the day
// before there (22:30), 15:00 UTC is midday of the same date. The test asks
// the app's own dates.mjs for «today» (installation clock) at that instant
// instead of the node process's zone, so it holds in any TZ and at any hour.
for (const at of ["2026-10-27T01:30:00Z", "2026-10-27T15:00:00Z"]) {
  test(`With no free slot today the proposal says so before naming a later day (${at})`, async () => {
    const now = new Date(at);
    const today = hoyIsoInstalacion(now);
    const tomorrow = new Date(Date.parse(today + "T12:00:00Z") + 86400000).toISOString().slice(0, 10);
    async function propose(page, dia) {
      await page.route(`${ORIGIN}/**`, (route) => fulfill(route, { posts: [],
        slotsBody: { libres: [{ dia, hora: "10:00", medico: DOCTOR_A }], sobreturnos: [] } }));
      await page.goto(ORIGIN + "/admin/solicitudes", { waitUntil: "domcontentloaded" });
      await page.click("#adm-crear-turno");
      await page.fill("#ct-pac", "Mesa");
      await page.click("#ct-pac-sug li[data-nota]");
      await page.fill("#ct-svc", "cardio");
      await page.click('#ct-svc-sug li[data-svc]');
      await page.locator(".ct-prop").waitFor({ state: "visible" });
    }
    await withPage(async (page) => {
      await propose(page, tomorrow);
      assert.match(await page.locator("#ct-hoy-sin-lugar").innerText(), /Hoy no hay horarios libres/);
    }, { now });
    await withPage(async (page) => {
      await propose(page, today);
      assert.equal(await page.locator("#ct-hoy-sin-lugar").count(), 0);
    }, { now });
  });
}

test("A pinned doctor in the pool is offered the service", async () => {
  await withPage(async page => {
    await page.route(`${ORIGIN}/**`, route => fulfill(route, { posts: [] }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await page.locator('button.queue-doctor-link[data-cola-doctor="ana"]').first().waitFor();
    await page.evaluate(async ({ url, doctor }) => {
      const overlay = await import(url);
      overlay.abrir({ medico: doctor });
    }, { url: MODULE_PATH, doctor: DOCTOR_B });
    await page.fill("#ct-pac", "Mesa");
    await page.click('#ct-pac-sug li[data-nota]');
    // the pool holds exactly one service: it arrives already chosen
    await page.locator('.ct-paso', { has: page.locator('[data-cambiar="servicio"]') }).first().waitFor({ state: "visible" });
    assert.equal(await page.locator("#ct-sin-servicios").count(), 0);
  });
});

test("«Reprogramar» from the ficha arrives with the patient, the service and the doctor chosen; only the time is left", async () => {
  // Product owner, 2026-11-05: a cancelled turno is rescheduled from the patient's
  // ficha through the desk's own «Crear turno».
  const posts = [];
  await withPage(async page => {
    await page.route(`${ORIGIN}/**`, route => fulfill(route, { posts }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await page.locator('button.queue-doctor-link[data-cola-doctor="ana"]').first().waitFor();
    await page.evaluate(async ({ url, doctor, service }) => {
      const overlay = await import(url);
      overlay.abrir({ paciente: { dni: "30111222", apellido: "Varela", nombre: "Alejo" }, servicio_id: service, medico: doctor });
    }, { url: MODULE_PATH, doctor: DOCTOR_B, service: SERVICE.id });
    await page.locator(".ct-elegido", { hasText: "Varela, Alejo" }).waitFor();
    assert.equal(await page.locator("#ct-pac").count(), 0, "no patient search: the ficha already said who");
    await page.locator(".ct-prop-cuando").first().waitFor();
    const saved = page.waitForResponse((r) => r.url().endsWith("/api/admin/appointments"));
    await page.click("#ct-ok");
    await saved;
    assert.equal(posts.length, 1);
    assert.equal(posts[0].dni, "30111222");
    assert.equal(posts[0].servicio_id, SERVICE.id);
    assert.equal(posts[0].medico, DOCTOR_B);
  });
});

test("Queue disposal closes its open create overlay", async () => {
  await withPage(async page => {
    const posts = [];
    await page.route(`${ORIGIN}/**`, route => fulfill(route, { posts }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await pickDoctor(page, "ana");
    await page.click('button.queue-give[data-cola-dar="08:30"]');
    await page.locator("#ct-back").waitFor({ state: "visible" });
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide")));
    assert.equal(await page.locator("#ct-back").count(), 0);
    assert.deepEqual(posts, []);
  });
});

test("A current queue keeps the patient's entry after a save conflict", async () => {
  await withPage(async page => {
    const posts = [];
    await page.route(`${ORIGIN}/**`, route => fulfill(route, { posts, saveFailure: true }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await pickDoctor(page, "ana");
    await page.click('button.queue-give[data-cola-dar="08:30"]');
    await page.fill("#ct-pac", "Mesa");
    await page.click('#ct-pac-sug li[data-nota]');
    await page.locator("#ct-ok:not([disabled])").click();
    await page.waitForFunction(() => document.querySelector("#ct-back")?.textContent.includes("Elegí otro"));
    assert.equal(posts.length, 1);
    assert.match(await page.locator("#ct-back").innerText(), /Mesa/);
    assert.equal(await page.locator("#ct-ok").isEnabled(), true);
  });
});

test("Owner refresh failure does not reopen or misreport a saved appointment", async () => {
  await withPage(async (page) => {
    const posts = [];
    await page.route(`${ORIGIN}/**`, route => fulfill(route, { posts }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await page.locator('button.queue-doctor-link[data-cola-doctor="ana"]').first().waitFor();
    await page.evaluate(async ({ url, day, doctor, hour }) => {
      const overlay = await import(url);
      overlay.abrir({ dia: day, medico: doctor, hora: hour }, { onCreated() {
        window.__refreshAttempted = true;
        throw new Error("synthetic owner refresh failure");
      } });
    }, { url: MODULE_PATH, day: DAY, doctor: DOCTOR_A, hour: HOUR });
    await page.fill("#ct-pac", "Mesa");
    await page.click('#ct-pac-sug li[data-nota]');
    await page.locator("#ct-ok:not([disabled])").click();
    await page.waitForFunction(() => window.__refreshAttempted === true);
    assert.equal(posts.length, 1);
    assert.equal(await page.locator("#ct-back").count(), 0);
    assert.ok(!(await page.evaluate(() => window.__toasts)).some(t => t.includes("No se guardó")));
  });
});

// «Turno creado» (contraparte técnica, 2026-10-19: «después de crear este turno tiene que
// ofrecer sincronizar con HSI»). With a patient record the overlay stays open
// and says where the turno stands in HSI.
const PATIENT = { dni: "30111222", nombre: "Sofía", apellido: "Herrera", activo: true };
const STORE = "https://chromewebstore.google.com/detail/jjnbmfhllglgplkphafoppdogikeegni";

async function createForPatient(page, hsi) {
  const posts = [];
  await page.addInitScript(() => { window.EventSource = undefined; });
  await page.route(`${ORIGIN}/**`, (route) => fulfill(route, {
    posts, patients: [PATIENT], saveResponse: { body: { ok: true, id: 91, hsi } },
  }));
  await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
  await pickDoctor(page, "ana");
  await page.click('button.queue-give[data-cola-dar="08:30"]');
  await page.fill("#ct-pac", "30111222");
  await page.click('#ct-pac-sug li[data-dni="30111222"]');
  await page.locator("#ct-ok:not([disabled])").click();
  await page.locator("#ct-back h2").filter({ hasText: "Turno creado" }).waitFor({ state: "visible" });
  return posts;
}

test("Without HSI connected, «Turno creado» offers «Cargar en HSI»; with no extension the click copies the DNI and links the install", async () => {
  await withPage(async (page) => {
    const posts = await createForPatient(page, { conectado: false, estado: "skip", extension: STORE });
    assert.equal(posts.length, 1);
    const text = await page.locator("#ct-back").innerText();
    assert.match(text, /Herrera, Sofía/);
    assert.match(text, new RegExp(DOCTOR_A));
    const load = page.locator("#ct-hsi");
    assert.equal(await load.getAttribute("data-hr-hsi"), "91", "the extension's own hook carries the turno id");
    assert.equal(await load.getAttribute("data-hr-hsi-dia"), DAY, "and its day, for a turno on any day");
    await load.click();
    const help = page.locator("#ct-hsi-sin-ext");
    await help.waitFor({ state: "visible" });
    assert.match(await help.innerText(), /30\.111\.222/);
    assert.equal(await help.locator("a").getAttribute("href"), STORE);
    const toasts = await page.evaluate(() => window.__toasts || []);
    assert.match(toasts[toasts.length - 1] || "", /Turno creado/,
      "the created toast replaces «Guardando…» at once instead of leaving it behind the view");
    await page.click("#ct-listo");
    assert.equal(await page.locator("#ct-back").count(), 0);
  });
});

// Contraparte técnica, 2026-10-27: «nunca entendí cómo… llegábamos a esa pantalla». Next to
// «Cargar en HSI» one line says where to install the extension and how; the
// click's hint then only copies the DNI, and the line goes once installed.
test("«Turno creado» says where to install the extension until the extension marks the page", async () => {
  await withPage(async (page) => {
    const instalar = { tienda: STORE, zip: null, guia: "/api/admin/hsi-extension/guide.pdf" };
    await createForPatient(page, { conectado: false, estado: "skip", extension: STORE, instalar });
    const line = page.locator("#ct-hsi-instalar");
    assert.equal(await line.isVisible(), true);
    assert.deepEqual(await line.locator("a").evaluateAll((as) => as.map((a) => [a.getAttribute("href"), a.textContent])),
      [[STORE, "Instalá HSI Humanizador en Chrome"], ["/api/admin/hsi-extension/guide.pdf", "Cómo se instala (PDF)"]]);
    await page.locator("#ct-hsi").click();
    const help = page.locator("#ct-hsi-sin-ext");
    await help.waitFor({ state: "visible" });
    assert.match(await help.innerText(), /30\.111\.222/);
    assert.equal(await help.locator("a").count(), 0, "the install link is the line's, not repeated in the hint");
    await page.evaluate(() => { document.documentElement.dataset.hrHumanizador = "1"; });
    assert.equal(await line.isVisible(), false);
  });
});

test("With HSI connected, «Turno creado» says it is on its way and offers no load button", async () => {
  await withPage(async (page) => {
    await createForPatient(page, { conectado: true, estado: "pendiente", extension: null });
    assert.match(await page.locator("#ct-back").innerText(), /Envío automático a HSI/);
    assert.equal(await page.locator("#ct-hsi").count(), 0);
  });
});

test("Queue disposal also closes «Turno creado»", async () => {
  await withPage(async (page) => {
    await createForPatient(page, { conectado: false, estado: "skip", extension: STORE });
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide")));
    assert.equal(await page.locator("#ct-back").count(), 0);
  });
});

// Truthful-acknowledgement boundary cluster. The backend returns {ok:true}
// only AFTER the appointment is persisted, so the desk overlay owns exactly
// this contract: no created claim, no onCreated and no owner refresh before
// the response, and success requires body.ok === true (HTTP 200 alone is not
// an acknowledgement). Disposal-during-flight and the known-409 input
// retention are already pinned above by "A saved appointment cannot refresh
// a disposed queue owner", "A late failed save cannot reopen the overlay
// after queue disposal" and "A current queue keeps the patient's entry after
// a save conflict" — not repeated here.
test("A held create announces success only after the acknowledgement, then refreshes its owner once", async () => {
  await withPage(async (page) => {
    const posts = [];
    const queueReads = [];
    const savedQueue = structuredClone(QUEUE_DAY);
    savedQueue.grupos[0].turnos.push({ id: "new-91", hora: HOUR, paciente: "Mesa", servicio: "Cardiología", centro: "Hospital", elegible_asistencia: true });
    savedQueue.grupos[0].libres = [];
    const requested = deferred(), release = deferred();
    await page.addInitScript(() => { window.EventSource = undefined; });
    await page.route(`${ORIGIN}/**`, (route) => fulfill(route, { posts, queueReads, savedQueue, holdSave: { requested, release } }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await pickDoctor(page, "ana");
    await page.click('button.queue-give[data-cola-dar="08:30"]');
    await page.fill("#ct-pac", "Mesa");
    await page.click('#ct-pac-sug li[data-nota]');
    queueReads.length = 0;
    try {
      await page.locator("#ct-ok:not([disabled])").click();
      await requested.promise;
      const inFlight = await page.evaluate(() => window.__toasts || []);
      assert.ok(!inFlight.some((t) => /creado/i.test(t)),
        `a save still in flight cannot claim creation, toasts were: ${JSON.stringify(inFlight)}`);
      assert.equal(posts.length, 1, "one create is in flight");
      assert.deepEqual(queueReads, [], "the owner refresh waits for the acknowledgement");
      const saved = page.waitForResponse((r) => r.url().endsWith("/api/admin/appointments"));
      release.release();
      await saved;
      await page.locator("#sec-cola tr.cnt-tr").filter({ hasText: "Mesa" }).waitFor({ state: "visible" });
      const after = await page.evaluate(() => window.__toasts || []);
      assert.ok(after.some((t) => /Turno creado/.test(t)), "the acknowledged save announces the created turno");
      assert.equal(posts.length, 1);
      assert.deepEqual(queueReads, [DAY], "exactly one current-owner refresh");
      assert.equal(await page.locator("#ct-back").count(), 0);
    } finally {
      release.release();
    }
  });
});

for (const body of [
  { name: "ok false", spec: { body: { ok: false } } },
  { name: "empty object", spec: { body: {} } },
  { name: "malformed json", spec: { body: '{"ok":' } },
  { name: "truthy nonboolean ok", spec: { body: { ok: "yes" } } },
  { name: "numeric ok", spec: { body: { ok: 1 } } },
  { name: "null", spec: { body: "null" } },
  { name: "array", spec: { body: [] } },
]) {
  test(`An HTTP 200 save without body ok true never announces creation or refreshes (${body.name})`, async () => {
    await withPage(async (page) => {
      const posts = [];
      const queueReads = [];
      await page.addInitScript(() => { window.EventSource = undefined; });
      await page.route(`${ORIGIN}/**`, (route) => fulfill(route, { posts, queueReads, saveResponse: { status: 200, ...body.spec } }));
      await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
      await pickDoctor(page, "ana");
      await page.click('button.queue-give[data-cola-dar="08:30"]');
      await page.fill("#ct-pac", "Mesa");
      await page.click('#ct-pac-sug li[data-nota]');
      queueReads.length = 0;
      const saved = page.waitForResponse((r) => r.url().endsWith("/api/admin/appointments"));
      await page.locator("#ct-ok:not([disabled])").click();
      await saved;
      await page.locator("#ct-err").waitFor({ state: "visible" });
      const toasts = await page.evaluate(() => window.__toasts || []);
      assert.ok(!toasts.some((t) => /creado/i.test(t)),
        `an unacknowledged save cannot claim creation, toasts were: ${JSON.stringify(toasts)}`);
      assert.deepEqual(queueReads, [], "onCreated/owner refresh must not run without body ok true");
      assert.equal(posts.length, 1, "no retry on an unacknowledged body");
      assert.match(await page.locator("#ct-back").innerText(), /Mesa/, "retain the operator's entry");
      if (body.name !== "ok false") {
        assert.match(await page.locator("#ct-err").innerText(), /No se pudo confirmar el turno/,
          "a malformed acknowledgement cannot prove that the server rejected the write");
      }
    });
  });
}

test("A lost save response reports uncertainty without retrying or refreshing", async () => {
  await withPage(async page => {
    const posts = [], queueReads = [];
    await page.addInitScript(() => { window.EventSource = undefined; });
    await page.route(`${ORIGIN}/**`, route => fulfill(route, { posts, queueReads, abortSave: true }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await pickDoctor(page, "ana");
    await page.click('button.queue-give[data-cola-dar="08:30"]');
    await page.fill("#ct-pac", "Mesa");
    await page.click('#ct-pac-sug li[data-nota]');
    queueReads.length = 0;
    await page.locator("#ct-ok:not([disabled])").click();
    await page.locator("#ct-err").waitFor({ state: "visible" });
    assert.match(await page.locator("#ct-err").innerText(), /No se pudo confirmar el turno/);
    assert.match(await page.locator("#ct-back").innerText(), /Mesa/);
    assert.equal(posts.length, 1);
    assert.deepEqual(queueReads, []);
    assert.ok(!(await page.evaluate(() => window.__toasts)).some(t => /creado|No se guardó/.test(t)));
  });
});

test("An in-flight save ignores repeat submits and posts exactly once", async () => {
  await withPage(async (page) => {
    const posts = [];
    const queueReads = [];
    const requested = deferred(), release = deferred();
    await page.addInitScript(() => { window.EventSource = undefined; });
    await page.route(`${ORIGIN}/**`, (route) => fulfill(route, { posts, queueReads, holdSave: { requested, release } }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await pickDoctor(page, "ana");
    await page.click('button.queue-give[data-cola-dar="08:30"]');
    await page.fill("#ct-pac", "Mesa");
    await page.click('#ct-pac-sug li[data-nota]');
    await page.locator("#ct-ok:not([disabled])").waitFor();
    queueReads.length = 0;
    try {
      // Three synchronous submits in one task: the first owns the save, the
      // repeats hit whatever guard the overlay keeps (disabled node, detached
      // node or the in-flight guard) — never a second POST.
      await page.evaluate(() => {
        const btn = document.getElementById("ct-ok");
        btn.click(); btn.click(); btn.click();
      });
      await requested.promise;
      await page.evaluate(() => {
        const btn = document.getElementById("ct-ok");
        if (btn) { btn.click(); btn.click(); }
      });
      assert.equal(posts.length, 1, "repeat submits during the flight must not post again");
      const saved = page.waitForResponse((r) => r.url().endsWith("/api/admin/appointments"));
      release.release();
      await saved;
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(posts.length, 1, "the single save settles without a duplicate POST");
      assert.deepEqual(queueReads, [DAY], "the one accepted save refreshes its owner once");
    } finally {
      release.release();
    }
  });
});

test("Initial SSE reconciliation preserves a held create intent for the SSR day", async () => {
  await withPage(async page => {
    const posts = [], queueReads = [];
    const requested = deferred(), release = deferred();
    await page.addInitScript(() => {
      window.EventSource = class extends EventTarget {
        constructor() { super(); window.__queueStream = this; }
        close() {}
      };
    });
    await page.route(`${ORIGIN}/**`, route => fulfill(route, { posts, queueReads, holdModule: { requested, release } }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await pickDoctor(page, "ana");
    await page.click('button.queue-give[data-cola-dar="08:30"]');
    await requested.promise;
    const refreshed = page.waitForResponse(r => r.url().includes("/api/admin/day-queue?"));
    await page.evaluate(() => window.__queueStream.dispatchEvent(new Event("ready")));
    await refreshed;
    await page.locator('button.queue-give[data-cola-dar="08:30"]').waitFor({ state: "visible" });
    release.release();
    await page.locator("#ct-pac").waitFor({ state: "visible" });
    assert.deepEqual(queueReads, [DAY]);
    assert.equal(await page.locator("#ct-back").count(), 1);
  });
});

test("Queue holds an early doctor/day/hour click and opens once with the latest intent", async () => {
  await withPage(async (page) => {
    const requested = deferred();
    const release = deferred();
    const posts = [];
    const requestedPaths = [];
    await page.route(`${ORIGIN}/**`, (route) => fulfill(route, {
      holdModule: { requested, release }, posts, requestedPaths,
    }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await page.locator("#adm-h1").waitFor({ state: "visible" });
    await page.waitForFunction(() => (document.getElementById("adm-h1") || {}).textContent);
    // The native boot contract, not a synthetic one: the DOCUMENT owns the
    // fingerprinted stylesheet and it really applies, the module owns every
    // action node below, and the classic controller never loads.
    const sheet = await page.evaluate(() => {
      const link = document.getElementById("cola-css");
      const controls = document.querySelector(".queue-controls");
      return {
        href: link ? link.href : "",
        applied: !!(link && link.sheet && link.sheet.cssRules.length > 0),
        display: controls ? getComputedStyle(controls).display : "",
      };
    });
    assert.match(sheet.href, /\/static\/platform\/admin-queue\.[a-f0-9]{8}\.css$/);
    assert.ok(sheet.applied, "the document-owned queue stylesheet loads and parses");
    assert.equal(sheet.display, "flex");
    await page.locator('button.queue-doctor-link[data-cola-doctor="ana"]').first().waitFor({ state: "visible" });
    await pickDoctor(page, "ana");
    await page.click('button.queue-give[data-cola-dar="08:30"]');
    await within(requested.promise, "request of the overlay module");
    await pickDoctor(page, "bruno");
    await page.locator('button.queue-give[data-cola-dar="09:00"]').waitFor({ state: "visible" });
    await page.click('button.queue-give[data-cola-dar="09:00"]');
    const delayed = await page.evaluate(() => ({
      overlay: document.querySelectorAll("#ct-back").length,
      alerts: document.querySelectorAll("[data-crear-turno-error]").length,
      legacy: typeof window.SanaCreateAppointment,
      spinning: /Cargando…/.test(document.body.innerText),
    }));
    assert.equal(delayed.overlay, 0, "overlay must not open while the module is held");
    assert.equal(delayed.alerts, 0);
    assert.equal(delayed.legacy, "undefined");
    assert.equal(delayed.spinning, false);
    release.release();
    await page.locator("#ct-back").waitFor({ state: "attached" });
    await page.locator("#ct-pac").waitFor({ state: "visible" });
    await page.fill("#ct-pac", "Mesa");
    await page.locator('#ct-pac-sug li[data-nota]').waitFor({ state: "visible" });
    await page.click('#ct-pac-sug li[data-nota]');
    await page.locator('[data-cambiar="medico"]').waitFor({ state: "visible" });
    await page.locator(".ct-prop-cuando").waitFor({ state: "visible" });
    const opened = await page.evaluate(() => ({
      overlays: document.querySelectorAll("#ct-back").length,
      doctor: document.querySelector('[data-cambiar="medico"]')
        ? document.querySelector('[data-cambiar="medico"]').closest(".ct-elegido").querySelector("b").textContent
        : "",
      hour: (document.querySelector(".ct-prop-cuando") || {}).textContent || "",
    }));
    assert.equal(opened.overlays, 1);
    assert.match(opened.doctor, /Bruno/);
    assert.match(opened.hour, /09:00/);
    assert.ok(requestedPaths.includes(QUEUE_MODULE_URL), "the native queue module itself was imported");
    assert.ok(!requestedPaths.includes("/admin-queue.js"), "the classic controller stays retired");
  });
});

test("Queue keeps a held free-slot intent when its disposable button is regenerated", async () => {
  await withPage(async (page) => {
    const requested = deferred();
    const release = deferred();
    const posts = [];
    await page.route(`${ORIGIN}/**`, (route) => fulfill(route, {
      holdModule: { requested, release }, posts,
    }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await page.locator("#adm-h1").waitFor({ state: "visible" });
    await page.waitForFunction(() => (document.getElementById("adm-h1") || {}).textContent);
    await page.locator('button.queue-doctor-link[data-cola-doctor="ana"]').first().waitFor({ state: "visible" });
    await pickDoctor(page, "ana");
    const giveButton = await page.locator('button.queue-give[data-cola-dar="08:30"]').elementHandle();
    await page.click('button.queue-give[data-cola-dar="08:30"]');
    await within(requested.promise, "request of the overlay module");
    // This is the module's own regeneration path: reselecting the same doctor
    // re-renders the list and replaces the very button that holds the intent.
    // The still-mounted screen owns the intent, not the button node.
    await pickDoctor(page, "ana");
    await page.waitForFunction((node) => !node.isConnected, giveButton);
    await page.locator('button.queue-give[data-cola-dar="08:30"]').waitFor({ state: "visible" });
    release.release();
    await page.locator("#ct-pac").waitFor({ state: "visible" });
    await page.fill("#ct-pac", "Mesa");
    await page.locator('#ct-pac-sug li[data-nota]').waitFor({ state: "visible" });
    await page.click('#ct-pac-sug li[data-nota]');
    await page.locator('[data-cambiar="medico"]').waitFor({ state: "visible" });
    const doctor = await page.locator('[data-cambiar="medico"]').locator("xpath=ancestor::div[contains(@class, 'ct-elegido')]").locator("b").innerText();
    assert.match(doctor, /Ana/);
  });
});

test("Queue does not reopen a held doctor intent after the operator changes doctor", async () => {
  await withPage(async (page) => {
    const requested = deferred();
    const release = deferred();
    const posts = [];
    await page.route(`${ORIGIN}/**`, (route) => fulfill(route, {
      holdModule: { requested, release }, posts,
    }));
    await page.goto(ORIGIN + "/admin/cola", { waitUntil: "domcontentloaded" });
    await page.locator("#adm-h1").waitFor({ state: "visible" });
    await page.waitForFunction(() => (document.getElementById("adm-h1") || {}).textContent);
    await page.locator('button.queue-doctor-link[data-cola-doctor="ana"]').first().waitFor({ state: "visible" });
    await pickDoctor(page, "ana");
    await page.click("#adm-crear-turno");
    await within(requested.promise, "request of the overlay module");
    await pickDoctor(page, "bruno");
    release.release();
    await page.waitForFunction(() => window.__createAppointmentModuleEvaluated === true);
    await stays(async () => await page.locator("#ct-back").count() === 0, "a changed selected doctor keeps the held old create intent from opening", 50);
    assert.equal(await page.locator("#ct-back").count(), 0,
      "a changed selected doctor cancels the held old create intent");
  });
});

test("Requests rejected import shows localized recovery without duplicate handlers", async () => {
  await withPage(async (page) => {
    let abortModule = true;
    const posts = [];
    await page.route(`${ORIGIN}/**`, (route) => fulfill(route, { abortModule, posts }));
    await page.goto(ORIGIN + "/admin/solicitudes", { waitUntil: "domcontentloaded" });
    await page.locator("#adm-crear-turno").waitFor({ state: "visible" });
    await page.click("#adm-crear-turno");
    await page.locator("[data-crear-turno-error]").waitFor({ state: "attached" });
    const first = await page.evaluate(() => ({
      alerts: document.querySelectorAll("[data-crear-turno-error]").length,
      retries: document.querySelectorAll("[data-crear-turno-retry]").length,
      overlay: document.querySelectorAll("#ct-back").length,
      spinning: /Cargando…/.test(document.body.innerText),
      copy: (document.querySelector("[data-crear-turno-error]") || {}).textContent || "",
      listeners: window.getEventListeners ? null : document.querySelectorAll("[data-crear-turno-retry]").length,
    }));
    assert.equal(first.overlay, 0);
    assert.equal(first.alerts, 1);
    assert.equal(first.retries, 1);
    assert.equal(first.spinning, false);
    assert.match(first.copy, /No se pudo cargar/);
    assert.match(first.copy, /Recargar/);
    await page.click("[data-crear-turno-retry]");
    await page.click("[data-crear-turno-retry]");
    const still = await page.evaluate(() => ({
      alerts: document.querySelectorAll("[data-crear-turno-error]").length,
      retries: document.querySelectorAll("[data-crear-turno-retry]").length,
      overlay: document.querySelectorAll("#ct-back").length,
    }));
    assert.equal(still.alerts, 1, "retry must reuse the existing delegate, not stack recovery nodes");
    assert.equal(still.retries, 1);
    assert.equal(still.overlay, 0);
    abortModule = false;
    await page.evaluate((url) => {
      window.SANA_STATIC["platform/admin-create-appointment.mjs"] = url;
    }, MODULE_RETRY_PATH);
    await page.click("[data-crear-turno-retry]");
    await page.locator("#ct-back").waitFor({ state: "attached" });
    assert.equal(await page.locator("#ct-back").count(), 1);
  });
});

test("Desk DNI readers load the fingerprinted platform pair and apply canonical scanner data", async () => {
  await withPage(async (page) => {
    const posts = [];
    const altas = [];
    const requestedPaths = [];
    const scannerRequested = deferred();
    const barcodeRequested = deferred();
    await page.route(`${ORIGIN}/**`, (route) => fulfill(route, {
      posts,
      altas,
      requestedPaths,
      barcodeResults: [{ text: DNI_PAYLOAD }],
      onRequest(path) {
        if (path === DNI_SCAN_URL) scannerRequested.release();
        if (path === DNI_BARCODE_URL) barcodeRequested.release();
      },
    }));
    await page.goto(ORIGIN + "/admin/solicitudes", { waitUntil: "domcontentloaded" });
    await page.click("#adm-crear-turno");
    await page.locator("#ct-pac").waitFor({ state: "visible" });
    await within(scannerRequested.promise, "request of the fingerprinted DNI scanner");
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    // A document with no patient record opens the new-patient form with
    // everything the document says: nothing is retyped, nothing is dropped.
    const leido = () => page.evaluate(() => ["dni", "apellido", "nombre", "nac", "sexo"]
      .map((k) => document.querySelector("#ct-alta-" + k).value));
    await page.locator("#ct-pac").fill(DNI_PAYLOAD);
    await page.locator("#ct-alta-dni").waitFor({ state: "visible" });
    assert.deepEqual(await leido(), ["12345678", "Perez", "Ana Maria", "01/02/1990", "F"],
      "keyboard wedge fills every field HSI asks for");
    await page.click('[data-cambiar="paciente"]');
    await page.locator("#ct-pac").waitFor({ state: "visible" });
    await page.locator("#ct-scan-file").setInputFiles({
      name: "dni-frente.jpg", mimeType: "image/jpeg", buffer: Buffer.from("fixture image"),
    });
    await within(barcodeRequested.promise, "request of the fingerprinted DNI barcode decoder");
    await page.locator("#ct-alta-dni").waitFor({ state: "visible" });
    assert.deepEqual(await leido(), ["12345678", "Perez", "Ana Maria", "01/02/1990", "F"],
      "photo decoder fills the same fields");
    await page.click("#ct-alta-ok");
    await page.locator(".ct-elegido").waitFor({ state: "visible" });
    assert.deepEqual(altas, [{ dni: "12345678", apellido: "Perez", nombre: "Ana Maria", nacimiento: "1990-02-01", sexo: "F" }],
      "the patient is created once, with the document's data");
    assert.match(await page.locator(".ct-elegido").innerText(), /Perez, Ana Maria/);
    assert.deepEqual(requestedPaths.filter((path) => /dni-(?:scan|barcode)/.test(path)), [DNI_SCAN_URL, DNI_BARCODE_URL],
      "only the two manifest-published fingerprint URLs may load the DNI pair");
    assert.ok(!requestedPaths.includes("/dni-scan.mjs"));
    assert.ok(!requestedPaths.includes("/dni-barcode.mjs"));
    assert.ok(!requestedPaths.includes("/static/platform/dni-scan.mjs"));
    assert.ok(!requestedPaths.includes("/static/platform/dni-barcode.mjs"));
  });
});

// Launch plan P4 (hospital map, doctor m07): a doctor's session filled the
// new-patient form and «Crear paciente» answered with the bare code
// «rol_no_autorizado». The server's refusal stays; the desk reads why and
// what to do, and a human sentence from the server still reaches it as is.
// One page for the three refusals: a refused sign-up keeps the form as typed,
// so each refusal is the next «Crear» after the previous one — its own POST,
// its own words. (A page per refusal ran this test into its 30 s under load.)
test("A refused new patient reads why and what to do, never the server's code", async () => {
  await withPage(async (page) => {
    const altas = [];
    const altaRefused = {};
    await page.route(`${ORIGIN}/**`, (route) => fulfill(route, { posts: [], altas, altaRefused }));
    await page.goto(ORIGIN + "/admin/solicitudes", { waitUntil: "domcontentloaded" });
    await page.click("#adm-crear-turno");
    await page.fill("#ct-pac", "30999002");
    await page.click("#ct-pac-sug li[data-alta]");
    await page.fill("#ct-alta-apellido", "Prueba");
    await page.fill("#ct-alta-nombre", "Rosa");
    await page.fill("#ct-alta-nac", "02/07/1975");
    await page.selectOption("#ct-alta-sexo", "F");
    const cases = [
      [{ status: 403, body: { error: "rol_no_autorizado" } }, /Anotar/],
      [{ status: 409, body: { error: "Ya existe un paciente con ese DNI." } }, /Ya existe un paciente con ese DNI\./],
      [{ status: 400, body: { error: "algo_inesperado" } }, /No se pudo guardar/],
    ];
    for (const [i, [refusal, expected]] of cases.entries()) {
      Object.assign(altaRefused, refusal);
      await page.click("#ct-alta-ok");
      // «Crear» clears the previous message before it posts, so once THIS POST
      // is in, the message that shows is this refusal's.
      await until(() => altas.length === i + 1, `${refusal.body.error}: the sign-up goes out`);
      await page.locator("#ct-alta-err:not([hidden])").waitFor();
      const shown = await page.locator("#ct-alta-err").innerText();
      assert.equal(altas.length, i + 1, `${refusal.body.error}: the form posted once per «Crear»`);
      assert.equal(altas[i].dni, "30999002");
      assert.doesNotMatch(shown, /^[a-z_]+$|rol_no_autorizado|algo_inesperado/, "never a bare server code");
      assert.match(shown, expected);
    }
  });
});

test("Unreadable DNI photos preserve a manual patient entry", async () => {
  await withPage(async (page) => {
    const posts = [];
    await page.route(`${ORIGIN}/**`, (route) => fulfill(route, { posts, barcodeResults: [] }));
    await page.goto(ORIGIN + "/admin/solicitudes", { waitUntil: "domcontentloaded" });
    await page.click("#adm-crear-turno");
    await page.locator("#ct-pac").waitFor({ state: "visible" });
    await page.locator("#ct-pac").fill("Entrada manual");
    await page.locator("#ct-scan-file").setInputFiles({
      name: "dni-ilegible.jpg", mimeType: "image/jpeg", buffer: Buffer.from("unreadable fixture"),
    });
    await page.waitForFunction(() =>
      document.querySelector("#ct-scan-msg")?.textContent.includes("No se pudo leer el código del DNI"));
    assert.match(await page.locator("#ct-scan-msg").innerText(), /No se pudo leer el código del DNI/);
    assert.equal(await page.locator("#ct-pac").inputValue(), "Entrada manual");
  });
});

test("Requests synthetic create posts the appointments API and refreshes the owner", async () => {
  await withPage(async (page) => {
    const posts = [];
    await page.route(`${ORIGIN}/**`, (route) => fulfill(route, { posts }));
    await page.goto(ORIGIN + "/admin/solicitudes", { waitUntil: "domcontentloaded" });
    await page.evaluate(() => {
      window.__owner = { solicitudes: 0, cola: 0, calendario: 0 };
      const wrap = (name, key) => {
        const prev = window[name];
        window[name] = function () {
          window.__owner[key] += 1;
          if (typeof prev === "function") return prev.apply(this, arguments);
        };
      };
      wrap("sanaShowSolicitudes", "solicitudes");
      wrap("sanaRouteCola", "cola");
      wrap("sanaRecargarCalendarioMedico", "calendario");
    });
    await page.click("#adm-crear-turno");
    await page.locator("#ct-pac").waitFor({ state: "visible" });
    await page.fill("#ct-pac", "Mesa");
    await page.locator('#ct-pac-sug li[data-nota]').waitFor({ state: "visible" });
    await page.click('#ct-pac-sug li[data-nota]');
    await page.locator("#ct-svc").waitFor({ state: "visible" });
    await page.fill("#ct-svc", "cardio");
    await page.locator('#ct-svc-sug li[data-svc]').waitFor({ state: "visible" });
    await page.click('#ct-svc-sug li[data-svc]');
    await page.locator("#ct-ok:not([disabled])").waitFor({ state: "visible" });
    await page.click("#ct-ok");
    await page.waitForFunction(() => (window.__owner || {}).solicitudes >= 1);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].servicio_id, SERVICE.id);
    assert.equal(posts[0].nota, "Mesa");
    assert.equal(posts[0].dia, DAY);
    assert.ok(["08:30", "09:00"].includes(posts[0].hora));
    const owner = await page.evaluate(() => window.__owner);
    assert.equal(owner.solicitudes, 1);
    assert.equal(await page.locator("#ct-back").count(), 0);
    assert.equal(await page.evaluate(() => typeof window.SanaCreateAppointment), "undefined");
  });
});

test("Doctors calendar early click keeps doctor/day and opens once on release", async () => {
  await withPage(async (page) => {
    const requested = deferred();
    const release = deferred();
    const posts = [];
    await page.route(`${ORIGIN}/**`, (route) => fulfill(route, {
      holdModule: { requested, release }, posts,
    }));
    await page.goto(ORIGIN + "/admin/doctors?item=ana", {
      waitUntil: "domcontentloaded",
    });
    await page.locator('[data-tab="calendario"]').waitFor({ state: "attached" });
    await page.click('[data-tab="calendario"]');
    await page.locator("[data-med-crear]").waitFor({ state: "visible" });
    await page.click("[data-med-crear]");
    await within(requested.promise, "request of the overlay module");
    const delayed = await page.evaluate(() => ({
      overlay: document.querySelectorAll("#ct-back").length,
      legacy: typeof window.SanaCreateAppointment,
    }));
    assert.equal(delayed.overlay, 0);
    assert.equal(delayed.legacy, "undefined");
    release.release();
    await page.locator("#ct-back").waitFor({ state: "attached" });
    await page.locator("#ct-pac").waitFor({ state: "visible" });
    await page.fill("#ct-pac", "Mesa");
    await page.locator('#ct-pac-sug li[data-nota]').waitFor({ state: "visible" });
    await page.click('#ct-pac-sug li[data-nota]');
    await page.locator('[data-cambiar="medico"]').waitFor({ state: "visible" });
    const opened = await page.evaluate(() => ({
      overlays: document.querySelectorAll("#ct-back").length,
      doctor: document.querySelector('[data-cambiar="medico"]')
        ? document.querySelector('[data-cambiar="medico"]').closest(".ct-elegido").querySelector("b").textContent
        : "",
    }));
    assert.equal(opened.overlays, 1);
    assert.match(opened.doctor, /Ana/);
  });
});

for (const disposed of [false, true]) {
  test(`Doctor calendar save refresh is owned: disposed=${disposed}`, async () => {
    await withPage(async (page) => {
      const posts = [], doctorReads = [];
      const requested = deferred(), release = deferred();
      await page.route(`${ORIGIN}/**`, route => fulfill(route, {
        posts, doctorReads, holdSave: { requested, release },
      }));
      await page.goto(ORIGIN + "/admin/doctors?item=ana", { waitUntil: "domcontentloaded" });
      await page.click('[data-tab="calendario"]');
      await page.click('[data-med-crear]');
      await page.fill("#ct-pac", "Mesa");
      await page.click('#ct-pac-sug li[data-nota]');
      await page.locator("#ct-ok:not([disabled])").click();
      await requested.promise;
      await page.evaluate((dispose) => {
        window.__legacyCalendarCalls = 0;
        window.sanaRecargarCalendarioMedico = () => { window.__legacyCalendarCalls++; };
        if (dispose) window.__catalogOwner();
      }, disposed);
      doctorReads.length = 0;
      const saved = page.waitForResponse(r => r.url().endsWith("/api/admin/appointments"));
      release.release();
      await saved;
      if (!disposed) {
        await page.locator("#sec-medicos tr").filter({ hasText: "Mesa" }).waitFor({ state: "visible" });
      }
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(posts.length, 1, "one accepted mutation, never a retry");
      assert.equal(doctorReads.length, disposed ? 0 : 1, "only the current owner refreshes");
      assert.equal(await page.evaluate(() => window.__legacyCalendarCalls), 0, "retired global must not be called");
    });
  });
}

test("Doctor replacement clears the previous calendar query", async () => {
  await withPage(async page => {
    await page.route(`${ORIGIN}/**`, route => fulfill(route, { posts: [], manyDoctorRows: true }));
    await page.goto(ORIGIN + "/admin/doctors?item=ana", { waitUntil: "domcontentloaded" });
    await page.click('[data-tab="calendario"]');
    await page.fill('[data-med-filter="q"]', "Only Ana");
    assert.equal(await page.locator("#med-cal tr.cnt-tr").count(), 0);
    await page.evaluate(() => {
      history.pushState({}, "", "/admin/doctors?item=bruno");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await page.locator("#adm-h1").filter({ hasText: "Bruno" }).waitFor();
    await page.click('[data-tab="calendario"]');
    await page.locator('[data-med-filter="q"]').waitFor();
    assert.equal(await page.inputValue('[data-med-filter="q"]'), "");
    await page.locator("#med-cal tr.cnt-tr").filter({ hasText: "Paciente Bruno" }).waitFor({ state: "visible" });
  });
});

for (const calendar of [false, true]) for (const ending of ['recover', 'navigate', 'dispose']) {
  test(`Catalog visible retry owns ${calendar ? 'calendar' : 'list'} ${ending}`, async () => {
    await withPage(async page => {
      const held = deferred(), requested = deferred();
      let hits = 0;
      const retrySelector = calendar ? '[data-catalog-calendar-retry]' : '[data-catalog-route-retry]';
      await page.route(`${ORIGIN}/**`, route => fulfill(route, { posts: [] }));
      const pattern = calendar ? `${ORIGIN}/api/admin/doctors/ana/appointments?*` : `${ORIGIN}/api/admin/content/doctors`;
      await page.route(pattern, async route => {
        hits++;
        if (hits <= 2) return route.fulfill({ status: 503, json: { error: 'temporarily unavailable' } });
        requested.release();
        await held.promise;
        await route.fallback();
      });
      await page.goto(ORIGIN + (calendar ? '/admin/doctors?item=ana' : '/admin/doctors'));
      if (calendar) {
        await page.click('[data-tab="info"]');
        await page.fill('#med-nombre-pila', 'Unsaved draft');
        await page.click('[data-tab="calendario"]');
      }
      // The page default, not a short bound: the retry shows on the failed read
      // itself (no delay in the controller), so 2.5 s only measured the load.
      await page.locator(retrySelector).waitFor({ state: 'visible' });
      assert.equal(await page.locator(retrySelector).innerText(), 'Recargar', 'Spanish mode-off fallback');
      await page.evaluate(async () => {
        const i18n = await import(window.SANA_STATIC['platform/i18n.mjs']);
        i18n.setCatalog({ 'admin.content.common.reload': 'Reload' });
      });
      await page.locator(retrySelector).click();
      await page.waitForFunction(selector => !document.querySelector(selector)?.disabled, retrySelector);
      assert.equal(await page.locator(retrySelector).innerText(), 'Reload', 'fresh retry consumes the canonical catalog');
      assert.equal(hits, 2, 'second failure leaves exactly one usable retry');
      await page.evaluate(selector => {
        const button = document.querySelector(selector);
        window.__oldRetry = button;
        button.click(); button.click();
      }, retrySelector);
      await requested.promise;
      assert.equal(hits, 3, 'rapid retry clicks issue only one read');
      if (ending === 'navigate') {
        await page.evaluate(() => {
          history.pushState({}, '', '/admin/doctors?item=bruno');
          window.dispatchEvent(new PopStateEvent('popstate'));
        });
      } else if (ending === 'dispose') {
        await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide')));
      }
      held.release();
      if (ending === 'recover') {
        if (calendar) {
          await page.locator('#med-cal tr.cnt-tr').filter({ hasText: 'Paciente' }).waitFor();
          await page.click('[data-tab="info"]');
          assert.equal(await page.inputValue('#med-nombre-pila'), 'Unsaved draft');
          assert.equal(await page.locator('#med-cal-grid').count(), 1);
        } else await page.locator('#cnt-tbody-medicos tr').first().waitFor();
        assert.equal(await page.locator(retrySelector).count(), 0);
      } else if (ending === 'navigate') {
        await page.locator('#adm-h1').filter({ hasText: 'Bruno' }).waitFor();
        assert.equal(await page.locator(retrySelector).count(), 0);
      } else {
        // The mounted owner is gone; a retained button cannot emit another read.
        await page.evaluate(() => { window.__oldRetry.disabled = false; window.__oldRetry.click(); });
        await stays(() => hits === 3, "a retained retry button emits no further read", 100);
        assert.equal(hits, 3);
      }
    });
  });
}

for (const failure of [false, true]) {
  test(`Catalog save settles across navigation without stale cache or cancellation toast: failure=${failure}`, async () => {
    await withPage(async page => {
      const mutation = { posts: [], requested: deferred(), release: deferred(), failure };
      await page.route(`${ORIGIN}/**`, route => fulfill(route, { posts: [], catalogMutation: mutation }));
      await page.goto(ORIGIN + "/admin/doctors?item=ana", { waitUntil: "domcontentloaded" });
      await page.click('[data-tab="info"]');
      await page.fill("#med-nombre-pila", "Actualizada");
      await page.click('button[form="cnt-form"][type="submit"]');
      await mutation.requested.promise;
      await page.evaluate(() => {
        history.pushState({}, "", "/admin/doctors?item=bruno");
        window.dispatchEvent(new PopStateEvent("popstate"));
      });
      mutation.release.release();
      await page.locator("#adm-h1").filter({ hasText: "Bruno" }).waitFor();
      await page.evaluate(() => {
        history.pushState({}, "", "/admin/doctors?item=ana");
        window.dispatchEvent(new PopStateEvent("popstate"));
      });
      await page.locator("#adm-h1").filter({ hasText: "Pérez" }).waitFor();
      await page.click('[data-tab="info"]');
      assert.equal(await page.inputValue("#med-nombre-pila"), failure ? "Ana" : "Actualizada", "revisited data must reflect the actual server outcome");
      assert.equal(mutation.posts.length, 1, "navigation must not retry the mutation");
      const toasts = await page.evaluate(() => window.__toasts || []);
      assert.ok(!toasts.some(text => /owner is no longer|CancelledOwnerWork/.test(text)), "internal cancellation must never be shown as save failure");
    });
  });
}

test("New doctor keeps its selected photo after navigation during the create acknowledgement", async () => {
  await withPage(async page => {
    const create = { posts: [], photos: [], requested: deferred(), release: deferred() };
    await page.route(`${ORIGIN}/**`, route => fulfill(route, { posts: [], catalogCreate: create }));
    await page.goto(ORIGIN + "/admin/doctors?item=new", { waitUntil: "domcontentloaded" });
    await page.click('[data-tab="info"]');
    await page.fill("#med-nombre-pila", "Nueva");
    await page.fill("#med-apellido", "Doctora");
    await page.setInputFiles("#med-foto-input", { name: "portrait.png", mimeType: "image/png", buffer: Buffer.from("photo") });
    await page.waitForFunction(() => document.querySelector("#med-foto-av")?.style.backgroundImage.includes("data:"));
    await page.click('button[form="cnt-form"][type="submit"]');
    await create.requested.promise;
    await page.evaluate(() => {
      history.pushState({}, "", "/admin/doctors?item=bruno");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    const photoRequest = page.waitForRequest(request => request.url().endsWith("/api/admin/doctors/created-doctor/photo"));
    create.release.release();
    await photoRequest;
    await page.locator("#adm-h1").filter({ hasText: "Bruno" }).waitFor();
    assert.equal(create.posts.length, 1, "navigation cannot repeat the entity create");
    assert.equal(create.photos.length, 1, "the accepted create owns one dependent photo upload");
    assert.equal(create.photos[0].filename, "portrait.png");
  });
});

for (const ending of ["deadline", "dispose", "photo-success", "photo-timeout"]) {
  test(`Catalog create and dependent photo respect ${ending}`, async () => {
    await withPage(async page => {
      await page.addInitScript(() => {
        const realFetch = window.fetch.bind(window);
        const realTimeout = window.setTimeout.bind(window);
        const realClearTimeout = window.clearTimeout.bind(window);
        window.__mutationTimers = new Map();
        window.__photoPosts = 0;
        window.setTimeout = (fn, ms, ...args) => {
          if (ms === 15000) window.__fireMutationDeadline = fn;
          const id = realTimeout(fn, ms, ...args);
          if (ms === 15000) window.__mutationTimers.set(id, fn);
          return id;
        };
        window.clearTimeout = id => {
          window.__mutationTimers.delete(id);
          return realClearTimeout(id);
        };
        window.fetch = (url, init) => {
          if (String(url) === "/api/admin/content/doctors" && init?.method === "POST") {
            // Deliberately ignore abort: a submitted server write may still
            // acknowledge after the client ends its ownership.
            return new Promise(resolve => {
              window.__resolveCreate = () => resolve(new Response(JSON.stringify({
                ok: true, item: { id: "created-doctor", ...JSON.parse(init.body) },
              }), { status: 200, headers: { "Content-Type": "application/json" } }));
            });
          }
          if (String(url).endsWith("/created-doctor/photo") && init?.method === "POST") {
            window.__photoPosts++;
            return new Promise(resolve => {
              window.__resolvePhoto = () => resolve(new Response('{"ok":true}', { status: 200 }));
            });
          }
          return realFetch(url, init);
        };
      });
      await page.route(`${ORIGIN}/**`, route => fulfill(route, { posts: [] }));
      await page.goto(ORIGIN + "/admin/doctors?item=new", { waitUntil: "domcontentloaded" });
      await page.click('[data-tab="info"]');
      await page.fill("#med-nombre-pila", "Nueva");
      await page.fill("#med-apellido", "Doctora");
      await page.setInputFiles("#med-foto-input", { name: "portrait.png", mimeType: "image/png", buffer: Buffer.from("photo") });
      await page.waitForFunction(() => document.querySelector("#med-foto-av")?.style.backgroundImage.includes("data:"));
      await page.click('button[form="cnt-form"][type="submit"]');
      await page.waitForFunction(() => typeof window.__resolveCreate === "function");
      if (ending.startsWith("photo-")) {
        await page.evaluate(() => window.__resolveCreate());
        await page.waitForFunction(() => window.__photoPosts === 1);
        assert.equal(await page.evaluate(() => window.__mutationTimers.size), 1,
          "the acknowledged create must release its deadline while the photo owns its own deadline");
        await page.evaluate(ending => {
          if (ending === "photo-timeout") Array.from(window.__mutationTimers.values())[0]();
          else window.__resolvePhoto();
        }, ending);
        await page.waitForFunction(ending => ending === 'photo-timeout'
          ? (window.__toasts || []).some(text => /la foto no se pudo subir/.test(text))
          : (window.__toasts || []).includes('Guardado.'), ending);
        const toasts = await page.evaluate(() => window.__toasts);
        assert.ok(!toasts.some(text => /outcome is unknown|No se pudo confirmar el guardado/.test(text)), "confirmed create is never reported unknown");
        assert.equal(toasts.some(text => /la foto no se pudo subir/.test(text)), ending === "photo-timeout");
        assert.equal(await page.evaluate(() => window.__mutationTimers.size), 0, "settled work releases every deadline");
        return;
      }
      await page.evaluate(ending => {
        if (ending === "dispose") window.__catalogOwner();
        else window.__fireMutationDeadline();
      }, ending);
      if (ending === "deadline") {
        await page.waitForFunction(() => (window.__toasts || []).some(text => /outcome is unknown|No se pudo confirmar el guardado/.test(text)));
      }
      await page.evaluate(async () => {
        window.__resolveCreate();
        // A task boundary drains the resolved Response/body microtasks. The
        // synthetic dependent fetch increments synchronously if invoked.
        await new Promise(resolve => setTimeout(resolve, 0));
      });
      assert.equal(await page.evaluate(() => window.__photoPosts), 0,
        "late acknowledgement cannot initiate a new mutation after ownership ended");
    });
  });
}

for (const ending of ['newer-selection', 'clear-selection', 'new-editor', 'dispose', 'existing-navigation']) {
  test(`Catalog retained photo reader respects ${ending}`, async () => {
    await withPage(async page => {
      await page.addInitScript(() => {
        window.__readers = [];
        window.__photoEmitted = 0;
        window.FileReader = class {
          readAsDataURL(file) { this.file = file; window.__readers.push(this); }
          abort() { this.aborted = true; }
        };
        const nativeFetch = window.fetch.bind(window);
        window.fetch = (url, init) => {
          if (String(url).endsWith('/photo') && init?.method === 'POST') window.__photoEmitted++;
          return nativeFetch(url, init);
        };
      });
      await page.route(`${ORIGIN}/**`, route => fulfill(route, { posts: [] }));
      const existing = ['dispose', 'existing-navigation'].includes(ending);
      await page.goto(ORIGIN + `/admin/doctors?item=${existing ? 'ana' : 'new'}`, { waitUntil: 'domcontentloaded' });
      await page.click('[data-tab="info"]');
      const pick = name => page.setInputFiles('#med-foto-input', { name, mimeType: 'image/png', buffer: Buffer.from(name) });
      await pick('first.png');
      if (ending === 'newer-selection') {
        await pick('second.png');
        await page.evaluate(() => {
          const second = window.__readers[1]; second.result = 'data:image/png;base64,Qg=='; second.onload?.();
        });
      } else if (ending === 'clear-selection') {
        await page.setInputFiles('#med-foto-input', []);
      } else if (ending === 'dispose') {
        await page.evaluate(() => window.__catalogOwner());
      } else {
        await page.evaluate(() => {
          history.pushState({}, '', '/admin/doctors?item=bruno');
          window.dispatchEvent(new PopStateEvent('popstate'));
        });
        await page.locator('#adm-h1').filter({ hasText: 'Bruno' }).waitFor();
        if (ending === 'new-editor') {
          await page.evaluate(() => {
            history.pushState({}, '', '/admin/doctors?item=new');
            window.dispatchEvent(new PopStateEvent('popstate'));
          });
          await page.locator('#cnt-form').waitFor();
          await page.waitForFunction(() => document.querySelector('#med-nombre-pila')?.value === '');
        }
      }
      const result = await page.evaluate(() => {
        const first = window.__readers[0]; first.result = 'data:image/png;base64,QQ=='; first.onload?.();
        return { posts: window.__photoEmitted, avatar: document.querySelector('#med-foto-av')?.style.backgroundImage || '' };
      });
      assert.equal(result.posts, 0, 'a retained read cannot start a departed-owner mutation');
      if (ending === 'newer-selection') assert.match(result.avatar, /Qg==/, 'last selected photo wins even if old read finishes last');
      else assert.ok(!result.avatar.includes('QQ=='), 'departed or cleared photo cannot paint a fresh editor');
    });
  });
}

for (const payload of ['', '{}', '{"ok":"true"}', 'null', '[]', '{"ok":false}']) {
  test(`Catalog pending photo requires typed acknowledgement: ${JSON.stringify(payload)}`, async () => {
    await withPage(async page => {
      const create = { posts: [], photos: [], requested: deferred(), release: deferred() };
      create.release.release();
      await page.route(`${ORIGIN}/**`, route => {
        if (route.request().url().endsWith('/created-doctor/photo') && route.request().method() === 'POST') {
          create.photos.push(route.request().postDataJSON());
          return route.fulfill({ status: 200, contentType: 'application/json', body: payload });
        }
        return fulfill(route, { posts: [], catalogCreate: create });
      });
      await page.goto(ORIGIN + '/admin/doctors?item=new', { waitUntil: 'domcontentloaded' });
      await page.click('[data-tab="info"]');
      await page.fill('#med-nombre-pila', 'Nueva'); await page.fill('#med-apellido', 'Doctora');
      await page.setInputFiles('#med-foto-input', { name: 'portrait.png', mimeType: 'image/png', buffer: Buffer.from('photo') });
      await page.waitForFunction(() => document.querySelector('#med-foto-av')?.style.backgroundImage.includes('data:'));
      await page.click('button[form="cnt-form"][type="submit"]');
      await page.waitForFunction(() => location.pathname === '/admin/doctors' && !location.search);
      assert.equal(create.posts.length, 1); assert.equal(create.photos.length, 1);
      const toasts = await page.evaluate(() => window.__toasts || []);
      assert.match(toasts.at(-1) || '', /la foto no se pudo subir/, 'partial success warning must not be erased by a generic saved toast');
    });
  });
}

test("Doctor request modal is removed with its owning route", async () => {
  await withPage(async page => {
    await page.route(`${ORIGIN}/**`, route => fulfill(route, {
      posts: [], requestRows: [{ id: "request-1", nombre: "Synthetic", apellido: "Patient", dni: "1", estado: "aprobada" }],
    }));
    await page.goto(ORIGIN + "/admin/doctors?item=ana", { waitUntil: "domcontentloaded" });
    await page.click('[data-tab="calendario"]');
    await page.click('[data-solicitud="request-1"]');
    await page.locator(".solmod-back").waitFor({ state: "visible" });
    await page.evaluate(() => {
      history.pushState({}, "", "/admin/doctors?item=bruno");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await page.locator("#adm-h1").filter({ hasText: "Bruno" }).waitFor();
    assert.equal(await page.locator(".solmod-back").count(), 0, "old patient overlay cannot cover a new doctor");
  });
});

for (const action of ["close", "escape", "dispose"]) {
  test(`Doctor request modal releases its owner on ${action}`, async () => {
    await withPage(async page => {
      await page.route(`${ORIGIN}/**`, route => fulfill(route, {
        posts: [], requestRows: [{ id: "request-1", nombre: "Synthetic", apellido: "Patient", dni: "1", estado: "aprobada" }],
      }));
      await page.goto(ORIGIN + "/admin/doctors?item=ana", { waitUntil: "domcontentloaded" });
      await page.click('[data-tab="calendario"]');
      await page.click('[data-solicitud="request-1"]');
      await page.locator(".solmod-back").waitFor();
      if (action === "close") await page.click(".solmod-x");
      if (action === "escape") await page.keyboard.press("Escape");
      if (action === "dispose") await page.evaluate(() => window.__catalogOwner());
      assert.equal(await page.locator(".solmod-back").count(), 0);
      if (action !== "dispose") {
        // Cached data still opens a fresh modal; releasing the old claim must
        // neither disable its replacement nor leave an extra body overlay.
        await page.click('[data-solicitud="request-1"]');
        await page.locator(".solmod-back").waitFor();
        assert.equal(await page.locator(".solmod-back").count(), 1);
        await page.keyboard.press("Escape");
        assert.equal(await page.locator(".solmod-back").count(), 0);
      }
    });
  });
}

// Médico = usuario (the product owner, 2026-10-19): the Doctors list is the doctor-users
// list, «Invitar médico» needs no field, and «Nuevo médico» says what happened
// to the account it created. Synthetic boundary HTTP, real controllers.
const INVITE_LINK = "https://create-appt-native.test/admin/reset?token=fixture";
async function routeDoctorAccounts(page, state) {
  await page.route(`${ORIGIN}/**`, async route => {
    const request = route.request(), pathname = new URL(request.url()).pathname, method = request.method();
    if (pathname === "/api/admin/content/doctors" && method === "GET") return jsonOk(route, { items: state.doctors });
    if (pathname === "/api/admin/users" && method === "GET") return jsonOk(route, { items: state.accounts });
    if (pathname === "/data/especialidades.json") return jsonOk(route, { specialties: [{ nombre: "Cardiología", cluster: "cardiologia" }] });
    if (pathname === "/api/admin/content/doctors" && method === "POST") {
      const body = request.postDataJSON();
      state.creates.push(body);
      const item = { id: state.createdId, nombre: [body.nombre_pila, body.apellido].filter(Boolean).join(" "), incompleto: true, centros: [], ...body };
      delete item._invitar;
      state.doctors = [...state.doctors, item];
      state.accounts = [...state.accounts, { usuario: state.createdUser, rol: "medico", medico_id: state.createdId, ...state.createdAccount }];
      return jsonOk(route, { ok: true, item, cuenta: { usuario: state.createdUser, ...state.cuenta } });
    }
    if (pathname === "/api/admin/users/actions/invite") {
      state.invites.push(request.postDataJSON());
      state.accounts = state.accounts.map(a => a.medico_id === state.createdId ? { ...a, activo: true, cuenta_estado: "invitado" } : a);
      return jsonOk(route, { ok: true, resultados: [{ ok: true, medico_id: state.createdId, usuario: state.createdUser, email_enviado: false, enlace: INVITE_LINK }] });
    }
    return fulfill(route, { posts: [] });
  });
}

function doctorAccountState(overrides = {}) {
  return {
    doctors: [
      { id: "ana", nombre: DOCTOR_A, especialidad: "Cardiología", centro: "hospital", centros: [{ centro: "hospital" }] },
      { id: "medico-ab12", nombre: "", incompleto: true, centros: [] },
    ],
    accounts: [
      { usuario: "ana.perez", rol: "medico", medico_id: "ana", activo: true, cuenta_estado: "perfil_completo" },
      { usuario: "medico.nuevo", rol: "medico", medico_id: "medico-ab12", activo: true, cuenta_estado: "invitado" },
      { usuario: "mesa", rol: "admisionista", activo: true },
    ],
    creates: [], invites: [],
    createdId: "nueva-doctora", createdUser: "nueva.doctora",
    createdAccount: { activo: true, cuenta_estado: "invitado" },
    cuenta: { cuenta_estado: "invitado", activo: true, email_enviado: false, enlace: INVITE_LINK },
    ...overrides,
  };
}

async function doctorRow(page, text) {
  return page.locator("#cnt-tbody-medicos tr").filter({ hasText: text });
}

test("Doctors list shows each doctor's account and marks a doctor still off the site", async () => {
  await withPage(async page => {
    await routeDoctorAccounts(page, doctorAccountState());
    await page.goto(ORIGIN + "/admin/doctors", { waitUntil: "domcontentloaded" });
    await page.locator("#cnt-tbody-medicos tr").nth(1).waitFor();
    assert.equal(await page.locator('#cnt-thead-medicos th[data-col="cuenta"]').textContent(), "Cuenta");
    assert.equal(await page.locator('#cnt-thead-medicos th[data-col="login"]').count(), 0);
    assert.equal(await (await doctorRow(page, DOCTOR_A)).locator('td[data-col="cuenta"]').textContent(), "Perfil completo");
    const nameless = await doctorRow(page, "Médico sin nombre");
    assert.equal(await nameless.locator('td[data-col="cuenta"]').textContent(), "Invitado");
    assert.match(await nameless.locator('td[data-col="nombre"]').textContent(), /Médico sin nombre · Todavía no está en el sitio/);
    assert.doesNotMatch(await (await doctorRow(page, DOCTOR_A)).locator('td[data-col="nombre"]').textContent(), /sitio/);
  });
});

test("Invitar médico creates the doctor with every field optional and hands back the link to copy", async () => {
  await withPage(async page => {
    const state = doctorAccountState();
    await routeDoctorAccounts(page, state);
    await page.goto(ORIGIN + "/admin/doctors", { waitUntil: "domcontentloaded" });
    await page.locator("#med-invite-open").waitFor();
    assert.equal(await page.locator("#med-invite").isVisible(), false);
    await page.click("#med-invite-open");
    assert.equal(await page.locator("#med-invite").isVisible(), true);
    assert.equal(await page.locator("#med-invite [required]").count(), 0, "no field is obligatory");
    await page.fill("#med-inv-nombre", "Nueva");
    await page.selectOption("#med-inv-especialidad", "Cardiología");
    await page.click('#med-invite-form button[type="submit"]');
    await page.locator("#med-invite-result .doctor-user-link-input").waitFor();
    assert.deepEqual(state.creates, [{ _invitar: true, nombre_pila: "Nueva", especialidad: "Cardiología" }]);
    assert.equal(await page.inputValue("#med-invite-result .doctor-user-link-input"), INVITE_LINK);
    // Without an email the doctor logs in with the username: the admin passes both.
    assert.match(await page.locator("#med-invite-result").textContent(), /Usuario\s*nueva\.doctora/);
    assert.equal(await page.inputValue("#med-inv-nombre"), "", "the form is ready for the next doctor");
    await (await doctorRow(page, "Nueva")).waitFor();
    assert.equal(await (await doctorRow(page, "Nueva")).locator('td[data-col="cuenta"]').textContent(), "Invitado");
    // The save drops the catalog caches: the refreshed list still names the center.
    assert.equal(await (await doctorRow(page, DOCTOR_A)).locator('td[data-col="centro"]').textContent(), "Hospital");
    await page.evaluate(() => {
      window.__copied = [];
      window.ClipboardItem = undefined;
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: text => { window.__copied.push(text); return Promise.resolve(); } } });
    });
    await page.click("#med-invite-result .doctor-user-link-copy");
    await page.waitForFunction(() => window.__copied.length === 1);
    assert.deepEqual(await page.evaluate(() => window.__copied), [INVITE_LINK]);
  });
});

test("Invitar médico with an email says the invitation went to that address", async () => {
  await withPage(async page => {
    const state = doctorAccountState({ cuenta: { cuenta_estado: "invitado", activo: true, email_enviado: true, email: "nueva@example.test" } });
    await routeDoctorAccounts(page, state);
    await page.goto(ORIGIN + "/admin/doctors", { waitUntil: "domcontentloaded" });
    await page.click("#med-invite-open");
    await page.fill("#med-inv-email", "nueva@example.test");
    await page.press("#med-inv-email", "Enter");
    await page.locator("#med-invite-result .doctor-user-outcome").waitFor();
    assert.deepEqual(state.creates, [{ _invitar: true, email: "nueva@example.test" }]);
    assert.equal(await page.locator("#med-invite-result").textContent(), "Invitación enviada a nueva@example.test.");
    assert.equal(await page.locator("#med-invite-result .doctor-user-link-input").count(), 0);
  });
});

test("Nuevo médico saves with no field filled, stays on the doctor and offers Invitar for its new account", async () => {
  await withPage(async page => {
    const state = doctorAccountState({
      createdAccount: { activo: false, cuenta_estado: "sin_invitar" },
      cuenta: { cuenta_estado: "sin_invitar", activo: false },
    });
    await routeDoctorAccounts(page, state);
    await page.goto(ORIGIN + "/admin/doctors?item=new", { waitUntil: "domcontentloaded" });
    await page.locator('button[form="cnt-form"][type="submit"]').waitFor();
    assert.equal(await page.locator("#cnt-form [required]").count(), 0, "no client-side required field");
    await page.click('button[form="cnt-form"][type="submit"]');
    await page.locator("#med-account-invite").waitFor();
    assert.equal(state.creates.length, 1);
    assert.equal(new URL(page.url()).searchParams.get("item"), "nueva-doctora");
    assert.match(await page.locator("#med-account-outcome").textContent(), /Se creó su cuenta nueva\.doctora, todavía sin invitar\./);
    // Where the admin is looking: above the editor's tabs, in view, focused.
    const outcomeBox = await stableBox(page.locator("#med-account-outcome"), "account outcome");
    const formBox = await stableBox(page.locator("#cnt-form"), "doctor form");
    assert.ok(outcomeBox.y < formBox.y, "the outcome sits above the doctor form");
    assert.equal(await page.locator("#med-account-outcome").isVisible(), true);
    assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.id), "med-account-outcome");
    await page.click("#med-account-invite");
    await page.locator("#med-account-outcome .doctor-user-link-input").waitFor();
    assert.deepEqual(state.invites, [{ medico_ids: ["nueva-doctora"] }]);
    assert.equal(await page.inputValue("#med-account-outcome .doctor-user-link-input"), INVITE_LINK);
  });
});

// Cargar una lista de médicos (Lira 13.10.2026, «creación masiva de usuarios»): the one path
// for a roster since 2026-11-03. The server reads the list and previews it without writing;
// «Crear N médicos» sends the same list again to create. Here the server is a stub: these
// pin what the screen sends and shows (the import itself: test_doctor_import.py).
async function routeDoctorImport(page, state, answer) {
  state.imports = [];
  await page.route(`${ORIGIN}/api/admin/doctors/import**`, async route => {
    const request = route.request(), url = new URL(request.url());
    const call = {
      dryRun: url.searchParams.get("dry_run"), filename: url.searchParams.get("filename"),
      body: (request.postDataBuffer() || Buffer.alloc(0)).toString("utf8"),
    };
    state.imports.push(call);
    const [status, body] = answer(call, state);
    return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  });
}

const IMPORT_LIST = "Nombre;Apellido;Email\nLucía;Gómez;lucia@example.test\nAna;Pérez;ana@example.test\nMal;Correo;mal-correo";

test("Cargar lista previews the list without writing, then creates what the preview showed", async () => {
  await withPage(async page => {
    const state = doctorAccountState();
    await routeDoctorAccounts(page, state);
    await routeDoctorImport(page, state, (call, s) => {
      const lucia = { fila: 2, nombre: "Lucía Gómez", email: "lucia@example.test", especialidad: "", centro: "" };
      const others = [
        { fila: 3, nombre: "Ana Pérez", email: "ana@example.test", especialidad: "", centro: "", estado: "existente", detalle: "Ya está entre los médicos; no se modifica." },
        { fila: 4, nombre: "Mal Correo", email: "mal-correo", especialidad: "", centro: "", estado: "error", detalle: "Email inválido." },
      ];
      if (call.dryRun === "true") {
        return [200, { dry_run: true, validas: 1, creadas: 0, existentes: 1, errores: 1,
          rows: [{ ...lucia, estado: "crear", detalle: "Crear médico y enviar el acceso." }, ...others] }];
      }
      s.doctors = [...s.doctors, { id: "lucia-gomez", nombre: "Lucía Gómez", incompleto: true, centros: [] }];
      return [200, { dry_run: false, validas: 1, creadas: 1, existentes: 1, errores: 1,
        rows: [{ ...lucia, estado: "creado", detalle: "Médico creado. Entregale este enlace de acceso.", enlace: INVITE_LINK }, ...others] }];
    });
    await page.goto(ORIGIN + "/admin/doctors", { waitUntil: "domcontentloaded" });
    // Its own toolbar button and panel: the list is not inside «Invitar médico».
    await page.click("#med-import-open");
    assert.equal(await page.locator("#med-import-open").getAttribute("aria-expanded"), "true");
    assert.equal(await page.locator("#med-invite").isVisible(), false);
    assert.equal(await page.locator("#med-invite #med-import-text").count(), 0);
    // The browser's own «Choose File / No file chosen» stays hidden behind a labelled button.
    assert.equal(await page.locator("#med-import-file").getAttribute("class"), "cnt-foto-file");
    assert.equal(await page.locator('label[for="med-import-file"]').textContent(), "Elegir archivo");
    await page.fill("#med-import-text", IMPORT_LIST);
    await page.click("#med-import-run");
    await page.locator("#med-import-result .med-import-table").waitFor();
    assert.deepEqual(state.imports, [{ dryRun: "true", filename: "lista.txt", body: IMPORT_LIST }]);
    assert.deepEqual(state.creates, [], "the preview creates nobody");
    assert.equal(await page.locator("#med-import-result .med-import-summary").textContent(),
      "1 médico para crear, 1 ya está entre los médicos, 1 con error.");
    // Columns no row has a value for are not drawn; the line number is, because a row failed.
    assert.deepEqual(await page.locator("#med-import-result th").allTextContents(), ["Fila", "Nombre", "Email", "Resultado"]);
    assert.equal(await page.locator('#med-import-result tr[data-state="error"]').count(), 1);
    assert.equal(await page.locator("#med-import-run").isVisible(), false);
    assert.equal(await page.locator("#med-import-create").textContent(), "Crear 1 médico");
    await page.click("#med-import-create");
    await page.locator("#med-import-result .med-import-link").waitFor();
    assert.deepEqual(state.imports[1], { dryRun: "false", filename: "lista.txt", body: IMPORT_LIST });
    assert.equal(await page.locator("#med-import-result .med-import-summary").textContent(),
      "1 médico creado, 1 ya estaba, 1 con error.");
    assert.equal(await page.inputValue("#med-import-result .med-import-link"), INVITE_LINK);
    await (await doctorRow(page, "Lucía Gómez")).waitFor();
    assert.equal(await page.locator("#med-import-create").isVisible(), false);
  });
});

test("Cargar lista sends a chosen Excel file as it is and says what the server refused", async () => {
  await withPage(async page => {
    const state = doctorAccountState();
    await routeDoctorAccounts(page, state);
    await routeDoctorImport(page, state, () => [400, { error: "Falta la columna del nombre o del email." }]);
    // The old /admin/users/import lands here (?import=1) with the list open and in focus.
    await page.goto(ORIGIN + "/admin/doctors?import=1", { waitUntil: "domcontentloaded" });
    await page.locator("#med-import-text").waitFor();
    assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.id), "med-import-text");
    await page.fill("#med-import-text", "texto que el Excel reemplaza");
    await page.setInputFiles("#med-import-file", { name: "medicos.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("PK-fake-xlsx") });
    assert.equal(await page.inputValue("#med-import-text"), "");
    assert.equal(await page.locator("#med-import-file-name").textContent(), "medicos.xlsx");
    await page.click("#med-import-run");
    await page.locator('#med-import-result [role="alert"]').waitFor();
    assert.deepEqual(state.imports, [{ dryRun: "true", filename: "medicos.xlsx", body: "PK-fake-xlsx" }]);
    assert.equal(await page.locator('#med-import-result [role="alert"]').textContent(), "Falta la columna del nombre o del email.");
    assert.equal(await page.locator("#med-import-create").isVisible(), false);
    assert.equal(await page.locator("#med-import-run").isEnabled(), true);
  });
});
