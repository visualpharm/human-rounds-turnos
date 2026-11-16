// The HSI extension's panel of changes on «Pacientes de hoy»: the REAL content
// script and stylesheet of extensions/hsi-humanizador against the real ASGI app,
// its real GET /api/admin/day-queue/changes and POST …/changes/<id>/hide. Only
// the Chrome extension API is stubbed: storage lives in the page's localStorage
// (so it survives a reload, as chrome.storage does) and the service worker's
// answers are handed to the script's listener by the test.
//
// 2026-11-05, the desk of the municipal installation: three turnos cancelled in
// the panel stayed in the box for good — two of them of days already past, which
// HSI no longer changes («HSI no deja cancelar este turno»), and nothing closed
// the box. What has passed never enters it; a refusal leaves an «Ocultar» that
// holds for the whole desk and survives a reload.
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { test, expect, loginAdmin, saveShot, ROOT } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_hsi_changes", hsiChanges: true } });
test.describe.configure({ mode: "serial" });

const EXT = join(ROOT, "extensions", "hsi-humanizador");
const SHARED_JS = readFileSync(join(EXT, "lib", "shared.js"), "utf8");
const CONTENT_JS = readFileSync(join(EXT, "content-turnos.js"), "utf8");
const CONTENT_CSS = readFileSync(join(EXT, "content-turnos.css"), "utf8");
const SHOTS = join(homedir(), ".claude/artifacts/pinamar-turnos/hsi-cancel-banner-1105/e2e");
const DESKTOP = { width: 1440, height: 900 };

// What this computer remembers of the three turnos it loaded into HSI: their
// place before the panel cancelled them (the fixture's day and hour).
function registroDe(cambios) {
  const registro = {};
  for (const c of Object.values(cambios)) registro[c.id] = { dia: c.dia, hora: c.hora, medico: "", visto: Date.now() };
  return registro;
}

// chrome.* for one browser context: storage kept in localStorage, messages to
// the service worker recorded, the script's message listener exposed.
async function stubChrome(context, registro) {
  await context.addInitScript((registro) => {
    const KEY = "__hrStorage";
    const leer = () => JSON.parse(localStorage.getItem(KEY) || "null");
    if (!leer()) localStorage.setItem(KEY, JSON.stringify({ turnosEnHsi: registro, turnosCargados: {}, modo: "boton" }));
    window.__hrSent = [];
    window.chrome = {
      runtime: {
        id: "stub",
        sendMessage: (msg) => { window.__hrSent.push(msg); return Promise.resolve({ ok: true }); },
        onMessage: { addListener: (fn) => { window.__hrListen = fn; } },
      },
      storage: {
        local: {
          get: (pedido) => {
            const s = leer() || {};
            const out = {};
            for (const k of Object.keys(pedido || {})) out[k] = k in s ? s[k] : pedido[k];
            return Promise.resolve(out);
          },
          set: (v) => { localStorage.setItem(KEY, JSON.stringify(Object.assign(leer() || {}, v))); return Promise.resolve(); },
        },
        onChanged: { addListener: () => {} },
      },
    };
  }, registro);
}

// What the manifest does on /admin/*, after every load.
async function injectExtension(page) {
  await page.addStyleTag({ content: CONTENT_CSS });
  await page.addScriptTag({ content: SHARED_JS });
  await page.addScriptTag({ content: CONTENT_JS });
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.hrHumanizador)).toBe("1");
}

const box = (page) => page.locator(".hr-hsi-cambios");
const row = (page, paciente) => box(page).locator(".hr-hsi-cambio", { hasText: paciente });

test("lo cancelado de un día que ya pasó no entra en el cuadro; lo que viene sí", async ({ page, app, baseURL }, testInfo) => {
  const cambios = app.fixture.hsi_changes;
  await page.setViewportSize(DESKTOP);
  await stubChrome(page.context(), registroDe(cambios));
  await loginAdmin(page, baseURL, undefined, undefined, "/admin/cola");
  await injectExtension(page);

  await expect(row(page, cambios.rechazado.paciente)).toBeVisible();
  await expect(row(page, cambios.siguiente.paciente)).toBeVisible();
  await expect(row(page, cambios.rechazado.paciente).getByRole("button", { name: "Cancelar en HSI…" })).toBeVisible();
  await expect(row(page, cambios.pasado.paciente)).toHaveCount(0);
  await expect(box(page).locator(".hr-hsi-cambio")).toHaveCount(2);
  await saveShot(testInfo, box(page), join(SHOTS, "1-sin-lo-pasado-1440.png"));
});

test("un rechazo de HSI deja «Ocultar»: no vuelve al recargar ni en otra computadora de la mesa", async ({ page, app, baseURL, newContext }, testInfo) => {
  const cambios = app.fixture.hsi_changes;
  const rechazado = cambios.rechazado;
  await page.setViewportSize(DESKTOP);
  await stubChrome(page.context(), registroDe(cambios));
  await loginAdmin(page, baseURL, undefined, undefined, "/admin/cola");
  await injectExtension(page);

  const fila = row(page, rechazado.paciente);
  await fila.getByRole("button", { name: "Cancelar en HSI…" }).click();
  await expect.poll(() => page.evaluate(() => window.__hrSent.filter((m) => m.tipo === "cargar").map((m) => m.turno.accion)))
    .toEqual(["cancelar"]);
  await page.evaluate((id) => window.__hrListen({ tipo: "estado", id, estado: "error", detalle: "HSI no deja cancelar este turno." }),
    String(rechazado.id));
  await expect(fila.locator(".hr-hsi-cambio-error")).toHaveText("HSI no deja cancelar este turno.");
  const ocultar = fila.getByRole("button", { name: "Ocultar" });
  await expect(ocultar).toBeVisible();
  // The rows still waiting for their first press offer no «Ocultar».
  await expect(row(page, cambios.siguiente.paciente).getByRole("button", { name: "Ocultar" })).toHaveCount(0);
  await saveShot(testInfo, box(page), join(SHOTS, "2-rechazo-con-ocultar-1440.png"));

  const oculto = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/admin/day-queue/changes/" + rechazado.id + "/hide"
    && r.request().method() === "POST");
  await ocultar.click();
  expect((await oculto).status()).toBe(200);
  await expect(fila).toHaveCount(0);
  await expect(row(page, cambios.siguiente.paciente)).toBeVisible();

  // Reload: this computer's mark and the panel's both keep it out.
  await page.reload();
  await injectExtension(page);
  await expect(row(page, cambios.siguiente.paciente)).toBeVisible();
  await expect(row(page, rechazado.paciente)).toHaveCount(0);
  await expect(row(page, cambios.pasado.paciente)).toHaveCount(0);
  await saveShot(testInfo, box(page), join(SHOTS, "3-tras-recargar-1440.png"));

  // Another computer of the desk, which never hid anything: the panel says so.
  const otra = await newContext({ viewport: DESKTOP });
  await stubChrome(otra, registroDe(cambios));
  const otraPage = await otra.newPage();
  await loginAdmin(otraPage, baseURL, undefined, undefined, "/admin/cola");
  await injectExtension(otraPage);
  await expect(row(otraPage, cambios.siguiente.paciente)).toBeVisible();
  await expect(row(otraPage, rechazado.paciente)).toHaveCount(0);
});
