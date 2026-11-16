// The window HSI opens on a turno, tidied by the extension (lib/hsi-tidy.js +
// content-hsi.css): the patient's name is the link, a datum HSI lacks is not
// drawn, the rest of the clutter goes — and the controls the extension drives
// stay. The fixture is HSI's own markup of a turno of a fictional patient,
// captured from the local HSI test copy. Runs in a real Chromium; skips when
// there is none (like `npm run e2e:dev`).
import { test, ceil } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const EXT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../extensions/hsi-humanizador");
const leer = (f) => fs.readFileSync(path.join(EXT, f), "utf8");
const TIDY = leer("lib/hsi-tidy.js");
const CSS = leer("content-hsi.css");
// HSI's avatar falls back to a local image on error; offline that retries forever.
const DIALOGO = leer("test/hsi-turno-dialog.html").replace(/ onerror="[^"]*"/g, "").replace(/ src="assets[^"]*"/g, "");

let browser = null;
try {
  browser = await require("playwright").chromium.launch();
} catch (e) {
  browser = null;
}
const skip = browser ? false : "no Chromium available";

async function abrir(html, { vigilar = false } = {}) {
  const page = ceil(await browser.newPage({ viewport: { width: 1200, height: 900 } }));
  await page.setContent("<!doctype html><body><mat-dialog-container>" + (vigilar ? "" : html) + "</mat-dialog-container></body>");
  await page.addStyleTag({ content: CSS });
  await page.addScriptTag({ content: TIDY });
  if (vigilar) {
    await page.evaluate(() => self.HRTidy.vigilar());
    await page.evaluate((h) => { document.querySelector("mat-dialog-container").innerHTML = h; }, html);
    await page.waitForFunction(() => document.querySelector("#patient-profile-action[data-hr-oculto]"));
  } else {
    await page.evaluate(() => self.HRTidy.ordenar(document.querySelector("app-appointment")));
  }
  return page;
}

const visible = (page, sel) => page.locator(sel).first().isVisible();
const textoVisible = (page) => page.locator("app-appointment").innerText();

test("sin cobertura ni teléfono: el nombre es el enlace y no queda ningún «sin dato»", { skip }, async () => {
  const page = await abrir(DIALOGO);
  const t = await textoVisible(page);
  assert.match(t, /Omar\s+Pruebacuatro/);
  assert.match(t, /DNI 90100104/);
  for (const sobra of [/ID\s*4/, /Ir a Paciente/i, /Editar informaci/i, /Sin cobertura/i, /Sin informaci/i, /Cobertura/, /Tel[eé]fono/, /Presencial/]) {
    assert.doesNotMatch(t, sobra, String(sobra));
  }
  assert.equal(await visible(page, "#patient-profile-action"), false);
  assert.equal(await page.locator("#window").getAttribute("data-hr-sin-datos"), "");
  // The turno itself and the controls the extension drives stay.
  assert.match(t, /28 De Octubre/);
  assert.match(t, /15:00/);
  assert.equal(await visible(page, "#cancel-appointment"), true);
  assert.equal(await page.locator("section#state button.mat-stroked-button").evaluateAll((b) => b.filter((x) => x.offsetParent).length), 4);
  await page.close();
});

test("el nombre abre al paciente por el propio botón de HSI, con el mouse y con Enter", { skip }, async () => {
  const page = await abrir(DIALOGO);
  await page.evaluate(() => {
    self.__abierto = 0;
    document.querySelector("#patient-profile-action").addEventListener("click", () => { self.__abierto++; });
  });
  await page.locator("#personal-data .info .title").click();
  await page.locator("#personal-data .info .title").press("Enter");
  assert.equal(await page.evaluate(() => self.__abierto), 2);
  assert.equal(await page.locator("#personal-data .info .title").getAttribute("role"), "link");
  await page.close();
});

test("con cobertura y teléfono se muestran; solo «editar» se va; una modalidad distinta se queda", { skip }, async () => {
  const conDatos = DIALOGO
    .replace("Sin cobertura", "OSDE 210")
    .replace(/<span([^>]*)class="mat-body patient-no-info ([^"]*)"([^>]*)>\s*Sin información\s*<\/span>/, '<span$1class="mat-body $2"$3>2254 15-401234</span>')
    .replace("Presencial", "Virtual");
  assert.match(conDatos, /OSDE 210/);
  assert.match(conDatos, /2254 15-401234/);
  const page = await abrir(conDatos);
  const t = await textoVisible(page);
  assert.match(t, /Cobertura Médica\s+OSDE 210/);
  assert.match(t, /Teléfono\s+2254 15-401234/);
  assert.match(t, /Virtual/);
  assert.doesNotMatch(t, /Editar informaci/i);
  assert.equal(await page.locator("#window").getAttribute("data-hr-sin-datos"), null);
  await page.close();
});

test("con solo uno de los dos datos se muestra solo ese", { skip }, async () => {
  const soloTelefono = DIALOGO.replace(/<span([^>]*)class="mat-body patient-no-info ([^"]*)"([^>]*)>\s*Sin información\s*<\/span>/, '<span$1class="mat-body $2"$3>2254 15-401234</span>');
  const page = await abrir(soloTelefono);
  const t = await textoVisible(page);
  assert.match(t, /Teléfono\s+2254 15-401234/);
  assert.doesNotMatch(t, /Cobertura|Sin cobertura/);
  await page.close();
});

test("se aplica solo cuando HSI abre la ventana, y repetirlo no cambia nada", { skip }, async () => {
  const page = await abrir(DIALOGO, { vigilar: true });
  const antes = await textoVisible(page);
  await page.evaluate(() => { self.HRTidy.ordenar(document.querySelector("app-appointment")); self.HRTidy.ordenar(document.querySelector("app-appointment")); });
  assert.equal(await textoVisible(page), antes);
  assert.doesNotMatch(antes, /Sin cobertura|ID\s*4/);
  await page.close();
});

test("solo se oculta: ningún elemento de HSI se quita del DOM", { skip }, async () => {
  assert.doesNotMatch(TIDY, /\.remove\(|removeChild|innerHTML|outerHTML|insertAdjacent/);
  assert.doesNotMatch(TIDY, /console\./);
  const page = await abrir(DIALOGO);
  const ids = await page.evaluate(() => ["patient-profile-action", "print-appointment", "cancel-appointment", "close-dialog", "state"].map((i) => !!document.getElementById(i)));
  assert.deepEqual(ids, [true, true, true, true, true]);
  await page.close();
});

test.after(async () => { if (browser) await browser.close(); });
