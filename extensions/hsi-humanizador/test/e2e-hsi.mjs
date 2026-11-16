/*
 * e2e-hsi.mjs — the extension, loaded in a real Chromium, loading turnos into
 * the HSI TEST copy (http://localhost:8280). Never point this at an HSI with
 * real patients: it books appointments.
 *
 *   HSI_ADMIN_PASS=… node extensions/hsi-humanizador/test/e2e-hsi.mjs [outDir]
 *
 * Needs: the test HSI up and seeded (tools/seed_hsi_demo.py), the four
 * fictional patients below registered in it, and playwright resolvable from
 * the repo (or PW_ROOT). «Pacientes de hoy» is a fixture served on the demo
 * address with the real row hooks, so the run needs no Turnos server.
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
// The same pure helpers the extension uses — an e2e evaluate() runs in the
// page's main world and cannot see the content script's HRDom/HRShared.
const S = require(path.join(EXT, "lib/shared.js"));

const HSI = "http://localhost:8280";
const TURNOS = "https://demo.humanrounds.org";
const OUT = path.resolve(process.argv[2] || path.join(os.tmpdir(), "turnos-hsi-e2e"));
fs.mkdirSync(OUT, { recursive: true });
if (!process.env.HSI_ADMIN_PASS) throw new Error("HSI_ADMIN_PASS is required");

// Next weekday on/after `desde` + n days, as yyyy-mm-dd (the demo agendas run Mon-Fri).
function habil(n) {
  const d = new Date();
  d.setDate(d.getDate() + n);
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1);
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
}

const sufijo = String(Date.now()).slice(-4);
// The patients HSI does not have: 91/92/93 + six digits off the clock, fresh per run.
// A birth date of its own per run and per patient: HSI lists people with the
// same name and birth date as possible matches, and earlier runs left some.
const baseNac = Math.floor(Date.now() / 60000);
const nac = (k) => {
  const n = baseNac + k * 7919;
  return (1940 + n % 60) + "-" + String(1 + Math.floor(n / 60) % 12).padStart(2, "0") + "-" + String(1 + Math.floor(n / 720) % 28).padStart(2, "0");
};
const dniNuevo ="91" + String(Date.now()).slice(-6);
const dniSegunda = "92" + String(Date.now()).slice(-6);
const dniTercera = "93" + String(Date.now()).slice(-6);
const HORAS = ["08:00", "08:30", "09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "13:00", "13:30", "14:00", "14:30", "15:00", "15:30", "16:00"];
// A different hour on every run, so re-runs do not collide with their own turnos.
const hora = (i) => HORAS[(Number(sufijo) + i * 3) % HORAS.length];

const COLA = {
  dia: habil(1), hoy: false, total: 7,
  grupos: [
    { medico_id: "generdemo", medico: "Raúl Generdemo", especialidad: "Medicina general", centro: "Hospital", turnos: [
      { id: 101, dia_iso: habil(1), hora: hora(0), paciente: "Marta Pruebauno", dni: "90.100.101", sexo: "F", contacto: "2254 15-401234", cobertura: "OBRA SOCIAL DEL PERSONAL DE LA INDUSTRIA FIDEERA", servicio: "Medicina general" },
      { id: 105, dia_iso: habil(1), hora: hora(0), paciente: "Omar Pruebacuatro", dni: "90100104", sexo: "M", contacto: "omar.prueba@example.com", cobertura: null, servicio: "Medicina general" },
    ] },
    { medico_id: "pediademo", medico: "Dra. Laura Pediademo", especialidad: "Pediatría", centro: "Hospital", turnos: [
      { id: 102, dia_iso: habil(6), hora: hora(1), paciente: "Héctor Pruebados", dni: "90100102", sexo: "M", contacto: "02254 402345", cobertura: null, servicio: "Pediatría" },
      { id: 104, dia_iso: habil(6), hora: hora(2), paciente: "Persona Inexistente", dni: "90999999", sexo: "M", contacto: null, cobertura: null, servicio: "Pediatría" },
      // A patient HSI does not have, with everything the alta needs: fresh
      // DNI on every run, so it is nobody's.
      { id: 110, dia_iso: habil(6), hora: hora(8), paciente: "Prueba Nuevo", dni: dniNuevo, sexo: "F", contacto: "2254 409901", cobertura: null, nacimiento: nac(0), servicio: "Pediatría" },
      // Two more: one the «guardar solo» run creates end to end by itself,
      // one whose alta the operator abandons halfway.
      { id: 111, dia_iso: habil(6), hora: hora(10), paciente: "Prueba Segunda", dni: dniSegunda, sexo: "M", contacto: "2254 409902", cobertura: null, nacimiento: nac(1), servicio: "Pediatría" },
      { id: 112, dia_iso: habil(6), hora: hora(9), paciente: "Prueba Tercera", dni: dniTercera, sexo: "F", contacto: null, cobertura: null, nacimiento: nac(2), servicio: "Pediatría" },
    ] },
    { medico_id: "dermademo", medico: "Hugo Dermademo", especialidad: "Dermatología", centro: "Hospital", turnos: [
      { id: 103, dia_iso: habil(11), hora: hora(3), paciente: "Lucía Pruebatres", dni: "90100103", sexo: "F", contacto: "lucia.prueba@example.com", cobertura: null, servicio: "Dermatología" },
      { id: 108, dia_iso: habil(11), hora: hora(6), paciente: "Lucía Pruebatres", dni: "90100103", sexo: "F", contacto: null, cobertura: null, servicio: "Dermatología" },
      { id: 109, dia_iso: habil(13), hora: hora(7), paciente: "Héctor Pruebados", dni: "90100102", sexo: "M", contacto: "2254 403456", cobertura: null, servicio: "Dermatología" },
      { id: 107,dia_iso: habil(12), hora: hora(4), paciente: "Omar Pruebacuatro", dni: "90100104", sexo: "M", contacto: null, cobertura: null, servicio: "Dermatología" },
    ] },
    { medico_id: "nadie", medico: "Dra. Zulema Inexistente", especialidad: "Clínica médica", centro: "Hospital", turnos: [
      { id: 106, dia_iso: habil(2), hora: hora(5), paciente: "Persona Inexistente", dni: "90999999", sexo: "F", contacto: null, cobertura: null, servicio: "Clínica médica" },
    ] },
  ],
};
// Every row carries a birth date except 104, the one that proves a row
// without it loads anyway and the alta asks for it.
for (const g of COLA.grupos) for (const t of g.turnos) if (t.id !== 104 && !t.nacimiento) t.nacimiento = "1975-03-12";
const guardadosEnTurnos = [];

const perfil = fs.mkdtempSync(path.join(os.tmpdir(), "turnos-hsi-perfil-"));
const ctx = await chromium.launchPersistentContext(perfil, {
  headless: false,
  viewport: { width: 1440, height: 900 },
  recordVideo: { dir: path.join(OUT, "video"), size: { width: 1440, height: 900 } },
  args: ["--headless=new", "--disable-extensions-except=" + EXT, "--load-extension=" + EXT],
});
ctx.setDefaultTimeout(90000);

const resultados = [];
function comprobar(nombre, ok, detalle) {
  resultados.push({ nombre, ok: !!ok });
  console.log((ok ? "PASS  " : "FAIL  ") + nombre + (detalle ? "  — " + detalle : ""));
}

try {
  let sw = ctx.serviceWorkers()[0] || await ctx.waitForEvent("serviceworker");
  const extId = new URL(sw.url()).host;
  // Storage is read through an extension page, never the service worker:
  // Chrome suspends an idle worker and an evaluate() on it never returns.
  const cfg = await ctx.newPage();
  await cfg.goto("chrome-extension://" + extId + "/options.html");
  const ajustes = (valores) => cfg.evaluate((v) => chrome.storage.local.set(v), valores);
  const leer = () => cfg.evaluate(() => chrome.storage.local.get(null));

  const login = await ctx.request.post(HSI + "/api/auth", { data: { username: "admin@example.com", password: process.env.HSI_ADMIN_PASS } });
  if (login.status() !== 200) throw new Error("HSI login " + login.status());

  // Marta's record in the test HSI (patientId 1) gets the coverage the row
  // names — plus one more, so the choice cannot be a preselect of the only
  // option. Idempotent: whatever the last run left is deactivated first.
  const covUrl = HSI + "/api/patientMedicalCoverage/1/coverages";
  for (const c of await (await ctx.request.get(covUrl)).json()) {
    await ctx.request.post(covUrl, { data: [{ ...c, active: false }] });
  }
  const covNueva = await ctx.request.post(covUrl, { data: [
    { medicalCoverage: { id: 1, type: 2 }, affiliateNumber: "60001", active: true, vigenciaDesde: "01/01/2024" },
    { medicalCoverage: { id: 2, type: 2 }, affiliateNumber: "50002", active: true, vigenciaDesde: "01/01/2024" }
  ] });
  if (covNueva.status() !== 201) throw new Error("coverage seed " + covNueva.status());

  await ctx.route(TURNOS + "/**", (route) => {
    const u = new URL(route.request().url());
    if (/^\/api\/admin\/requests\/\d+\/patient-data$/.test(u.pathname) && route.request().method() === "POST") {
      guardadosEnTurnos.push({ ruta: u.pathname, cuerpo: route.request().postDataJSON() });
      return route.fulfill({ json: { ok: true } });
    }
    if (u.pathname === "/api/admin/day-queue") return route.fulfill({ json: COLA });
    if (u.pathname === "/admin/cola") return route.fulfill({ contentType: "text/html", body: fs.readFileSync(path.join(aqui, "pacientes-de-hoy.fixture.html"), "utf8") });
    return route.fulfill({ status: 204, body: "" });
  });

  // The options page opens by itself on install: the first thing an operator sees.
  const abierta = () => ctx.pages().find((p) => p !== cfg && p.url().includes(extId));
  for (let i = 0; i < 40 && !abierta(); i++) await new Promise((ok) => setTimeout(ok, 250));
  comprobar("al instalar se abren los ajustes solos", !!abierta(), ctx.pages().map((p) => p.url()).join(" | "));
  const opciones = abierta() || await ctx.newPage();
  if (!opciones.url().includes(extId)) await opciones.goto("chrome-extension://" + extId + "/options.html");
  await opciones.waitForSelector('input[name="hsi"]');
  // Before anything was loaded, the popup teaches how to use it.
  const popVacio = await ctx.newPage();
  await popVacio.goto("chrome-extension://" + extId + "/popup.html");
  await popVacio.locator("#pasos li").first().waitFor();
  const textoVacio = await popVacio.locator("main").innerText();
  comprobar("popup vacío: explica los tres pasos, no «todavía nada»",
    await popVacio.locator("#pasos li").count() === 3 && textoVacio.includes("Cargar en HSI") && textoVacio.includes("Confirmar") && !/todavía no/i.test(textoVacio));
  comprobar("popup vacío: el video del tutorial está a la vista", await popVacio.locator("#video").isVisible() && /^https:\/\/\S+/.test(await popVacio.locator("#video").getAttribute("href")));
  comprobar("popup vacío: sin lista de cargas vacía", await popVacio.locator("#recientes").isHidden());
  await popVacio.screenshot({ path: path.join(OUT, "00-popup-vacio.png") });
  await popVacio.close();
  comprobar("ajustes: la HSI de la Provincia viene elegida", await opciones.locator('input[name="hsi"]:checked').getAttribute("value") === "https://shc.ms.gba.gov.ar");
  comprobar("ajustes: «guardar solo» no se ofrece antes del primer guardado", await opciones.locator("#fila-guardar-solo").isHidden());
  await opciones.screenshot({ path: path.join(OUT, "01-ajustes.png") });
  await opciones.close();
  // This run books on the HSI test copy, never the Province's: chosen
  // explicitly, the way an operator would.
  await ajustes({ hsiUrl: HSI });
  await cfg.evaluate(() => chrome.runtime.sendMessage({ tipo: "ajustes-cambiados" }));

  const turnos = await ctx.newPage();
  await turnos.goto(TURNOS + "/admin/cola");
  const boton = (id) => turnos.locator('[data-hr-hsi="' + id + '"]');
  await boton(101).waitFor();
  comprobar("Pacientes de hoy: un botón por fila", await turnos.locator("[data-hr-hsi]").count() === 12);
  await turnos.screenshot({ path: path.join(OUT, "02-pacientes-de-hoy.png") });

  const hsiPage = async () => ctx.pages().find((p) => p.url().startsWith(HSI)) || ctx.waitForEvent("page", (p) => p.url().startsWith(HSI) || p.url() === "about:blank");
  // Which HSI page a job runs in is the extension's call; the run watches
  // them ALL for the route instead of betting on one handle (a stray second
  // HSI page once split the job from the assertions and hung the run).
  const esperarRutaHsi = async (pat, ms) => {
    const fin = Date.now() + (ms || 120000);
    for (;;) {
      const p = ctx.pages().find((pg) => pg.url().startsWith(HSI) && pat.test(pg.url()));
      if (p) return p;
      if (Date.now() > fin) throw new Error("ninguna pestaña de HSI llegó a " + pat);
      await new Promise((ok) => setTimeout(ok, 250));
    }
  };
  const texto = async (id) => (await boton(id).textContent()).trim();
  const esperarBoton = (id, t, ms) => turnos.waitForFunction(([i, x]) => { const b = document.querySelector('[data-hr-hsi="' + i + '"]'); return b && b.textContent.trim() === x; }, [id, t], { timeout: ms || 180000 });
  // The agenda repaints a moment after HSI confirms: give the count time to settle.
  const eventosDe = async (hsi, apellido, esperado) => {
    const loc = hsi.locator("mwl-calendar-week-view .cal-event", { hasText: apellido.toUpperCase() });
    for (let i = 0; esperado != null && i < 40 && await loc.count() !== esperado; i++) await hsi.waitForTimeout(250);
    return loc.count();
  };

  // Opens «Nuevo turno» the way the operator does — professional, agenda,
  // week, free slot — with the extension's own selectors, so the auto-mode
  // check drives a dialog the extension never opened.
  const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
  const abrirDialogo = async (page, medico, iso, horaTurno) => {
    if (!page.url().includes("/institucion/1/turnos")) await page.goto(HSI + "/institucion/1/turnos");
    const campo = page.locator("app-professional-select input");
    await campo.waitFor();
    await campo.click();
    await campo.fill(medico.split(" ").pop());
    const opcion = page.locator(".cdk-overlay-container mat-option:visible").first();
    await opcion.waitFor({ timeout: 15000 });
    await opcion.click();
    const agenda = page.locator("#agenda-select");
    await agenda.waitFor();
    await agenda.click();
    const agOpcion = page.locator(".cdk-overlay-container mat-option:visible").first();
    await agOpcion.waitFor({ timeout: 15000 });
    await agOpcion.click();
    const enSemana = await page.evaluate(() => {
      const t = Array.from(document.querySelectorAll("mat-button-toggle")).find((x) => /semana/i.test(x.textContent || ""));
      return !!(t && t.classList.contains("mat-button-toggle-checked"));
    });
    if (!enSemana) {
      await page.locator("mat-button-toggle", { hasText: "Semana" }).first().click();
      await page.waitForTimeout(600);
    }
    const [y, m, d] = iso.split("-").map(Number);
    const lunes = new Date(y, m - 1, d);
    lunes.setDate(lunes.getDate() - ((lunes.getDay() + 6) % 7));
    const titulo = lunes.getDate() + " de " + MESES[lunes.getMonth()] + " de " + lunes.getFullYear();
    const tituloSemana = () => page.evaluate(() => {
      const cabeza = document.querySelector("app-agenda #header") || document.querySelector("app-agenda");
      if (!cabeza) return "";
      const con = Array.from(cabeza.querySelectorAll("*")).find((e) => !e.children.length && /\d{1,2} de \S+ de \d{4}/.test(e.textContent || ""));
      return con ? con.textContent.replace(/\s+/g, " ").trim() : "";
    });
    for (let i = 0; i < 26; i++) {
      const t = await tituloSemana();
      if (t && t.includes(titulo)) break;
      await page.locator("#next-button").click();
      await page.waitForTimeout(400);
    }
    const textos = (sel) => page.evaluate((s) => Array.from(document.querySelectorAll(s)).map((e) => (e.textContent || "").replace(/\s+/g, " ").trim()), sel);
    const col = S.columnaDelDia(iso, await textos("mwl-calendar-week-view .cal-day-headers .cal-header"));
    const fila = S.filaDeLaHora(horaTurno, await textos("mwl-calendar-week-view .cal-time-label-column .cal-hour-segment"));
    await page.evaluate(([c, f]) => {
      const colEl = document.querySelectorAll("mwl-calendar-week-view .cal-time-events .cal-day-column")[c];
      const seg = colEl && colEl.querySelectorAll(".cal-hour-segment")[f];
      if (!seg || !seg.classList.contains("bg-green")) throw new Error("slot no libre");
      const r = seg.getBoundingClientRect();
      const medio = r.top + r.height / 2;
      const ocupado = Array.from(colEl.querySelectorAll(".cal-event")).some((ev) => {
        const e = ev.getBoundingClientRect();
        return e.height > 0 && e.top <= medio && e.bottom >= medio;
      });
      if (ocupado) throw new Error("slot no libre");
      seg.click();
    }, [col, fila]);
    await page.locator("app-new-appointment #identifNumber").waitFor({ timeout: 15000 });
  };

  // 1 — default mode: fills, stops at Confirmar, the operator saves.
  await boton(101).click();
  const hsi = await hsiPage();
  // First time on this HSI: the operator picks their institution once.
  await hsi.locator(".hr-hsi-mensaje", { hasText: "Elegí la institución de Hospital" }).waitFor();
  await hsi.screenshot({ path: path.join(OUT, "02b-hsi-elegir-institucion.png") });
  await hsi.getByText("Hospital Demo", { exact: true }).first().click();
  await hsi.locator("app-new-appointment #phoneNumber").waitFor({ timeout: 180000 });
  await esperarBoton(101, "Confirmá en HSI");
  comprobar("recuerda la institución elegida para ese centro", ((await leer()).institucionPorCentro || {}).hospital === "1");
  await hsi.bringToFront();
  comprobar("modo por defecto: llena y NO guarda", await hsi.locator("app-new-appointment").count() === 1 && await eventosDe(hsi, "Pruebauno") === 0);
  const confirmarBtn = await hsi.locator("app-new-appointment button:visible", { hasText: /^\s*Confirmar\s*$/i }).boundingBox();
  const panelBox = await hsi.locator(".hr-hsi-panel").boundingBox();
  comprobar("el panel no tapa el botón Confirmar", panelBox.x + panelBox.width <= confirmarBtn.x || panelBox.y >= confirmarBtn.y + confirmarBtn.height);
  comprobar("modo por defecto: DNI sin puntos", await hsi.locator("#identifNumber").inputValue() === "90100101");
  comprobar("modo por defecto: teléfono partido en prefijo y número", await hsi.locator("#phonePrefix").inputValue() === "2254" && await hsi.locator("#phoneNumber").inputValue() === "401234");
  comprobar("modo por defecto: pide revisar y confirmar", (await hsi.locator(".hr-hsi-mensaje").textContent()).includes("tocá Confirmar"));
  const coberturaTxt = await hsi.evaluate(() => {
    const sel = document.querySelector('mat-select[formcontrolname="patientMedicalCoverage"]');
    return sel ? sel.textContent.replace(/\s+/g, " ").trim() : "";
  });
  comprobar("cobertura: la que nombra la fila queda elegida", /FIDEERA/i.test(coberturaTxt), coberturaTxt);
  await hsi.screenshot({ path: path.join(OUT, "03-hsi-listo-para-confirmar.png") });

  // 1a — a second row pressed while the first is still open in HSI: it is
  // refused at once with the reason, and the first turno keeps its place.
  await boton(102).click();
  await turnos.locator('[data-hr-hsi="102"][data-estado="error"]').waitFor({ timeout: 20000 });
  comprobar("segundo botón con HSI ocupada: avisa y no queda colgado", (await turnos.locator('[data-hr-hsi="102"] + .hr-hsi-aviso').textContent()).includes("otro turno") && (await texto(102)) === "Cargar en HSI");
  comprobar("segundo botón con HSI ocupada: el primero sigue en pie", (await texto(101)) === "Confirmá en HSI" && await hsi.locator("#identifNumber").inputValue() === "90100101");
  await hsi.locator("app-new-appointment button:visible", { hasText: /^\s*Confirmar\s*$/i }).click();
  await esperarBoton(101, "Guardado en HSI");
  comprobar("el operador confirma: «Guardado en HSI» en la fila", true);
  comprobar("el turno quedó en la agenda de HSI", await eventosDe(hsi, "Pruebauno", 1) === 1);
  comprobar("tras el 1.er guardado ofrece «guardar solo»", (await hsi.locator(".hr-hsi-mensaje").textContent()).includes("lo guarde solo"));
  await hsi.screenshot({ path: path.join(OUT, "04-hsi-guardado-y-oferta.png") });
  await turnos.screenshot({ path: path.join(OUT, "05-fila-guardada.png") });
  comprobar("guardar solo sigue apagado hasta que el operador acepta", (await leer()).guardarSolo !== true);

  // 1b — "No, gracias" keeps it off; the offer returns on the 2nd save.
  await hsi.locator(".hr-hsi-accion", { hasText: "No, gracias" }).click();
  comprobar("«No, gracias» lo deja apagado", (await leer()).guardarSolo !== true && (await leer()).guardados === 1);

  // 2 — second manual save, next week, title + reversed name order; then accept the offer.
  await turnos.bringToFront();
  await boton(102).click();
  await hsi.locator("app-new-appointment #phoneNumber").waitFor({ timeout: 180000 });
  await esperarBoton(102, "Confirmá en HSI");
  await hsi.bringToFront();
  comprobar("otra semana: abrió el día y la hora del turno", (await hsi.locator("app-new-appointment app-contextual-box").first().textContent()).includes(COLA.grupos[1].turnos[0].hora));
  comprobar("teléfono con 0 adelante: prefijo y número", await hsi.locator("#phonePrefix").inputValue() === "2254" && await hsi.locator("#phoneNumber").inputValue() === "402345");
  await hsi.locator("app-new-appointment button:visible", { hasText: /^\s*Confirmar\s*$/i }).click();
  await esperarBoton(102, "Guardado en HSI");
  comprobar("2.º guardado: vuelve a ofrecer", (await hsi.locator(".hr-hsi-mensaje").textContent()).includes("lo guarde solo"));
  await hsi.locator(".hr-hsi-accion", { hasText: "Sí, guardá solo" }).click();
  await hsi.waitForTimeout(400);
  comprobar("«Sí, guardá solo» lo enciende", (await leer()).guardarSolo === true);

  // 3 — guardar solo: fills AND saves, two weeks ahead, e-mail contact.
  await turnos.bringToFront();
  await boton(103).click();
  await esperarBoton(103, "Guardado en HSI");
  await hsi.bringToFront();
  comprobar("guardar solo: guardó sin intervención", await eventosDe(hsi, "Pruebatres", 1) === 1);
  comprobar("guardar solo: no vuelve a ofrecer", await hsi.locator(".hr-hsi-mensaje", { hasText: "lo guarde solo" }).count() === 0);
  await hsi.screenshot({ path: path.join(OUT, "06-hsi-guardar-solo.png") });

  // 3b — HSI asks a question (the patient already has a turno that day):
  // even with guardar solo on, the answer is the operator's.
  await turnos.bringToFront();
  await boton(108).click();
  await hsi.locator(".hr-hsi-mensaje", { hasText: "te hace una pregunta" }).waitFor({ timeout: 180000 });
  await hsi.bringToFront();
  comprobar("pregunta de HSI: no la contesta por el operador", await eventosDe(hsi, "Pruebatres", 1) === 1 && await hsi.locator("app-new-appointment").count() === 1);
  await hsi.screenshot({ path: path.join(OUT, "06b-hsi-pregunta.png") });
  await hsi.getByRole("button", { name: /asignar turno/i }).click();
  await esperarBoton(108, "Guardado en HSI");
  comprobar("pregunta de HSI: el operador acepta y queda guardado", await eventosDe(hsi, "Pruebatres", 2) === 2);

  // 3c — the operator presses Confirmar before the contact is filled in: HSI
  // saves and closes; the row must say saved, never an error (owner, 2026-10-08).
  await ajustes({ guardarSolo: false });
  await turnos.bringToFront();
  await boton(109).click();
  await hsi.locator("app-new-appointment #phoneNumber").waitFor({ timeout: 180000 });
  await hsi.locator("app-new-appointment button:visible", { hasText: /^\s*Confirmar\s*$/i }).click();
  await hsi.getByRole("button", { name: /asignar turno/i }).click({ timeout: 4000 }).catch(() => {});
  await esperarBoton(109, "Guardado en HSI", 60000);
  comprobar("Confirmar antes de tiempo: queda «Guardado en HSI», sin error", true);
  await hsi.locator(".hr-hsi-accion", { hasText: "No, gracias" }).click({ timeout: 5000 }).catch(() => {});
  await ajustes({ guardarSolo: true });

  // 4 — patient HSI does not know and the row carries no birth date: the row
  // asks NOTHING (the birth date never blocks the load), HSI is searched, and
  // only because HSI has no such patient the panel asks for the date, right
  // there, in the alta (owner, 2026-10-09; HSI desk feedback, 2026-10-30). The alta
  // is left unsaved here, so this fixed document never comes to exist in HSI.
  await ajustes({ guardarSolo: false });
  await turnos.bringToFront();
  await boton(104).click();
  const hsiSinFecha = await esperarRutaHsi(/\/institucion\//, 60000);
  const dePanel = hsiSinFecha.locator(".hr-hsi-datos input[name=nacimiento]");
  await dePanel.waitFor({ timeout: 180000 });
  comprobar("sin fecha de nacimiento: la fila no pregunta nada antes de ir a HSI", await turnos.locator(".hr-hsi-datos").count() === 0);
  comprobar("…y el paciente que HSI no tiene cae en el alta, donde el panel pide solo la fecha",
    await hsiSinFecha.locator(".hr-hsi-datos input, .hr-hsi-datos select").count() === 1);
  await hsiSinFecha.screenshot({ path: path.join(OUT, "07-alta-pide-la-fecha.png") });
  await dePanel.fill(nac(3));
  await hsiSinFecha.locator(".hr-hsi-datos button[type=submit]").click();
  const hsiDato = await esperarRutaHsi(/\/pacientes\/new/, 180000);
  comprobar("…y con la respuesta sigue sola hasta el alta de HSI, con la fecha cargada",
    await hsiDato.locator("#identificationNumber").inputValue() === "90999999" && await hsiDato.locator("#birthDate").inputValue() === nac(3).split("-").reverse().join("/"));
  await hsiDato.locator(".hr-hsi-cancelar").click();
  await esperarBoton(104, "Cargar en HSI");
  await ajustes({ guardarSolo: true });

  // 5 — the hour is already taken in HSI: asks the operator, never overbooks.
  await turnos.bringToFront();
  await boton(105).click();
  await hsi.locator(".hr-hsi-mensaje", { hasText: "no están libres" }).waitFor({ timeout: 180000 });
  await hsi.bringToFront();
  comprobar("horario ocupado: lo deja en manos del operador", (await hsi.locator(".hr-hsi-mensaje").textContent()).includes("no están libres") && await hsi.locator("app-new-appointment").count() === 0);
  await hsi.screenshot({ path: path.join(OUT, "08-hsi-horario-ocupado.png") });
  await hsi.locator(".hr-hsi-cancelar").click();
  await esperarBoton(105, "Cargar en HSI");
  comprobar("Cancelar devuelve la fila a su estado inicial", true);

  // 6 — a professional HSI does not list: asks, never picks a lookalike.
  await turnos.bringToFront();
  await boton(106).click();
  await hsi.locator(".hr-hsi-mensaje", { hasText: "Elegilo vos" }).waitFor({ timeout: 180000 });
  await hsi.bringToFront();
  comprobar("profesional inexistente: pide elegirlo a mano", (await hsi.locator(".hr-hsi-mensaje").textContent()).includes("Elegilo vos"));
  await hsi.locator(".hr-hsi-cancelar").click();
  await esperarBoton(106, "Cargar en HSI");

  // 7 — quick mode: no pauses, saves, and comes back to the list by itself.
  await ajustes({ mostrarPasos: false });
  await turnos.bringToFront();
  const inicio = Date.now();
  await boton(107).click();
  await esperarBoton(107, "Guardado en HSI");
  const segundos = Math.round((Date.now() - inicio) / 1000);
  await turnos.waitForFunction(() => document.visibilityState === "visible", null, { timeout: 10000 }).catch(() => {});
  comprobar("modo rápido: guarda y vuelve solo a Pacientes de hoy", await turnos.evaluate(() => document.visibilityState) === "visible", segundos + " s");
  await turnos.screenshot({ path: path.join(OUT, "09-pacientes-de-hoy-final.png") });

  // 8 — the patient HSI does not have, with everything the alta needs: HSI's
  // own «Crear paciente» opens the alta, the row's data fill the search, HSI's
  // «Buscar» is pressed (a search, it only reads), and the job waits on HSI's
  // new-patient screen. The operator (the test) finishes the alta with HSI's
  // «Guardar»; the extension then continues BY ITSELF to the turno for that
  // same row and stops at Confirmar. On the way there, the test HSI's Renaper
  // never answers: HSI warns so and STILL opens its patient search — the alta
  // goes on, and the run pins that real behavior.
  await ajustes({ guardarSolo: false, mostrarPasos: true });
  await turnos.bringToFront();
  await boton(110).click();
  const enBusqueda = await esperarRutaHsi(/\/pacientes\/search/, 60000);
  const renaper = enBusqueda.locator("snack-bar-container, .mat-snack-bar-container").filter({ hasText: /renaper/i });
  await renaper.first().waitFor({ timeout: 20000 });
  comprobar("alta: con Renaper caído HSI avisa y sigue a su búsqueda igual", /no hubo respuesta del servicio de renaper/i.test(await renaper.first().innerText()));
  let hsiA = await esperarRutaHsi(/\/pacientes\/new/, 120000);
  comprobar("alta: HSI llegó a su pantalla de paciente nuevo", /\/pacientes\/new/.test(hsiA.url()));
  comprobar("alta: DNI, nombre, apellido y nacimiento cargados",
    await hsiA.locator("#identificationNumber").inputValue() === dniNuevo
    && await hsiA.locator("#firstName").inputValue() === "Prueba"
    && await hsiA.locator("#lastName").inputValue() === "Nuevo"
    && await hsiA.locator("#birthDate").inputValue() === nac(0).split("-").reverse().join("/"));
  comprobar("alta: el sexo de la fila marcado", await hsiA.locator("mat-radio-button.mat-radio-checked", { hasText: "Femenino" }).count() === 1);
  await turnos.locator('[data-hr-hsi="110"][data-estado="alta"]').waitFor({ timeout: 30000 });
  comprobar("alta: la fila espera el Guardar del operador", (await texto(110)) === "Terminá el alta en HSI" && (await turnos.locator('[data-hr-hsi="110"] + .hr-hsi-aviso').textContent()).includes("alta"));
  comprobar("alta: el recuadro pide terminar el alta", (await hsiA.locator(".hr-hsi-mensaje").textContent()).includes("tocá Guardar"));
  await hsiA.screenshot({ path: path.join(OUT, "10-hsi-alta-espera.png") });
  // The operator finishes the alta in HSI itself.
  await hsiA.locator("button", { hasText: "Guardar" }).first().click();
  // The same job goes on: back to the agendas, the slot of the row, the
  // patient that just came to exist, and a stop at Confirmar.
  await hsiA.locator("app-new-appointment #phoneNumber").waitFor({ timeout: 180000 });
  await esperarBoton(110, "Confirmá en HSI");
  comprobar("alta: siguió solo hasta el turno del mismo paciente", await hsiA.locator("#identifNumber").inputValue() === dniNuevo);
  comprobar("alta: el horario es el de la fila", (await hsiA.locator("app-new-appointment app-contextual-box").first().textContent()).includes(COLA.grupos[1].turnos[2].hora));
  await esperarBoton(110, "Confirmá en HSI");
  comprobar("alta: se detiene en Confirmar como siempre", (await hsiA.locator(".hr-hsi-mensaje").textContent()).includes("tocá Confirmar"));
  await hsiA.screenshot({ path: path.join(OUT, "10b-hsi-tras-el-alta-listo.png") });
  await hsiA.locator("app-new-appointment button:visible", { hasText: /^\s*Confirmar\s*$/i }).click();
  await esperarBoton(110, "Guardado en HSI");
  comprobar("alta: el operador confirma y queda «Guardado en HSI»", true);
  await hsiA.screenshot({ path: path.join(OUT, "10c-hsi-tras-el-alta-guardado.png") });

  // 8b — «guardar solo»: the patient does not exist, and nobody touches HSI:
  // the extension searches, saves the patient, comes back for the turno,
  // fills it and confirms it, all by itself.
  await ajustes({ guardarSolo: true });
  await turnos.bringToFront();
  await boton(111).click();
  await esperarBoton(111, "Guardado en HSI");
  comprobar("alta con «guardar solo»: paciente y turno guardados sin tocar a HSI", true);
  await hsiA.bringToFront();
  comprobar("alta con «guardar solo»: el turno quedó en la agenda", await eventosDe(hsiA, "Segunda", 1) === 1 && await hsiA.locator("app-new-appointment").count() === 0);
  await ajustes({ guardarSolo: false });

  // 8c — the operator abandons the alta: walks off HSI's new-patient screen;
  // the job ends with the reason and the row's button comes back.
  await turnos.bringToFront();
  await boton(112).click();
  hsiA = await esperarRutaHsi(/\/pacientes\/new/, 120000);
  await turnos.locator('[data-hr-hsi="112"][data-estado="alta"]').waitFor({ timeout: 30000 });
  comprobar("alta abandonada: la fila espera mientras está en pantalla", (await texto(112)) === "Terminá el alta en HSI");
  await hsiA.goto(HSI + "/institucion/1/turnos");
  await turnos.locator('[data-hr-hsi="112"][data-estado="error"]').waitFor({ timeout: 60000 });
  comprobar("alta abandonada: la fila vuelve con el motivo",
    (await texto(112)) === "Cargar en HSI" && (await turnos.locator('[data-hr-hsi="112"] + .hr-hsi-aviso').textContent()).includes("Se cortó el alta"));
  await hsiA.screenshot({ path: path.join(OUT, "10d-hsi-alta-abandonada.png") });

  // 9 — auto mode: the operator opens the slot by hand and types the DNI; the
  // patient's half fills itself out of the last «Pacientes de hoy» read.
  await ajustes({ modo: "auto", guardarSolo: false });
  // The previous case ended on an error panel; the operator closes it.
  await hsi.locator(".hr-hsi-accion", { hasText: "Cerrar" }).click({ timeout: 3000 }).catch(() => {});
  const diaAuto = habil(9), horaAuto = hora(9);
  await abrirDialogo(hsi, "Hugo Dermademo", diaAuto, horaAuto);
  // A DNI the list does not know (or knows twice): nothing moves at all.
  await hsi.locator("#identifNumber").fill("31111111");
  await hsi.locator("#identifNumber").press("Tab");
  await hsi.waitForTimeout(2500);
  comprobar("automático: un DNI que no está en la cola no mueve nada", await hsi.locator(".hr-hsi-panel").count() === 0);
  await hsi.locator("#identifNumber").fill("90100101");
  await hsi.locator("#identifNumber").press("Tab");
  // The extension's own signal that it finished filling: the panel parks at
  // «tocá Confirmar». Waiting for the contact step's fields is not enough —
  // the stepper keeps both steps' fields attached while it slides.
  await hsi.locator(".hr-hsi-mensaje", { hasText: "tocá Confirmar" }).waitFor({ timeout: 60000 });
  comprobar("automático: sexo y contacto completados solos",
    await hsi.locator("mat-radio-button.mat-radio-checked", { hasText: "Femenino" }).count() === 1
    && await hsi.locator("#phonePrefix").inputValue() === "2254"
    && await hsi.locator("#phoneNumber").inputValue() === "401234");
  comprobar("automático: se detiene en Confirmar",
    (await hsi.locator(".hr-hsi-mensaje").textContent()).includes("tocá Confirmar") && await hsi.locator(".hr-hsi-paso").count() === 3);
  await hsi.screenshot({ path: path.join(OUT, "11-hsi-automatico.png") });
  await hsi.locator("app-new-appointment button:visible", { hasText: /^\s*Confirmar\s*$/i }).click();
  comprobar("automático: el operador confirma y queda guardado", await eventosDe(hsi, "Pruebauno", 1) === 1);
  // The count lands a moment after HSI's own save notice: watch for it, and
  // say what is left behind if it never comes.
  const contadorAuto = await cfg.waitForFunction(() => chrome.storage.local.get("guardados").then((a) => a.guardados === 9), null, { timeout: 30000, polling: 500 }).then(() => true, () => false);
  const fotoAuto = contadorAuto ? "" : JSON.stringify({ guardados: (await leer()).guardados, trabajos: await cfg.evaluate(() => chrome.storage.session.get("trabajos").then((s) => Object.keys(s.trabajos || {}))), panel: await hsi.locator(".hr-hsi-mensaje").textContent().catch(() => "?") });
  comprobar("automático: contado como guardado", contadorAuto, fotoAuto);

  // 10 — manual mode: the row copies the DNI and brings HSI up; nothing is
  // ever typed into HSI.
  await ajustes({ modo: "manual" });
  await turnos.bringToFront();
  await turnos.waitForFunction(() => { const b = document.querySelector('[data-hr-hsi="105"]'); return b && b.textContent.trim() === "Copiar DNI"; }, null, { timeout: 10000 });
  comprobar("manual: el botón de la fila pasa a «Copiar DNI»", true);
  await boton(105).click();
  await turnos.locator('[data-hr-hsi="105"][data-estado="manual"]').waitFor({ timeout: 15000 });
  comprobar("manual: DNI copiado (solo se anuncia tras copiarlo)", true);
  await hsi.bringToFront();
  comprobar("manual: nada se cargó en HSI", await hsi.locator("app-new-appointment").count() === 0);
  const sesion = await cfg.evaluate(() => chrome.storage.session.get(null));
  comprobar("manual: sin trabajo pendiente en HSI", !sesion.trabajos || Object.keys(sesion.trabajos).length === 0);
  await turnos.screenshot({ path: path.join(OUT, "12-manual-dni-copiado.png") });

  // 11 — the popup: recent loads, newest first, reduced form only.
  const pop = await ctx.newPage();
  await pop.goto("chrome-extension://" + extId + "/popup.html");
  await pop.locator("#cargas li").first().waitFor();
  const primerLi = await pop.locator("#cargas li").first().innerText();
  comprobar("popup: la más reciente primero", primerLi.includes("Omar P. · …104") && primerLi.includes("DNI copiado"), primerLi.replace(/\n/g, " | "));
  const cuerpo = await pop.locator("body").innerText();
  comprobar("popup: la guardada y la no guardada a la vista", cuerpo.includes("Marta P. · …101") && cuerpo.includes("Guardado") && cuerpo.includes("No se guardó"));
  comprobar("popup: nada que identifique a nadie", !/Pruebauno|Pruebados|Pruebatres|Pruebacuatro|Inexistente|Prueba Nuevo|Prueba Segunda|Prueba Tercera|90100101|90100104|90999999|9\d{7}|example\.com/.test(cuerpo));
  await pop.screenshot({ path: path.join(OUT, "13-popup.png") });
  // With loads on screen the tutorial stays one touch away: the video link
  // is there, and the steps unfold on demand.
  const arriba = async (sel) => (await pop.locator(sel).boundingBox()).y;
  comprobar("popup con cargas: el video sigue a la vista y los pasos plegados", await pop.locator("#video").isVisible() && await pop.locator("#pasos").isHidden());
  comprobar("popup con cargas: la ayuda queda arriba de la lista, sin desplazarse", await arriba("#video") < 60 && await arriba("#ayuda summary") < await arriba("#cargas"));
  await pop.locator("#ayuda summary").click();
  comprobar("popup con cargas: «Cómo se usa» despliega los tres pasos", await pop.locator("#pasos li").count() === 3 && await pop.locator("#pasos").isVisible());
  await pop.screenshot({ path: path.join(OUT, "13b-popup-ayuda.png") });
  await pop.locator("#ayuda summary").click();
  // «Abrir en HSI» on the load the operator left halfway (the abandoned
  // alta): the whole DNI comes back from memory while the browser holds it.
  const liMala = pop.locator("#cargas li").filter({ hasText: "No se guardó" }).first();
  await liMala.locator(".abrir").click();
  await pop.waitForFunction(() => Array.from(document.querySelectorAll(".nota")).some((n) => n.textContent.includes("DNI copiado")), null, { timeout: 10000 });
  comprobar("popup: «Abrir en HSI» copia el DNI mientras el navegador lo recuerda", true);
  // The browser forgot: the button still opens HSI, and says so.
  await cfg.evaluate(() => chrome.storage.session.remove("dnis"));
  await pop.reload();
  await pop.locator("#cargas li").filter({ hasText: "No se guardó" }).first().locator(".abrir").click();
  await pop.waitForFunction(() => Array.from(document.querySelectorAll(".nota")).some((n) => n.textContent.includes("ya no está disponible")), null, { timeout: 10000 });
  comprobar("popup: sin el DNI en memoria lo dice y no lo inventa", true);
  await pop.close();

  const final = await leer();
  comprobar("contador local de guardados = 9", final.guardados === 9, "guardados=" + final.guardados);
  const guardado = JSON.stringify(final);
  comprobar("nada que identifique a nadie en el almacenamiento del perfil", !/Pruebauno|Pruebados|Pruebatres|Pruebacuatro|Inexistente|Prueba Nuevo|Prueba Segunda|Prueba Tercera|90100101|90100102|90100103|90100104|90999999|example\.com|401234|402345|403456|409901|409902/.test(guardado) && !guardado.includes(dniNuevo) && !guardado.includes(dniSegunda) && !guardado.includes(dniTercera));
} catch (e) {
  comprobar("la corrida terminó sin excepción", false, String(e && e.message || e).split("\n")[0]);
  console.log("pestañas: " + ctx.pages().map((p) => p.url().split("?")[0]).join(" | "));
  for (const [i, p] of ctx.pages().entries()) {
    const fila = await p.locator(".hr-hsi-boton[data-estado]:not([data-estado=reposo]), .hr-hsi-mensaje").allInnerTexts().catch(() => []);
    if (fila.length) console.log("pestaña " + i + ": " + fila.join(" / "));
    await p.screenshot({ path: path.join(OUT, "fallo-" + i + ".png"), timeout: 8000 }).catch(() => {});
  }
} finally {
  await ctx.close();
  fs.rmSync(perfil, { recursive: true, force: true });
}

const malos = resultados.filter((r) => !r.ok).length;
console.log("\n" + (resultados.length - malos) + "/" + resultados.length + " ok — " + OUT);
process.exit(malos ? 1 : 0);
