/*
 * e2e-hsi-cambios.mjs — what happens in HSI AFTER a turno was loaded: the
 * extension, in a real Chromium, passes the changes made later in Human
 * Rounds to the HSI TEST copy (http://localhost:8280). Never point this at an
 * HSI with real patients: it books, cancels and confirms appointments.
 *
 *   HSI_ADMIN_PASS=… node extensions/hsi-humanizador/test/e2e-hsi-cambios.mjs [outDir]
 *
 * Four cases, each with screenshots: a turno cancelled, a turno moved, a turno
 * that came back from the waiting list (cancelled, then approved at a new
 * slot) and a patient who showed up. «Pacientes de hoy» and the changes
 * answer are mocked on the demo address, so the run needs no Turnos server.
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
const OUT = path.resolve(process.argv[2] || path.join(os.tmpdir(), "turnos-hsi-cambios-e2e"));
fs.mkdirSync(OUT, { recursive: true });
if (!process.env.HSI_ADMIN_PASS) throw new Error("HSI_ADMIN_PASS is required");

// A whole number of weeks apart per run keeps the weekday (the agendas open on
// fixed weekdays) while a re-run never meets its own turnos of another day: HSI
// asks a question when the same person already has a turno on that day.
const SEMANAS = Number(process.env.HSI_SEMANAS || 1 + (Number(String(Date.now()).slice(-3)) % 2));
function habil(n) {
  const d = new Date();
  d.setDate(d.getDate() + n + 7 * SEMANAS);
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1);
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
}
const sufijo = Number(String(Date.now()).slice(-4));
const HORAS = ["08:00", "08:30", "09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "13:00", "13:30", "14:00", "14:30", "15:00", "15:30", "16:00"];
const hora = (i) => HORAS[(sufijo + i * 4) % HORAS.length];

// Four fictional patients HSI already knows; one turno each, a different hour
// per run so a re-run does not collide with its own turnos.
const FILAS = [
  { id: 201, dia_iso: habil(1), hora: hora(0), paciente: "Marta Pruebauno", dni: "90100101", sexo: "F", contacto: "2254 15-401234", servicio: "Medicina general", medico: "Raúl Generdemo", especialidad: "Medicina general" },
  { id: 202, dia_iso: habil(6), hora: hora(1), paciente: "Héctor Pruebados", dni: "90100102", sexo: "M", contacto: "02254 402345", servicio: "Pediatría", medico: "Dra. Laura Pediademo", especialidad: "Pediatría" },
  { id: 203, dia_iso: habil(11), hora: hora(2), paciente: "Lucía Pruebatres", dni: "90100103", sexo: "F", contacto: "lucia.prueba@example.com", servicio: "Dermatología", medico: "Hugo Dermademo", especialidad: "Dermatología" },
  { id: 204, dia_iso: habil(12), hora: hora(3), paciente: "Omar Pruebacuatro", dni: "90100104", sexo: "M", contacto: "omar.prueba@example.com", servicio: "Dermatología", medico: "Hugo Dermademo", especialidad: "Dermatología" }
];
for (const f of FILAS) { f.nacimiento = "1975-03-12"; f.centro = "Hospital"; f.cobertura = null; }
const COLA = { dia: habil(1), hoy: false, total: FILAS.length, grupos: [] };
for (const f of FILAS) {
  let g = COLA.grupos.find((x) => x.medico === f.medico);
  if (!g) COLA.grupos.push(g = { medico_id: f.medico, medico: f.medico, especialidad: f.especialidad, centro: "Hospital", turnos: [] });
  g.turnos.push(f);
}
// What Human Rounds says changed since the load: id -> { estado, turno }.
const CAMBIOS = {};
const consultas = [];

const perfil = fs.mkdtempSync(path.join(os.tmpdir(), "turnos-hsi-cambios-perfil-"));
const ctx = await chromium.launchPersistentContext(perfil, {
  headless: false,
  viewport: { width: 1440, height: 900 },
  args: ["--headless=new", "--disable-extensions-except=" + EXT, "--load-extension=" + EXT],
});
ctx.setDefaultTimeout(90000);

const resultados = [];
function comprobar(nombre, ok, detalle) {
  resultados.push({ nombre, ok: !!ok });
  console.log((ok ? "PASS  " : "FAIL  ") + nombre + (detalle ? "  — " + detalle : ""));
}

try {
  const sw = ctx.serviceWorkers()[0] || await ctx.waitForEvent("serviceworker");
  const extId = new URL(sw.url()).host;
  const cfg = await ctx.newPage();
  await cfg.goto("chrome-extension://" + extId + "/options.html");
  const ajustes = (valores) => cfg.evaluate((v) => chrome.storage.local.set(v), valores);
  const leer = () => cfg.evaluate(() => chrome.storage.local.get(null));

  const login = await ctx.request.post(HSI + "/api/auth", { data: { username: "admin@example.com", password: process.env.HSI_ADMIN_PASS } });
  if (login.status() !== 200) throw new Error("HSI login " + login.status());

  await ctx.route(TURNOS + "/**", (route) => {
    const u = new URL(route.request().url());
    if (u.pathname === "/api/admin/day-queue/changes") {
      consultas.push(u.search);
      const ids = (u.searchParams.get("ids") || "").split(",");
      return route.fulfill({ json: { cambios: Object.keys(CAMBIOS).filter((i) => ids.includes(i)).map((i) => CAMBIOS[i]) } });
    }
    if (u.pathname === "/api/admin/day-queue") return route.fulfill({ json: COLA });
    if (u.pathname === "/admin/cola") return route.fulfill({ contentType: "text/html", body: fs.readFileSync(path.join(aqui, "pacientes-de-hoy.fixture.html"), "utf8") });
    return route.fulfill({ status: 204, body: "" });
  });

  // This run works on the HSI test copy, in quick mode with «guardar solo»,
  // so the loads that set the scene need nobody; the changes under test
  // are checked with the default stops.
  await ajustes({ hsiUrl: HSI, guardarSolo: true, mostrarPasos: false, institucionPorCentro: { hospital: "1" } });
  await cfg.evaluate(() => chrome.runtime.sendMessage({ tipo: "ajustes-cambiados" }));

  const turnos = await ctx.newPage();
  await turnos.goto(TURNOS + "/admin/cola");
  const boton = (id) => turnos.locator('[data-hr-hsi="' + id + '"]');
  await boton(201).waitFor();
  const esperarBoton = (id, t, ms) => turnos.waitForFunction(([i, x]) => { const b = document.querySelector('[data-hr-hsi="' + i + '"]'); return b && b.textContent.trim() === x; }, [id, t], { timeout: ms || 180000 });
  const hsiPage = () => ctx.pages().find((p) => p.url().startsWith(HSI));
  const esperarHsi = async (pat, ms) => {
    const fin = Date.now() + (ms || 120000);
    for (;;) {
      const p = ctx.pages().find((pg) => pg.url().startsWith(HSI) && pat.test(pg.url()));
      if (p) return p;
      if (Date.now() > fin) throw new Error("ninguna pestaña de HSI llegó a " + pat);
      await new Promise((ok) => setTimeout(ok, 250));
    }
  };
  const nombre = (f) => f.paciente.split(" ")[1].toUpperCase();
  // The turno drawn at an hour on the grid on screen (the hour opens its text).
  const eventos = async (hsi, f, hh, esperado) => {
    const loc = hsi.locator("mwl-calendar-week-view .cal-event", { hasText: new RegExp("^\\s*" + hh.replace(/^0/, "0?") + "\\b.*" + nombre(f), "i") });
    for (let i = 0; esperado != null && i < 60 && await loc.count() !== esperado; i++) await hsi.waitForTimeout(250);
    return loc.count();
  };
  const panel = () => turnos.locator(".hr-hsi-cambios");
  const filaCambio = (txt) => panel().locator(".hr-hsi-cambio", { hasText: txt });
  const alRevisar = async () => { await turnos.bringToFront(); await turnos.reload(); await boton(201).waitFor(); };
  const cambio = async (f, estado, extra) => {
    const reg = (await leer()).turnosEnHsi || {};
    const medico = (reg[f.id] && reg[f.id].medico) || f.medico;
    CAMBIOS[f.id] = { id: f.id, estado, turno: { ...f, medico, ...extra } };
  };

  // 0 — the scene: the four turnos loaded by the extension itself.
  for (const f of FILAS) {
    await turnos.bringToFront();
    console.log("carga " + f.id + " (" + f.hora + ")…");
    await boton(f.id).click();
    await esperarBoton(f.id, "Guardado en HSI", 150000);
    console.log("carga " + f.id + " guardada");
  }
  const reg0 = (await leer()).turnosEnHsi || {};
  comprobar("las 4 cargas quedan en el registro del equipo", FILAS.every((f) => reg0[f.id] && reg0[f.id].hora === f.hora && reg0[f.id].dia === f.dia_iso));
  comprobar("el registro guarda solo lugar: ni nombre ni DNI", !/Pruebauno|Pruebados|Pruebatres|Pruebacuatro|9010010\d/.test(JSON.stringify(reg0)));
  comprobar("sin cambios en Turnos, el panel de cambios no aparece", await panel().count() === 0);

  // 1 — cancelled in Human Rounds (desk, patient or «No» in WhatsApp).
  const f1 = FILAS[0];
  await cambio(f1, "cancelada");
  await alRevisar();
  await filaCambio("Cancelado: Marta").waitFor({ timeout: 20000 });
  comprobar("cancelado: Turnos avisa el cambio y ofrece el botón", (await filaCambio("Cancelado: Marta").locator("button").textContent()).includes("Cancelar en HSI"));
  comprobar("cancelado: el aviso sin cargas dice solo lo que hay que hacer", consultas.length >= 1 && consultas.every((q) => /ids=/.test(q)));
  await turnos.screenshot({ path: path.join(OUT, "20-cancelado-aviso-en-turnos.png") });
  await filaCambio("Cancelado: Marta").locator("button").click();
  const hsi1 = await esperarHsi(/institucion\/1\/turnos/);
  await hsi1.locator("app-cancel-appointment textarea#motivo").waitFor({ timeout: 180000 });
  await hsi1.locator("app-cancel-appointment #okButton-button").waitFor();
  comprobar("cancelado: escribe el motivo y se detiene: el último toque es del operador",
    await hsi1.locator("app-cancel-appointment textarea#motivo").inputValue() === "Cancelado desde Human Rounds Turnos" && await eventos(hsi1, f1, f1.hora) === 1);
  await hsi1.screenshot({ path: path.join(OUT, "21-cancelado-hsi-espera-al-operador.png") });
  await hsi1.locator("app-cancel-appointment #okButton-button").click();
  await filaCambio("Listo en HSI").waitFor({ timeout: 60000 });
  comprobar("cancelado: el turno desapareció de la agenda de HSI", await eventos(hsi1, f1, f1.hora, 0) === 0);
  await hsi1.screenshot({ path: path.join(OUT, "22-cancelado-hsi-listo.png") });
  await turnos.screenshot({ path: path.join(OUT, "23-cancelado-listo-en-turnos.png") });
  comprobar("cancelado: el registro lo recuerda y no vuelve a ofrecerlo", ((await leer()).turnosEnHsi[f1.id] || {}).cancelado === true);
  await alRevisar();
  await turnos.waitForTimeout(1500);
  comprobar("cancelado: tras recargar Turnos no vuelve a avisar", await filaCambio("Cancelado: Marta").count() === 0);

  // 2 — moved by the patient: the old slot is freed and the new one taken.
  const f2 = FILAS[1];
  const horaNueva = HORAS[(HORAS.indexOf(f2.hora) + 5) % HORAS.length];
  await cambio(f2, "aprobada", { hora: horaNueva });
  await alRevisar();
  await filaCambio("Movido: Héctor").waitFor({ timeout: 20000 });
  await turnos.screenshot({ path: path.join(OUT, "30-movido-aviso-en-turnos.png") });
  await filaCambio("Movido: Héctor").locator("button").click();
  const hsi2 = await esperarHsi(/institucion\/1\/turnos/);
  await hsi2.locator("app-cancel-appointment #okButton-button").waitFor({ timeout: 180000 });
  await hsi2.screenshot({ path: path.join(OUT, "31-movido-hsi-libera-el-viejo.png") });
  await hsi2.locator("app-cancel-appointment #okButton-button").click();
  await filaCambio("Listo en HSI").waitFor({ timeout: 180000 });
  const registro2 = (await leer()).turnosEnHsi[f2.id] || {};
  comprobar("movido: el lugar nuevo queda en el registro", registro2.hora === horaNueva && !registro2.cancelado, JSON.stringify({ hora: registro2.hora, esperada: horaNueva }));
  const hsi2b = hsiPage();
  comprobar("movido: el horario viejo quedó libre en HSI", await eventos(hsi2b, f2, f2.hora, 0) === 0);
  comprobar("movido: el horario nuevo tiene el turno", await eventos(hsi2b, f2, horaNueva, 1) === 1);
  await hsi2b.screenshot({ path: path.join(OUT, "32-movido-hsi-turno-en-el-nuevo.png") });
  await turnos.screenshot({ path: path.join(OUT, "33-movido-listo-en-turnos.png") });

  // 3 — a slot from the waiting list: the request was cancelled, then comes
  // back approved at another time, and the turno appears in HSI.
  const f3 = FILAS[2];
  await cambio(f3, "cancelada");
  await alRevisar();
  await filaCambio("Cancelado: Lucía").waitFor({ timeout: 20000 });
  await filaCambio("Cancelado: Lucía").locator("button").click();
  const hsi3 = await esperarHsi(/institucion\/1\/turnos/);
  await hsi3.locator("app-cancel-appointment #okButton-button").waitFor({ timeout: 180000 });
  await hsi3.locator("app-cancel-appointment #okButton-button").click();
  await filaCambio("Listo en HSI").waitFor({ timeout: 60000 });
  const horaEspera = HORAS[(HORAS.indexOf(f3.hora) + 3) % HORAS.length];
  await cambio(f3, "aprobada", { hora: horaEspera });
  await alRevisar();
  await filaCambio("Aprobado de nuevo: Lucía").waitFor({ timeout: 20000 });
  comprobar("lista de espera: el turno vuelve como «Aprobado de nuevo» con su botón", (await filaCambio("Aprobado de nuevo: Lucía").locator("button").textContent()).includes("Cargar en HSI"));
  await turnos.screenshot({ path: path.join(OUT, "40-lista-de-espera-aviso-en-turnos.png") });
  await filaCambio("Aprobado de nuevo: Lucía").locator("button").click();
  await filaCambio("Listo en HSI").waitFor({ timeout: 180000 });
  const hsi3b = hsiPage();
  comprobar("lista de espera: el turno aparece en la agenda de HSI", await eventos(hsi3b, f3, horaEspera, 1) === 1);
  await hsi3b.screenshot({ path: path.join(OUT, "41-lista-de-espera-hsi-turno-nuevo.png") });
  await turnos.screenshot({ path: path.join(OUT, "42-lista-de-espera-listo-en-turnos.png") });

  // 4 — the patient showed up: «Confirmado» in HSI (HSI's own «Atendido» and
  // «Ausente» are disabled for everyone, so presence is that state).
  const f4 = FILAS[3];
  await cambio(f4, "aprobada", { asistencia: "presente" });
  await alRevisar();
  await filaCambio("Llegó: Omar").waitFor({ timeout: 20000 });
  await turnos.screenshot({ path: path.join(OUT, "50-presente-aviso-en-turnos.png") });
  await filaCambio("Llegó: Omar").locator("button").click();
  await filaCambio("Listo en HSI").waitFor({ timeout: 180000 });
  const hsi4 = hsiPage();
  await hsi4.screenshot({ path: path.join(OUT, "51-presente-hsi.png") });
  // Open the turno in HSI and read its state.
  await hsi4.bringToFront();
  // Found by its text in the page itself: a Playwright text filter with a
  // regexp did not match the calendar's event in this HSI.
  const abierto = await hsi4.evaluate(([hh, nom]) => {
    const ev = [...document.querySelectorAll("mwl-calendar-week-view .cal-event")].find((e) => e.innerText.trim().toUpperCase().startsWith(hh) && e.innerText.toUpperCase().includes(nom));
    if (ev) ev.click();
    return !!ev;
  }, [f4.hora, nombre(f4)]);
  comprobar("presente: el turno sigue en la agenda de HSI", abierto);
  await hsi4.locator("mat-dialog-container app-appointment section#state").waitFor();
  await hsi4.locator("mat-dialog-container app-appointment .spinner").waitFor({ state: "detached" });
  const clases = await hsi4.locator("section#state button.mat-stroked-button").nth(1).getAttribute("class");
  comprobar("presente: el turno quedó «Confirmado» en HSI", /confirmed/.test(clases || ""), clases);
  await hsi4.screenshot({ path: path.join(OUT, "52-presente-hsi-estado-confirmado.png") });
  await hsi4.locator("#close-dialog").evaluate((el) => el.click());
  comprobar("presente: el registro lo recuerda", ((await leer()).turnosEnHsi[f4.id] || {}).presente === true);

  // Nothing that identifies a person stays on the computer.
  const guardado = JSON.stringify(await leer());
  comprobar("nada que identifique a nadie en el almacenamiento", !/Pruebauno|Pruebados|Pruebatres|Pruebacuatro|9010010\d|example\.com|401234|402345/.test(guardado));
} catch (e) {
  comprobar("la corrida terminó sin excepción", false, String(e && e.message || e).split("\n")[0]);
  console.log("pestañas: " + ctx.pages().map((p) => p.url().split("?")[0]).join(" | "));
  for (const [i, p] of ctx.pages().entries()) {
    const fila = await p.locator(".hr-hsi-cambio, .hr-hsi-mensaje").allInnerTexts().catch(() => []);
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
