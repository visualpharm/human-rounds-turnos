// The panel's one download button (static/platform/admin-table.mjs): what a
// small table sends to POST /api/admin/table-export is exactly what the screen
// shows — the visible columns in order, the visible rows, the day as a column
// when the screen groups by day, the patients for the access log — and the
// server's refusal reaches the desk as its own sentence. Real Chromium, the
// real module; the server is routed.
import { test, within, hookOptions, openPage } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (file) => fs.readFileSync(path.join(ROOT, file), "utf8");
const ORIGIN = "https://table-export.test";
const FILES = {
  "/static/platform/admin-table.mjs": "static/platform/admin-table.mjs",
  "/static/platform/admin-preferences.mjs": "static/platform/admin-preferences.mjs",
  "/static/platform/i18n.mjs": "static/platform/i18n.mjs",
};
const STATIC = {
  "platform/admin-table.mjs": "/static/platform/admin-table.mjs",
  "platform/admin-preferences.mjs": "/static/platform/admin-preferences.mjs",
  "platform/i18n.mjs": "/static/platform/i18n.mjs",
};

// Pacientes de hoy in miniature: a day-grouped table, an action column with no
// header, a column the engine hid (empty in every row: the table's tbl-oc-
// class), an icon-only phone, a cut summary, a hidden row, an empty-state row
// and a band row spanning the table; the second day's head is hidden the way
// the phone's cards hide it, and its columns still count.
const QUEUE = `
<main class="adm5-main"><h1>Pacientes de hoy</h1>
<section id="area">
  <style>.tbl-oc-confirmo [data-col=confirmo]{display:none}#day2 thead{display:none}
    .adm-icon{display:inline-block;width:16px;height:16px}</style>
  <h2 data-export-title-part>Dra. Ana Gómez</h2>
  <table class="tbl-oc-confirmo" data-export-group="2026-11-07" data-export-group-head="Día">
    <thead><tr><th>Hora<span aria-hidden="true">▲</span></th><th>Paciente</th><th>Teléfono</th><th data-col="confirmo">Confirmó</th>
      <th><span>Motivo</span><span style="display:none">Motivo y práctica</span><button type="button" aria-label="Qué significa"><span></span></button></th>
      <th data-export-skip>Interno</th><th></th></tr></thead>
    <tbody>
      <tr data-export-dni="30111222">
        <td>09:00</td>
        <td>Juan Pérez<br><span>DNI 30.111.222</span></td>
        <td><a href="tel:%2B5492254111222" data-copy="+54 9 2254 111222" aria-label="Llamar"><span class="adm-icon"></span></a>
            <a href="https://wa.me/5492254111222" data-copy="+54 9 2254 111222" aria-label="WhatsApp"><span class="adm-icon"></span></a></td>
        <td data-col="confirmo"></td>
        <td><span>Control</span> <span data-export="Control anual con análisis">Control anual…</span> <button data-export-omit>Ver</button></td>
        <td>x</td>
        <td><button>Atender</button></td>
      </tr>
      <tr data-export-dni="30999888" hidden><td>09:30</td><td>Oculta</td><td></td><td data-col="confirmo"></td><td></td><td></td><td></td></tr>
      <tr><td colspan="7">Pausa 10:00–10:30</td></tr>
      <tr data-export-skip><td>—</td><td>Sin turnos</td><td></td><td data-col="confirmo"></td><td></td><td></td><td></td></tr>
      <tr><td>10:30</td><td data-export-skip colspan="6">Libre <button>Dar este turno</button></td></tr>
      <tr data-export-dni="27555666"><td>11:00</td><td>María López</td><td></td><td data-col="confirmo"></td><td>=1+2</td><td>y</td><td><button>Atender</button></td></tr>
    </tbody>
  </table>
  <table id="day2" class="tbl-oc-confirmo" data-export-group="2026-11-08" data-export-group-head="Día">
    <thead><tr><th>Hora ↑</th><th>Paciente</th><th>Teléfono</th><th data-col="confirmo">Confirmó</th><th>Motivo</th><th data-export-skip>Interno</th><th></th></tr></thead>
    <tbody><tr data-export-dni="20444555"><td>08:15</td><td>Pedro Ruiz</td><td></td><td data-col="confirmo"></td><td>Primera vez</td><td>z</td><td></td></tr></tbody>
  </table>
  <table data-export-skip><thead><tr><th>Resumen</th></tr></thead><tbody><tr><td>no va</td></tr></tbody></table>
</section>
<span id="btn-slot"></span>
</main>`;

const EMPTY = `
<main class="adm5-main"><h1>Servicios</h1>
<section id="area"><table><thead><tr><th>Nombre</th></tr></thead>
<tbody><tr><td colspan="3">No hay servicios.</td></tr></tbody></table></section>
<span id="btn-slot"></span></main>`;

function pageHtml(body) {
  return `<!doctype html><html lang="es"><head><meta charset="utf-8">
<script>window.SANA_STATIC=${JSON.stringify(STATIC)};
window.sanaImportStatic=function(name){return import(window.SANA_STATIC[name]);};
window.toasts=[];window.toast=function(m){window.toasts.push(m);};</script>
</head><body>${body}
<script type="module">
import * as Table from "/static/platform/admin-table.mjs";
window.Table = Table;
document.getElementById("btn-slot").innerHTML = Table.exportHtml({ scope: "#area" });
window.tableReady = true;
</script></body></html>`;
}

