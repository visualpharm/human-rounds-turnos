/*
 * demo.mjs — a ready-to-click window: Chromium with the extension loaded, a
 * «Pacientes de hoy» list of fictional patients, and the HSI TEST copy
 * (http://localhost:8280) already signed in. Press «Cargar en HSI» on a row.
 *
 *   HSI_ADMIN_PASS=… node extensions/hsi-humanizador/test/demo.mjs [--self-check]
 *
 * The list is the same fixture the end-to-end run uses, served on the demo
 * address: its patients exist in the test HSI, which the real demo's do not.
 * --self-check loads the first row and presses Confirmar once, then leaves
 * the window open. The window stays until this process is stopped.
 */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const aqui = path.dirname(fileURLToPath(import.meta.url));
const EXT = path.resolve(aqui, "..");
const require = createRequire(path.join(process.env.PW_ROOT || path.resolve(EXT, "../.."), "package.json"));
const { chromium } = require("playwright");

const HSI = "http://localhost:8280";
const TURNOS = "https://demo.humanrounds.org";
if (!process.env.HSI_ADMIN_PASS) throw new Error("HSI_ADMIN_PASS is required");

function habil(n) {
  const d = new Date();
  d.setDate(d.getDate() + n);
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1);
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
}

const dia = habil(1);
const COLA = {
  dia, hoy: false, total: 6,
  grupos: [
    { medico_id: "generdemo", medico: "Raúl Generdemo", especialidad: "Medicina general", centro: "Hospital", turnos: [
      { id: 201, dia_iso: dia, hora: "09:00", paciente: "Marta Pruebauno", dni: "90.100.101", sexo: "F", contacto: "2254 15-401234", servicio: "Medicina general" },
      { id: 202, dia_iso: dia, hora: "09:30", paciente: "Héctor Pruebados", dni: "90100102", sexo: "M", contacto: "02254 402345", servicio: "Medicina general" },
      { id: 203, dia_iso: dia, hora: "10:00", paciente: "Lucía Pruebatres", dni: "90100103", sexo: "F", contacto: "lucia.prueba@example.com", servicio: "Medicina general" },
    ] },
    { medico_id: "pediademo", medico: "Dra. Laura Pediademo", especialidad: "Pediatría", centro: "Hospital", turnos: [
      { id: 204, dia_iso: habil(6), hora: "11:00", paciente: "Omar Pruebacuatro", dni: "90100104", sexo: "M", contacto: null, servicio: "Pediatría" },
      { id: 205, dia_iso: habil(6), hora: "11:30", paciente: "Persona Inexistente", dni: "90999999", sexo: "M", contacto: null, servicio: "Pediatría" },
      // A patient the test HSI does not have, with everything the alta needs:
      // the load ends on HSI's own patient screen, alta half-done.
      { id: 206, dia_iso: habil(6), hora: "12:00", paciente: "Prueba Nuevo", dni: "92" + String(Date.now()).slice(-6), sexo: "F", contacto: "2254 409901", nacimiento: "1990-05-01", servicio: "Pediatría" },
    ] },
  ],
};

// Every row carries a birth date except «Persona Inexistente» (205): that
// row shows the question the extension asks at the button.
for (const g of COLA.grupos) for (const t of g.turnos) if (t.id !== 205 && !t.nacimiento) t.nacimiento = "1975-03-12";

const perfil = fs.mkdtempSync(path.join(os.tmpdir(), "turnos-hsi-demo-"));
const ctx = await chromium.launchPersistentContext(perfil, {
  headless: false, viewport: null,
  // A window covered by other windows is treated as hidden and HSI stops
  // drawing; keep it live so the demo works wherever the window sits.
  args: ["--window-size=1440,900", "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
    "--disable-features=CalculateNativeWinOcclusion","--disable-extensions-except=" + EXT, "--load-extension=" + EXT],
});
const sw = ctx.serviceWorkers()[0] || await ctx.waitForEvent("serviceworker");
// Settings go through an extension page, never the service worker: Chrome
// suspends an idle worker and an evaluate() on it then never returns.
const extId = new URL(sw.url()).host;
const ajustes = await ctx.newPage();
await ajustes.goto("chrome-extension://" + extId + "/options.html");
// The demo books on the HSI test copy, chosen explicitly (the default is the
// Province's HSI).
await ajustes.evaluate(() => chrome.storage.local.set({ hsiUrl: "http://localhost:8280", institucionId: "1" })
  .then(() => chrome.runtime.sendMessage({ tipo: "ajustes-cambiados" })));
await ajustes.close();
console.log("extension loaded");

const login = await ctx.request.post(HSI + "/api/auth", { data: { username: "admin@example.com", password: process.env.HSI_ADMIN_PASS } });
if (login.status() !== 200) throw new Error("HSI login " + login.status());

await ctx.route(TURNOS + "/**", (route) => {
  const u = new URL(route.request().url());
  if (u.pathname === "/api/admin/day-queue") return route.fulfill({ json: COLA });
  if (u.pathname === "/admin/cola") return route.fulfill({ contentType: "text/html", body: fs.readFileSync(path.join(aqui, "pacientes-de-hoy.fixture.html"), "utf8") });
  return route.fulfill({ status: 204, body: "" });
});

for (const p of ctx.pages()) if (/options\.html|about:blank/.test(p.url())) await p.close().catch(() => {});
const hsi = await ctx.newPage();
await hsi.goto(HSI + "/institucion/1/turnos", { waitUntil: "domcontentloaded" });
const turnos = await ctx.newPage();
await turnos.goto(TURNOS + "/admin/cola");
await turnos.locator('[data-hr-hsi="201"]').waitFor({ timeout: 60000 });
console.log("READY: Pacientes de hoy + HSI de prueba abiertos");

if (process.argv.includes("--self-check")) try {
  await turnos.locator('[data-hr-hsi="201"]').click();
  await turnos.waitForFunction(() => document.querySelector('[data-hr-hsi="201"]').textContent.trim() === "Confirmá en HSI", null, { timeout: 180000 });
  await hsi.locator("app-new-appointment button:visible", { hasText: /^\s*Confirmar\s*$/i }).click();
  await turnos.waitForFunction(() => document.querySelector('[data-hr-hsi="201"]').textContent.trim() === "Guardado en HSI", null, { timeout: 60000 });
  const enAgenda = await hsi.locator("mwl-calendar-week-view .cal-event", { hasText: "PRUEBAUNO" }).count();
  console.log("SELF-CHECK: fila 201 «Guardado en HSI»; turnos de PRUEBAUNO en la agenda: " + enAgenda);
  await hsi.locator(".hr-hsi-accion", { hasText: "No, gracias" }).click().catch(() => {});
  await turnos.bringToFront();
} catch (e) {
  // The window stays open either way; say where the check stopped.
  const fila = await turnos.locator('[data-hr-hsi="201"]').textContent().catch(() => "?");
  const panel = await hsi.locator(".hr-hsi-mensaje").textContent().catch(() => "");
  const pregunta = await hsi.locator("mat-dialog-container").count().catch(() => -1);
  console.log("SELF-CHECK FAILED: fila=«" + fila.trim() + "» panel=«" + panel + "» ventanas HSI=" + pregunta + " — " + String(e.message).split("\n")[0]);
}

await new Promise((ok) => ctx.on("close", ok));
fs.rmSync(perfil, { recursive: true, force: true });