// One Chromium per file, a fresh context per page (helpers/ceiling.mjs).
let browser;
test.before(async () => { browser = await chromium.launch(); }, hookOptions());
test.after(async () => { await browser?.close(); });

async function withPage(body, answer, run) {
  const page = await openPage(browser, { viewport: { width: 1280, height: 900 }, acceptDownloads: true });
  const posts = [];
  const failures = [];
  page.on("pageerror", (error) => failures.push(String(error)));
  try {
    await page.route(`${ORIGIN}/**`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/admin/table-export") {
        posts.push(JSON.parse(route.request().postData() || "{}"));
        return answer(route);
      }
      if (url.pathname === "/api/admin/preferences") return route.fulfill({ json: { items: {} } });
      if (FILES[url.pathname]) {
        return route.fulfill({ contentType: "text/javascript; charset=utf-8", body: read(FILES[url.pathname]) });
      }
      if (url.pathname === "/admin/cola") return route.fulfill({ contentType: "text/html; charset=utf-8", body: pageHtml(body) });
      return route.fulfill({ status: 404, body: "" });
    });
    await page.goto(`${ORIGIN}/admin/cola`);
    await page.waitForFunction(() => window.tableReady === true);
    await run(page, posts);
    assert.deepEqual(failures, []);
  } finally {
    await page.context().close();
  }
}

const CSV_OK = (route) => route.fulfill({
  status: 200,
  contentType: "text/csv; charset=utf-8",
  headers: { "Content-Disposition": "attachment; filename=\"export.csv\"; filename*=UTF-8''Pacientes%20de%20hoy%202026-11-07.csv" },
  body: "﻿Hora;Paciente\r\n",
});

test("the button is one icon control labelled Descargar, aimed at its area", async () => {
  await withPage(QUEUE, CSV_OK, async (page) => {
    const btn = page.locator("[data-tbl-export]");
    assert.equal(await btn.count(), 1);
    assert.equal(await btn.getAttribute("data-tbl-export"), "#area");
    assert.equal(await btn.getAttribute("aria-label"), "Descargar");
    assert.equal(await btn.getAttribute("type"), "button");
    assert.match(await btn.getAttribute("class"), /\bcnt-gear-btn\b/);
    assert.equal(await btn.locator(".adm-icon-download").count(), 1);
    const withPart = await page.evaluate(() => window.Table.exportHtml({ scope: "#x", titlePart: "Búsquedas \"top\"", day: "2026-11-07" }));
    assert.match(withPart, /data-tbl-export-title-part="Búsquedas &quot;top&quot;"/);
    assert.match(withPart, /data-tbl-export-day="2026-11-07"/);
  });
});

test("collect: visible columns in order, visible rows, the day as a column, the patients", async () => {
  await withPage(QUEUE, CSV_OK, async (page) => {
    // As on the screen: the engine has measured the tables (and hid what it hides) first.
    await page.waitForFunction(() => [...document.querySelectorAll("#area table:not([data-export-skip])")].every((t) => t._sanaAnchos));
    await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
    const data = await page.evaluate(() => window.Table.collectExport(document.getElementById("area")));
    assert.deepEqual(data.columns, ["Día", "Hora", "Paciente", "Teléfono", "Motivo"]);
    assert.deepEqual(data.rows, [
      ["2026-11-07", "09:00", "Juan Pérez · DNI 30.111.222", "+54 9 2254 111222", "Control Control anual con análisis"],
      ["2026-11-07", "11:00", "María López", "", "=1+2"],
      ["2026-11-08", "08:15", "Pedro Ruiz", "", "Primera vez"],
    ]);
    assert.deepEqual(data.patients, ["30111222", "27555666", "20444555"]);
  });
});

test("click: the POST carries the screen, the title with the doctor, the rows; the file downloads by the server's name", async () => {
  await withPage(QUEUE, CSV_OK, async (page, posts) => {
    const [download] = await Promise.all([
      within(page.waitForEvent("download"), "the CSV download"),
      page.click("[data-tbl-export]"),
    ]);
    assert.equal(download.suggestedFilename(), "Pacientes de hoy 2026-11-07.csv");
    assert.equal(posts.length, 1);
    const sent = posts[0];
    assert.equal(sent.screen, "/admin/cola");
    assert.equal(sent.title, "Pacientes de hoy · Dra. Ana Gómez");
    assert.equal(sent.rows.length, 3);
    assert.deepEqual(sent.patients, ["30111222", "27555666", "20444555"]);
    assert.equal(await page.locator("[data-tbl-export]").isDisabled(), false);
  });
});

test("a refusal shows the server's sentence; nothing to download sends nothing", async () => {
  const tooMany = (route) => route.fulfill({
    status: 413,
    json: { code: "demasiadas_filas", error: "Son 1500 filas y se pueden descargar hasta 1000." },
  });
  await withPage(QUEUE, tooMany, async (page, posts) => {
    await page.click("[data-tbl-export]");
    await page.waitForFunction(() => window.toasts.length > 0);
    assert.deepEqual(await page.evaluate(() => window.toasts), ["Son 1500 filas y se pueden descargar hasta 1000."]);
    assert.equal(posts.length, 1);
  });
  await withPage(EMPTY, CSV_OK, async (page, posts) => {
    await page.click("[data-tbl-export]");
    await page.waitForFunction(() => window.toasts.length > 0);
    assert.deepEqual(await page.evaluate(() => window.toasts), ["No hay filas para descargar."]);
    assert.equal(posts.length, 0);
  });
});
