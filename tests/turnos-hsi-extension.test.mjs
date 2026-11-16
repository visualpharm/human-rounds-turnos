// The Chrome extension that loads «Pacientes de hoy» turnos into HSI
// (extensions/hsi-humanizador). These pin the decisions it must never get wrong on
// a patient's record: who the professional is, which slot, which sex, and
// when it may press Confirmar by itself.
import test from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { setImmediate as macrotarea } from "node:timers/promises";
import { until } from "./helpers/wait.mjs";

const require = createRequire(import.meta.url);
const EXT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../extensions/hsi-humanizador");
const S = require(path.join(EXT, "lib/shared.js"));
const leer = (f) => fs.readFileSync(path.join(EXT, f), "utf8");
const manifest = JSON.parse(leer("manifest.json"));

test("«guardar solo» se ofrece en 1, 2, 3, 5, 8, 13 y después cada 20", () => {
  const ofrecidos = [];
  for (let n = 0; n <= 65; n++) if (S.debeOfrecer(n)) ofrecidos.push(n);
  assert.deepEqual(ofrecidos, [1, 2, 3, 5, 8, 13, 20, 40, 60]);
});

test("«guardar solo» nace apagado y la HSI por defecto es la de la Provincia; la de prueba solo a propósito", () => {
  assert.equal(S.DEFAULTS.guardarSolo, false);
  assert.equal(S.DEFAULTS.guardados, 0);
  assert.equal(S.DEFAULTS.hsiUrl, "https://shc.ms.gba.gov.ar");
  assert.equal(S.PRESETS[0].id, "provincia");
  assert.ok(S.PRESETS.some((p) => p.id === "prueba" && p.url === "http://localhost:8280"));
});

// Contraparte técnica, administrador de HSI del municipio (2026-10-27): cada centro
// es una institución distinta en la HSI de la Provincia.
test("cada turno va a la institución de su centro; los números son los de la contraparte técnica", () => {
  const PROV = "https://shc.ms.gba.gov.ar";
  assert.deepEqual(
    Object.fromEntries(S.institucionesDe(PROV).map((i) => [i.nombre, i.id])),
    { "Hospital de Pinamar": "1086", "CAPS Pinamar": "1068", "CAPS Ostende": "1073",
      "CAPS Valeria del Mar": "1077", "CAPS DiabetPin": "1084", "Discapacidad": "1082" });
  const a = { hsiUrl: PROV, institucionId: "", institucionPorCentro: {} };
  // By the center's id, and by its name when the row is from an older panel.
  assert.equal(S.institucionPara(a, { centroId: "caps-valeria", centro: "CAPS Valeria del Mar" }), "1077");
  assert.equal(S.institucionPara(a, { centro: "Hospital de Pinamar" }), "1086");
  assert.equal(S.institucionPara(a, { centro: "Coordinación de Discapacidad y Centro de Día" }), "1082");
  // The center's own number wins over the operator's choice: a CAPS turno
  // belongs in that CAPS, whoever loads it.
  assert.equal(S.institucionPara({ ...a, institucionId: "1086" }, { centroId: "caps-pinamar" }), "1068");
  // A center without a known number: the operator's choice, else what they
  // picked in HSI for that center, else nothing (ask).
  const movil = { centroId: "caps-movil", centro: "CAPS Móvil" };
  assert.equal(S.institucionPara(a, movil), "");
  assert.equal(S.institucionPara({ ...a, institucionPorCentro: { [S.claveCentro(movil)]: "1099" } }, movil), "1099");
  assert.equal(S.institucionPara({ ...a, institucionId: "1050", institucionPorCentro: { [S.claveCentro(movil)]: "1099" } }, movil), "1050");
  // «Centro de Salud Integral de Ostende» is not «CAPS Ostende»: no guess.
  assert.equal(S.institucionPara(a, { centroId: "caps-integral-ostende", centro: "Centro de Salud Integral de Ostende" }), "");
  // The numbers are the Province's: another HSI knows none of them.
  assert.equal(S.institucionPara({ ...a, hsiUrl: "http://localhost:8280" }, { centroId: "hospital-pinamar" }), "");
});

test("dónde ves los turnos: un solo lugar; los ajustes de las casillas viejas pasan a una opción", () => {
  // Nothing saved yet, or salud ticked: salud.
  assert.equal(S.turnosElegido({}), "salud");
  assert.equal(S.turnosElegido({ turnosApagados: [], turnosPropio: "" }), "salud");
  // salud unticked and no address of their own: still one place, salud.
  assert.equal(S.turnosElegido({ turnosApagados: ["salud"], turnosPropio: "" }), "salud");
  // An address they typed wins, even with salud ticked too.
  assert.equal(S.turnosElegido({ turnosApagados: [], turnosPropio: "https://turnos.example.org" }), "otra");
  assert.equal(S.turnosElegido({ turnosPropio: "no es una dirección" }), "salud");
});

// Human Rounds sabe la institución de cada centro («Institución en HSI» en el
// panel) y la manda en la fila: manda sobre la tabla de la extensión.
test("la institución que trae la fila manda; la tabla queda para sitios viejos", () => {
  const PROV = "https://shc.ms.gba.gov.ar";
  const a = { hsiUrl: PROV, institucionId: "", institucionPorCentro: {} };
  // Two centers in a row, two institutions — even a center the table never knew.
  assert.equal(S.institucionPara(a, { centroId: "caps-movil", centro: "CAPS Móvil", institucion: "1090" }), "1090");
  assert.equal(S.institucionPara(a, { centroId: "caps-pinamar", institucion: "1068" }), "1068");
  // The row wins over the table and over the operator's choice.
  assert.equal(S.institucionPara({ ...a, institucionId: "1050" }, { centroId: "hospital-pinamar", institucion: "1099" }), "1099");
  // A row without a number (a center not filled in, or an older site): the table…
  assert.equal(S.institucionPara(a, { centroId: "caps-valeria", institucion: "" }), "1077");
  // …and a center nobody knows: ask.
  assert.equal(S.institucionPara(a, { centroId: "zoonosis", centro: "Zoonosis", institucion: "" }), "");
  // The numbers are the real HSI's: the test copy never takes them.
  assert.equal(S.institucionPara({ ...a, hsiUrl: "http://localhost:8280" }, { centroId: "hospital-pinamar", institucion: "1086" }), "");
  // Another real HSI the operator set by address does.
  assert.equal(S.institucionPara({ ...a, hsiUrl: "https://hsi.example.gob.ar" }, { institucion: "12" }), "12");
});

test("la fila de «Pacientes de hoy» pasa la institución de su centro a la extensión", () => {
  const src = fs.readFileSync(new URL("../extensions/hsi-humanizador/content-turnos.js", import.meta.url), "utf8");
  assert.match(src, /institucion: S\.soloDigitos\(t\.hsi_institucion\)/);
});

test("el número de la institución se lee de la dirección de HSI", () => {
  assert.equal(S.institucionDeUrl("https://shc.ms.gba.gov.ar/institucion/1086/turnos"), "1086");
  assert.equal(S.institucionDeUrl("/institucion/1068/pacientes/profile/7"), "1068");
  assert.equal(S.institucionDeUrl("https://shc.ms.gba.gov.ar/home"), "");
  assert.equal(S.institucionDeUrl(""), "");
  assert.equal(S.institucionDeUrl(undefined), "");
});

test("sin ajustes, sin turno o sin centro no hay número que adivinar", () => {
  const PROV = "https://shc.ms.gba.gov.ar";
  assert.deepEqual(S.institucionesDe("no es una dirección"), []);
  assert.equal(S.claveCentro(null), "");
  assert.equal(S.claveCentro({}), "");
  assert.equal(S.institucionDelCentro(PROV, null), "");
  assert.equal(S.institucionDelCentro(PROV, { centroId: "", centro: "" }), "");
  // No settings at all: nothing known, ask.
  assert.equal(S.institucionPara(null, { centro: "Hospital de Pinamar" }), "");
  assert.equal(S.institucionPara(undefined, null), "");
  // A row without a center: the operator's choice, or nothing — never a
  // number remembered for some other center.
  assert.equal(S.institucionPara({ hsiUrl: PROV, institucionId: "1086" }, {}), "1086");
  assert.equal(S.institucionPara({ hsiUrl: PROV, institucionPorCentro: { "": "1099" } }, {}), "");
  // Nothing remembered yet for this center.
  assert.equal(S.institucionPara({ hsiUrl: PROV }, { centroId: "caps-movil" }), "");
});

test("ajustes: lo que muestra el formulario es lo que se guarda, aunque cambie la HSI en el mismo Guardar", () => {
  const src = leer("options.js");
  // The old bug: a number typed together with a change of HSI was wiped.
  assert.doesNotMatch(src, /institucionId: cambioDeHsi \? ""/);
  assert.match(src, /institucionId: institucionElegida\(\)/);
  // The per-center picks belong to the old HSI.
  assert.match(src, /institucionPorCentro: cambioDeHsi \? \{\} : antes\.institucionPorCentro/);
  // The operator's own address for the turnos is asked for in the same prompt.
  assert.match(src, /\.concat\(propio \? \[S\.patronDeOrigen\(propio\)\] : \[\]\)/);
  const html = leer("options.html");
  // «¿Dónde ves los turnos?» is ONE place: radios, like the HSI block.
  assert.match(html, /<input type="radio" name="turnos" value="otra" id="turnos-otra">/);
  assert.doesNotMatch(html + src, /type="checkbox" id="turnos-otra"|marca\.type = "checkbox"/);
  assert.match(src, /radio\.name = "turnos";/);
  // One card: which HSI, which institution, where the turnos are seen.
  const tarjeta = html.match(/<div class="tarjeta">([\s\S]*?)<div class="tarjeta"><fieldset>\s*<legend>¿Cómo cargo/)[1];
  for (const t of ["¿Con qué HSI trabajás?", "¿En qué institución de HSI?", "¿Dónde ves los turnos?"]) assert.ok(tarjeta.includes(t), t);
  // The «i» is the Icons8 glyph, and its guide ships inside the extension.
  assert.match(html, /<img src="icons\/info\.svg"/);
  assert.match(html, /href="guia\/guia\.html#institucion"/);
  // The version this computer runs is on screen, read from the manifest.
  assert.match(html, /<p id="version"><\/p>/);
  assert.match(src, /\$\("version"\)\.textContent = "Versión " \+ chrome\.runtime\.getManifest\(\)\.version;/);
  assert.ok(fs.existsSync(path.join(EXT, "guia/guia.html")));
  assert.match(leer("guia/guia.html"), /id="institucion"/);
});

test("la extensión solo trae permiso para la HSI de prueba; cualquier otra la habilita el operador", () => {
  assert.deepEqual(manifest.host_permissions, ["http://localhost:8280/*", "http://127.0.0.1:8280/*"]);
  assert.ok(manifest.optional_host_permissions.length > 0);
  // No static content script on any HSI: it is registered for the one
  // address the operator chose.
  for (const cs of manifest.content_scripts) {
    for (const m of cs.matches) assert.match(m, /humanrounds\.org\/admin\/|:7700\/admin\//);
  }
});

test("salud.pinamar.gob.ar llega como permiso opcional: la actualización no apaga la extensión a nadie", () => {
  // A new required host (host_permissions or a static content script) makes
  // Chrome disable the extension for everyone who has it until they accept
  // again. salud rides on optional_host_permissions instead.
  const salud = S.TURNOS_OPCIONALES.find((s) => s.id === "salud");
  assert.equal(salud.url, "https://salud.pinamar.gob.ar");
  assert.ok(manifest.optional_host_permissions.includes("https://*/*"));
  const fijos = manifest.content_scripts.flatMap((cs) => cs.matches).concat(manifest.host_permissions);
  for (const m of fijos) assert.doesNotMatch(m, /salud\.pinamar\.gob\.ar/);
  assert.deepEqual(manifest.content_scripts.flatMap((cs) => cs.matches), [
    "https://pinamar.humanrounds.org/admin/*",
    "https://demo.humanrounds.org/admin/*",
    "http://localhost:7700/admin/*",
    "http://127.0.0.1:7700/admin/*"
  ]);
});

// The service worker against a fake chrome: what the row script does on
// salud follows Chrome's permission, whoever grants or removes it.
async function trabajadorFalso({ permitidos = [], pestanias = [], local = {} } = {}) {
  const vm = await import("node:vm");
  const oyentes = {};
  const evento = (n) => ({ addListener: (f) => { (oyentes[n] = oyentes[n] || []).push(f); } });
  const registrados = new Map();
  const inyectados = [];
  const permisos = new Set(permitidos);
  const almacen = (datos = {}) => ({ get: async (d) => (typeof d === "object" ? { ...d, ...datos } : { ...datos }), set: async (v) => { Object.assign(datos, v); } });
  const chrome = {
    storage: { local: almacen(local), session: almacen(), onChanged: evento("storage") },
    action: { setBadgeText: async () => {}, setBadgeBackgroundColor: async () => {} },
    permissions: {
      contains: async ({ origins }) => origins.every((o) => permisos.has(o)),
      onAdded: evento("permAdded"), onRemoved: evento("permRemoved")
    },
    scripting: {
      getRegisteredContentScripts: async ({ ids }) => ids.filter((i) => registrados.has(i)).map((i) => registrados.get(i)),
      registerContentScripts: async (l) => { for (const s of l) registrados.set(s.id, s); },
      unregisterContentScripts: async ({ ids }) => { for (const i of ids) registrados.delete(i); },
      insertCSS: async () => {},
      executeScript: async ({ target, files }) => { inyectados.push([target.tabId, files]); }
    },
    tabs: {
      query: async ({ url }) => pestanias.filter((t) => url === "https://salud.pinamar.gob.ar/admin/*" && t.url.startsWith("https://salud.pinamar.gob.ar/admin/")),
      onRemoved: evento("tabRemoved"), onUpdated: evento("tabUpdated"), create: async () => {}
    },
    runtime: { onMessage: evento("message"), onInstalled: evento("installed"), onStartup: evento("startup"), getURL: (p) => p, openOptionsPage: () => {} },
    windows: { update: async () => {} }
  };
  // Every async call into the fake chrome is counted while it runs: a dispatched event is done
  // when none is left, not after a fixed pause.
  let pendientes = 0;
  const contar = (o) => {
    for (const k of Object.keys(o)) {
      const v = o[k];
      if (typeof v === "function" && v.constructor.name === "AsyncFunction") {
        o[k] = async (...a) => { pendientes += 1; try { return await v(...a); } finally { pendientes -= 1; } };
      } else if (v && typeof v === "object") contar(v);
    }
  };
  contar(chrome);
  const ctx = { chrome, console, URL, setTimeout, clearTimeout, Promise };
  ctx.self = ctx;
  ctx.importScripts = (f) => vm.runInContext(leer(f), ctx);
  vm.createContext(ctx);
  vm.runInContext(leer("background.js"), ctx);
  const disparar = async (n, ...a) => {
    for (const f of oyentes[n] || []) f(...a);
    await macrotarea();
    await until(() => pendientes === 0, "the worker's calls into chrome settle");
  };
  return { permisos, registrados, inyectados, disparar, chrome };
}

test("la dirección propia de los turnos: con permiso tiene el script de la fila; al cambiarla o quitarla, lo sigue", async () => {
  const local = { turnosPropio: "https://turnos.example.org" };
  const w = await trabajadorFalso({ local, permitidos: ["https://turnos.example.org/*"] });
  w.chrome.runtime.getManifest = () => manifest;
  await w.disparar("installed", { reason: "update" });
  assert.deepEqual(JSON.parse(JSON.stringify(w.registrados.get("hr-turnos-propio").matches)), ["https://turnos.example.org/admin/*"]);

  // Another address: the old registration goes, the new one comes with its permission.
  local.turnosPropio = "https://otra.example.org";
  w.permisos.add("https://otra.example.org/*");
  await w.disparar("permAdded", { origins: ["https://otra.example.org/*"] });
  assert.deepEqual(JSON.parse(JSON.stringify(w.registrados.get("hr-turnos-propio").matches)), ["https://otra.example.org/admin/*"]);

  local.turnosPropio = "";
  await w.disparar("startup");
  assert.equal(w.registrados.has("hr-turnos-propio"), false);

  // A site the manifest already names never gets a second copy.
  local.turnosPropio = "https://demo.humanrounds.org";
  w.permisos.add("https://demo.humanrounds.org/*");
  await w.disparar("startup");
  assert.equal(w.registrados.has("hr-turnos-propio"), false);
});

test("sin permiso la fila de salud no tiene script; al permitirlo aparece, también en la pestaña abierta, y se va si lo quitan", async () => {
  const w = await trabajadorFalso({ pestanias: [{ id: 7, url: "https://salud.pinamar.gob.ar/admin/today" }] });
  await w.disparar("installed", { reason: "update" });
  assert.equal(w.registrados.has("hr-turnos-salud"), false);

  w.permisos.add("https://salud.pinamar.gob.ar/*");
  await w.disparar("permAdded", { origins: ["https://salud.pinamar.gob.ar/*"] });
  // The worker lives in its own realm: compare plain copies.
  const s = JSON.parse(JSON.stringify(w.registrados.get("hr-turnos-salud")));
  assert.deepEqual(s.matches, ["https://salud.pinamar.gob.ar/admin/*"]);
  assert.deepEqual(s.js, ["lib/shared.js", "content-turnos.js"]);
  assert.equal(s.persistAcrossSessions, true);
  assert.deepEqual(JSON.parse(JSON.stringify(w.inyectados)), [[7, ["lib/shared.js", "content-turnos.js"]]]);

  // A second signal (startup, another grant) never injects twice.
  await w.disparar("startup");
  assert.equal(w.inyectados.length, 1);

  w.permisos.delete("https://salud.pinamar.gob.ar/*");
  await w.disparar("permRemoved", { origins: ["https://salud.pinamar.gob.ar/*"] });
  assert.equal(w.registrados.has("hr-turnos-salud"), false);
});

test("el script de la fila corre una sola vez por página aunque lo inyecten y lo registren", () => {
  assert.match(leer("content-turnos.js"), /if \(self\.__hrTurnos\) return;/);
});

test("el profesional se reconoce aunque HSI lo escriba APELLIDO NOMBRE con segundo nombre", () => {
  const hsi = ["FUNES ANDREA SOLEDAD", "GONZALEZ CAROLINA", "BARRERA RICARDO MARIA"];
  assert.equal(S.mejor("Andrea Funes", hsi, null, 0.99), "FUNES ANDREA SOLEDAD");
  assert.equal(S.mejor("Dra. Andrea Funes", hsi, null, 0.99), "FUNES ANDREA SOLEDAD");
  assert.equal(S.mejor("Ricardo Barrera", hsi, null, 0.99), "BARRERA RICARDO MARIA");
});

test("ante dos profesionales igual de parecidos no elige ninguno", () => {
  assert.equal(S.mejor("Juan Pérez", ["PEREZ JUAN CARLOS", "PEREZ JUAN MANUEL"], null, 0.99), null);
  assert.equal(S.mejor("Zulema Inexistente", ["FUNES ANDREA"], null, 0.99), null);
  // A shared surname alone is not a match.
  assert.equal(S.mejor("Andrea Funes", ["FUNES MARTIN"], null, 0.99), null);
});

test("sexo: M/F/X de Turnos a la opción de HSI; sin dato no adivina", () => {
  assert.equal(S.sexoHsi("M"), "Masculino");
  assert.equal(S.sexoHsi("f"), "Femenino");
  assert.equal(S.sexoHsi("X"), "X");
  assert.equal(S.sexoHsi(""), null);
  assert.equal(S.sexoHsi(null), null);
});

test("el DNI viaja sin puntos", () => {
  assert.equal(S.soloDigitos("46.911.853"), "46911853");
});

test("lo que llega al disco es la forma reducida: nombre corto, DNI de tres, documentos fuera de los mensajes", () => {
  assert.equal(S.nombreCorto("Marta Pruebauno"), "Marta P.");
  assert.equal(S.nombreCorto("Marta del Valle Juárez"), "Marta J.");
  assert.equal(S.nombreCorto("Marta"), "Marta");
  assert.equal(S.nombreCorto(null), "");
  assert.equal(S.dniCorto("90100101"), "…101");
  assert.equal(S.dniCorto("123"), "123");
  assert.equal(S.sinDnis("El paciente 46.911.853 no estaba, DNI 30701234"), "El paciente … no estaba, DNI …");
});

test("modo automático: un DNI solo trae su turno si es exactamente uno", () => {
  const turnos = [
    { id: 1, dni: "46911853" }, { id: 2, dni: "46.911.853" }, { id: 3, dni: "30111222" }
  ];
  assert.equal(S.turnoPorDni(turnos, "46911853"), null, "dos filas con el mismo documento no se eligen solas");
  assert.equal(S.turnoPorDni(turnos, "30.111.222").id, 3);
  assert.equal(S.turnoPorDni(turnos, "99999999"), null);
  assert.equal(S.turnoPorDni(turnos, "3011122"), null, "un prefijo no es un DNI");
  assert.equal(S.turnoPorDni(turnos, ""), null);
});

test("el alta parte el nombre como los campos de HSI y da vuelta la fecha", () => {
  assert.deepEqual(S.nombreYApellido("Marta Pruebauno"), { nombre: "Marta", apellido: "Pruebauno" });
  assert.deepEqual(S.nombreYApellido("Marta del Valle Juárez"), { nombre: "Marta", apellido: "del Valle Juárez" });
  assert.deepEqual(S.nombreYApellido(""), { nombre: "", apellido: "" });
  assert.equal(S.fechaDdmmaaaa("1990-05-01"), "01051990");
  assert.equal(S.fechaDdmmaaaa("2000-12-31"), "31122000");
  assert.equal(S.fechaDdmmaaaa(""), "");
});

test("sin sexo o sin nacimiento no hay alta: se nombran los datos que faltan", () => {
  const completo = { paciente: "Marta Pruebauno", sexo: "F", nacimiento: "1990-05-01" };
  assert.deepEqual(S.faltantesParaAlta(completo), []);
  assert.deepEqual(S.faltantesParaAlta({ ...completo, sexo: "" }), ["el sexo"]);
  assert.deepEqual(S.faltantesParaAlta({ ...completo, nacimiento: "" }), ["la fecha de nacimiento"]);
  assert.deepEqual(S.faltantesParaAlta({ ...completo, sexo: "", nacimiento: "" }), ["el sexo", "la fecha de nacimiento"]);
  assert.deepEqual(S.faltantesParaAlta({ ...completo, paciente: "" }), ["el nombre y apellido"]);
});

test("la cobertura de la fila se marca solo si una opción de HSI la nombra sin duda", () => {
  const texto = (o) => o.texto;
  const opciones = [
    "OBRA SOCIAL DEL PERSONAL DE LA INDUSTRIA FIDEERA / 60001 Act. 08/10/2026",
    "OSECAC / 50002 Act. 01/06/2026",
    "Actualizar Cobertura"
  ].map((t) => ({ texto: t }));
  const elegida = S.mejor("OBRA SOCIAL DEL PERSONAL DE LA INDUSTRIA FIDEERA", opciones, texto, 0.6);
  assert.ok(elegida && /FIDEERA/.test(elegida.texto), "la opción que nombra la cobertura de la fila");
  // Dos opciones igual de parecidas: no se elige ninguna.
  const ambigua = ["OSDE 210 / 1 Act. 01/01/2026", "OSDE 310 / 2 Act. 01/01/2026"].map((t) => ({ texto: t }));
  assert.equal(S.mejor("OSDE", ambigua, texto, 0.6), null);
  // Una cobertura que el paciente no tiene nunca se inventa.
  assert.equal(S.mejor("SWISS MEDICAL", opciones, texto, 0.6), null);
  // La preselección de HSI solo queda cuando ya nombra la de la fila: es un
  // default, no la mano del operador.
  assert.equal(S.coincidencia("OBRA SOCIAL DEL PERSONAL DE LA INDUSTRIA FIDEERA", "OBRA SOCIAL DEL PERSONAL DE LA INDUSTRIA FIDEERA / 60001 (Act.08/10/2026)"), 1);
  assert.ok(S.coincidencia("OBRA SOCIAL DEL PERSONAL DE LA INDUSTRIA FIDEERA", "OBRA SOCIAL DE TRABAJADORES DE ESTACIONES DE SERVICIO / 50002 (Act.08/10/2026)") < 1);
});

test("contacto: un solo campo en Turnos, teléfono o mail en HSI", () => {
  assert.deepEqual(S.contacto("ana@example.com"), { telefono: "", email: "ana@example.com" });
  assert.deepEqual(S.contacto("2254 40-1234"), { telefono: "2254401234", email: "" });
  assert.deepEqual(S.contacto(null), { telefono: "", email: "" });
});

test("teléfono: prefijo y número, sin el 0 ni el 15; sin prefijo conocido no parte", () => {
  assert.deepEqual(S.telefonoPartes("2254 15-401234"), { prefijo: "2254", numero: "401234" });
  assert.deepEqual(S.telefonoPartes("02254 402345"), { prefijo: "2254", numero: "402345" });
  assert.deepEqual(S.telefonoPartes("+54 9 2254 401234"), { prefijo: "2254", numero: "401234" });
  assert.deepEqual(S.telefonoPartes("11 4444-5555"), { prefijo: "11", numero: "44445555" });
  assert.equal(S.telefonoPartes("401234").prefijo, "");
  assert.equal(S.telefonoPartes("3875 123456").prefijo, "");
});

test("la semana en pantalla se lee del título de la agenda y se cuenta desde ahí", () => {
  const visible = S.fechaDeTitulo("lunes, 5 de octubre de 2026 - domingo, 11 de octubre de 2026");
  assert.equal(visible.getFullYear() + "-" + (visible.getMonth() + 1) + "-" + visible.getDate(), "2026-10-5");
  assert.equal(S.semanasHasta("2026-10-14", visible), 1);
  assert.equal(S.semanasHasta("2026-10-11", visible), 0);
  assert.equal(S.semanasHasta("2026-09-30", visible), -1);
  assert.equal(S.semanasHasta("2027-01-04", visible), 13);
  assert.equal(S.semanasHasta("2026-10-14", S.fechaDeTitulo("sin fecha")), null);
  assert.equal(S.fechaDeTitulo("lunes, 7 de setiembre de 2026").getMonth(), 8);
});

test("el día se ubica por nombre y número; la hora por la etiqueta de la fila", () => {
  const cabeza = ["Lunes oct 12", "Martes oct 13", "Miércoles oct 14", "Jueves oct 15", "Viernes oct 16", "Sábado oct 17", "Domingo oct 18"];
  assert.equal(S.columnaDelDia("2026-10-14", cabeza), 2);
  assert.equal(S.columnaDelDia("2026-10-17", cabeza), 5);
  assert.equal(S.columnaDelDia("2026-10-21", cabeza), -1);
  const horas = ["07:00hs.", "07:30hs.", "08:00hs.", "08:30hs."];
  assert.equal(S.filaDeLaHora("08:00", horas), 2);
  assert.equal(S.filaDeLaHora("07:30", horas), 1);
  assert.equal(S.filaDeLaHora("07:40", horas), -1);
});

test("el título de «Nuevo turno» prueba que se abrió el horario pedido", () => {
  const titulo = "miércoles, 14 de octubre de 2026 ● 07:00hs.";
  assert.equal(S.esElHorario("2026-10-14", "07:00", titulo), true);
  assert.equal(S.esElHorario("2026-10-14", "07:30", titulo), false);
  assert.equal(S.esElHorario("2026-10-15", "07:00", titulo), false);
});

test("una agenda vencida o futura no se elige para el día del turno", () => {
  const agenda = "DEMO Medicina general (Medicina general)08/10/2026 - 07/01/2027";
  assert.equal(S.agendaVigente("2026-10-14", agenda), true);
  assert.equal(S.agendaVigente("2026-10-07", agenda), false);
  assert.equal(S.agendaVigente("2027-01-08", agenda), false);
});

test("nada de datos de pacientes se registra ni sale del navegador", () => {
  for (const f of ["background.js", "content-hsi.js", "content-turnos.js", "options.js", "popup.js", "lib/shared.js", "lib/hsi-dom.js", "lib/hsi-tidy.js"]) {
    const src = leer(f);
    assert.doesNotMatch(src, /console\.(log|info|warn|error|debug)/, f + " no debe escribir en la consola");
    assert.doesNotMatch(src, /XMLHttpRequest|sendBeacon|WebSocket|new Image\(/, f + " no debe abrir conexiones");
  }
  // Four fetches, all to the panel's own origin with the operator's session:
  // reading the day queue, asking what became of the turnos loaded (ids only
  // go out; the registry on disk holds a place, never a person), saving
  // back what the operator answered when the row lacked a datum, and hiding a
  // change HSI refused (an id in the address, no body). Never an absolute address.
  const turnosSrc = leer("content-turnos.js");
  assert.equal((turnosSrc.match(/fetch\(/g) || []).length, 4);
  assert.match(turnosSrc, /fetch\(ultimaConsulta\(dia\), \{ credentials: "same-origin" \}\)/);
  assert.match(turnosSrc, /fetch\("\/api\/admin\/day-queue\/changes\?ids=" \+ ids\.join\(","\), \{ credentials: "same-origin" \}\)/);
  assert.match(turnosSrc, /var OCULTAR = "\/api\/admin\/day-queue\/changes\/\{id\}\/hide";/);
  assert.match(turnosSrc, /fetch\(OCULTAR\.replace\("\{id\}", encodeURIComponent\(c\.id\)\), \{ method: "POST", credentials: "same-origin" \}\)/);
  assert.doesNotMatch(turnosSrc, /registro\[[^\]]+\] = \{[^}]*(paciente|dni|contacto|nacimiento)/);
  assert.match(turnosSrc, /var GUARDAR_DATOS = "\/api\/admin\/requests\/\{id\}\/patient-data";/);
  assert.match(turnosSrc, /fetch\(GUARDAR_DATOS\.replace\(/);
  assert.doesNotMatch(turnosSrc, /fetch\(\s*["']https?:/);
  for (const f of ["background.js", "content-hsi.js", "options.js", "popup.js"]) assert.doesNotMatch(leer(f), /fetch\(/);
  // The turno in transit lives in session storage (memory), never on disk.
  assert.match(leer("background.js"), /chrome\.storage\.session\.set\(\{ trabajos:/);
  assert.doesNotMatch(leer("background.js"), /storage\.local\.set\([^)]*turno/);
});

// Contraparte técnica, 2026-10-19: el turno recién creado en «Crear turno» se carga en HSI
// desde el mismo cartel, aunque sea de otro día que el de «Pacientes de hoy».
test("el botón de «Turno creado» lee la cola de SU día, y esa lectura no pisa la de pantalla", () => {
  const src = leer("content-turnos.js");
  assert.match(src, /alTocar\(boton\.getAttribute\("data-hr-hsi"\), boton\.getAttribute\("data-hr-hsi-dia"\)\)/);
  assert.match(src, /function ultimaConsulta\(dia\) \{\s*if \(dia && \/\^\\d\{4\}-\\d\{2\}-\\d\{2\}\$\/\.test\(dia\)\) return "\/api\/admin\/day-queue\?dia=" \+ dia;/);
  assert.match(src, /function buscarTurno\(id, dia\) \{\s*return buscarCola\(dia\)\.then\(function \(cola\) \{\s*if \(!dia\) reportarCola\(cola\);/);
  assert.match(src, /function manualmente\(id, dia\) \{[\s\S]{0,120}?buscarTurno\(id, dia\)/);
});

// Responsable del producto, 2026-10-27 (d1122568): aprobado o creado, el turno va solo a HSI si
// la mesa lo eligió; mientras viaja dice «Envío automático a HSI».
test("«Cargar en HSI apenas apruebo o creo un turno» nace apagado y no existe en «Solo copiar el DNI»", () => {
  assert.equal(S.DEFAULTS.cargarAlAprobar, false);
  const html = leer("options.html");
  assert.match(html, /<label class="opcion" id="fila-al-aprobar">\s*<input type="checkbox" id="cargarAlAprobar">/);
  const src = leer("options.js");
  // Hidden in manual mode, and saved only while it is on screen.
  assert.match(src, /function mostrarAlAprobar\(\)/);
  assert.match(src, /cargarAlAprobar: /);
});

test("lo recién aprobado o creado va solo, de a uno, y nunca dos veces", () => {
  const src = leer("content-turnos.js");
  // Only the panel's marked controls, only with the choice on and not in manual mode.
  assert.match(src, /var solos = alAprobar && modo !== "manual";/);
  assert.match(src, /querySelectorAll\("\[data-hr-hsi\]\[data-hr-hsi-auto\]"\)/);
  // Never one already sent, skipped while the choice was off, saved, or under way.
  assert.match(src, /if \(!id \|\| disparados\[id\] \|\| saltados\[id\] \|\| hechos\[id\] \|\| estados\[id\]\) return;/);
  assert.match(src, /if \(!solos\) \{ saltados\[id\] = true; return; \}/);
  // HSI takes one at a time: the rest wait «en cola» and go when a load ends.
  assert.match(src, /if \(ocupado\(\) \|\| enCola\.length\) \{\s*enCola\.push\(/);
  assert.match(src, /if \(ACTIVOS\.indexOf\(estado\) === -1 && estado !== "en-cola"\) setTimeout\(siguiente, 0\);/);
  assert.match(src, /"en-cola": "Envío automático a HSI"/);
  assert.match(src, /var AUTO = "Envío automático a HSI";/);
  // The panel's own hooks get this computer's state, and the page says the extension is here.
  assert.match(src, /querySelectorAll\("\[data-hr-hsi\]:not\(\[data-hr-visto\]\)"\)/);
  assert.match(src, /document\.documentElement\.dataset\.hrHumanizador = "1";/);
  // Never the control's own hook on <html>: the scan for hooks would paint the whole page.
  assert.doesNotMatch(src, /documentElement\.dataset\.hrHsi\b/);
  assert.match(src, /pedido\.cargarAlAprobar = false;/);
});

test("Confirmar solo se aprieta por la extensión con «guardar solo» encendido", () => {
  const src = leer("content-hsi.js");
  const clics = src.match(/botonConfirmar\.click\(\)/g) || [];
  assert.equal(clics.length, 1);
  assert.match(src, /if \(actual\.ajustes\.guardarSolo\) \{\s*return respiro\(\)\.then\(function \(\) \{\s*botonConfirmar\.click\(\);/);
  // …and that switch is only ever turned on by the operator's own answer.
  const encendidos = (src.match(/guardarSolo: true/g) || []).length;
  assert.equal(encendidos, 1);
  assert.match(src, /texto: "Sí, guardá solo"[\s\S]{0,120}guardarSolo: true/);
});

test("el alta: «Buscar» se aprieta en todos los modos; «Guardar» solo con «guardar solo»", () => {
  const src = leer("content-hsi.js");
  // «Buscar» only reads: its press carries no ajustes condition.
  const busqueda = src.match(/var buscar = D\.botonBuscarPaciente\(\);[\s\S]{0,400}?buscar\.click\(\);/);
  assert.ok(busqueda, "el alta aprieta el Buscar de HSI");
  assert.doesNotMatch(busqueda[0], /guardarSolo/, "Buscar no depende de «guardar solo»");
  // «Guardar» writes the patient: the press only happens past the branch
  // that hands the alta to the operator, and the retry only when the press
  // was ours.
  assert.match(src, /if \(!actual\.ajustes\.guardarSolo\) \{[\s\S]{0,700}?return vigilarElAlta\(false\);\s*\}\s*return respiro\(\)[\s\S]{0,600}?guardar\.click\(\)/,
    "el primer Guardar va después de descartar «guardar solo»");
  assert.match(src, /if \(nuestro[^)]*\)[\s\S]{0,400}?guardar\.click\(\)/,
    "el reintento de Guardar solo cuando la prensa fue de la extensión");
  // The continuation rides on HSI's own evidence: its notice about the
  // patient it created, or the new patient's profile.
  assert.match(src, /\/se creó\/i/);
  assert.match(src, /pacientes\\\/profile\\\/\\d\+/);
});

test("el alta no queda colgada: HSI en la búsqueda con un aviso de error pasa a manos del operador", () => {
  const src = leer("content-hsi.js");
  // After «Buscar», an error notice while still on HSI's search screen (the
  // test HSI's Renaper is down and its warning keeps coming back) is not a
  // dead end: the job hands the alta over quoting HSI's own answer.
  assert.match(src, /if \(enAltaBusqueda\(\)\) \{[\s\S]{0,200}?return \{ aviso: mal\.texto \};/);
  assert.match(src, /function esperarAltaConAviso/);
  assert.match(src, /estado\("alta", propio \|\| "HSI respondió: " \+ textoHsi\)/);
  assert.match(src, /Volvé a tocar «Buscar» o completá el alta a mano/);
  // And it keeps watching: the patient's profile, its card or the new-patient
  // screen resume the turno with the same job — nobody presses the row again.
  assert.match(src, /function esperarAltaConAviso\(textoHsi, propio\) \{[\s\S]{0,900}?enPerfilDelPaciente\(\) \|\| avisoPacienteCreado\(\)[\s\S]{0,400}?tarjetaPaciente\(dni\)/);
});

test("un paciente parecido nunca cuenta como el paciente: solo la tarjeta con su mismo DNI", () => {
  const src = leer("content-hsi.js");
  // HSI lists people with the same name and birth date after a search; taking
  // one of them for the patient loads a turno onto the wrong person.
  assert.doesNotMatch(src, /tarjetaPaciente\(\)/, "tarjetaPaciente siempre recibe el DNI del turno");
  assert.match(src, /hayParecidos\(turno\.dni\)\) return \{ parecidos: true \}/);
  assert.match(src, /HSI encontró pacientes parecidos/);
  const dom = leer("lib/hsi-dom.js");
  assert.match(dom, /function tarjetaPaciente\(dni\) \{[\s\S]{0,300}?if \(!d\) return null;/);
});

test("un fallo dentro de «bien» llega a «mal»: el panel no queda en «cargando» cuando HSI no confirma", () => {
  const src = leer("content-hsi.js");
  // `bien` throws a Fallo when HSI did not confirm the turno. A rejection
  // handler placed beside `bien` in `.then(bien, mal)` never sees that throw,
  // so the panel stays on «cargando» for good.
  assert.match(src, /function bien\(r, mio\) \{[\s\S]{0,900}?throw new Fallo\(/);
  assert.doesNotMatch(src, /\.then\(function \(r\) \{ return bien\(r, mio\); \}, function/, "bien y mal en el mismo then");
  const rutas = src.match(/return bien\(r, mio\); \}\)\.catch\(function \(e\) \{ return mal\(e, mio\); \}\)/g) || [];
  assert.equal(rutas.length, 2, "la carga normal (correr) y el modo automático (empezarAuto) atrapan el fallo de bien");
  // The changes road already did it right: the ending's throw reaches `mal`.
  assert.match(src, /bienCambio\(r, mio\); \}\)\.then\(null, function \(e\) \{ return mal\(e, mio\); \}\)/);
});

test("el flujo de «bien» y «mal» sobre una promesa: un Fallo lanzado por «bien» termina en «mal»", async () => {
  // The same chain shape as `correr`/`empezarAuto`, run for real.
  class Fallo extends Error {}
  const visto = [];
  const bien = () => { throw new Fallo("HSI no confirmó el turno."); };
  const mal = (e) => { visto.push(e.message); return Promise.resolve(); };
  await Promise.resolve({ guardado: false }).then((r) => bien(r)).catch((e) => mal(e));
  assert.deepEqual(visto, ["HSI no confirmó el turno."]);
  // …and the old shape loses it, which is the bug this pins.
  const perdido = [];
  await Promise.resolve({ guardado: false }).then((r) => bien(r), (e) => mal(e)).catch((e) => perdido.push(e.message));
  assert.deepEqual(perdido, ["HSI no confirmó el turno."]);
});

test("una sola HSI a la vez: antes de abrir otro turno responden TODAS las pestañas de HSI", () => {
  const src = leer("background.js");
  assert.match(src, /tabs\.sort\(function \(x, y\) \{ return x\.id - y\.id; \}\)/, "la pestaña más vieja primero: el turno no salta de pestaña");
  assert.match(src, /respuestas\.some\(function \(r\) \{ return r && r\.ocupado; \}\)/, "un trabajo en cualquiera de las pestañas frena al siguiente");
  assert.doesNotMatch(src, /tabs\[0\] \|\| null/, "mirar sólo la primera pestaña fue lo que partió una carga en dos");
});

// Same walk and digest as scripts/build_extension.py.
function archivosQueViajan(dir, rel = "") {
  const out = [];
  for (const nombre of fs.readdirSync(path.join(dir, rel)).sort()) {
    const r = rel ? rel + "/" + nombre : nombre;
    if (fs.statSync(path.join(dir, r)).isDirectory()) {
      if (nombre !== "test" && nombre !== "store") out.push(...archivosQueViajan(dir, r));
    } else if (nombre !== ".DS_Store" && nombre !== "README.md") out.push(r);
  }
  return out;
}

test("cambios por pasar a HSI: cancelada, movida (día, hora o médico) y presente; nada si HSI ya está al día", () => {
  const cargado = { dia: "2026-10-29", hora: "09:00", medico: "Dra. Pérez" };
  const ahora = { dia_iso: "2026-10-29", hora: "09:00", medico: "Dra. Pérez", asistencia: null };
  const registro = { 1: cargado, 2: cargado, 3: cargado, 4: cargado, 5: cargado, 6: cargado, 7: { ...cargado, cancelado: true }, 8: { ...cargado, presente: true } };
  const cambios = [
    { id: 1, estado: "cancelada", turno: ahora },
    { id: 2, estado: "aprobada", turno: { ...ahora, hora: "10:30" } },
    { id: 3, estado: "aprobada", turno: { ...ahora, dia_iso: "2026-11-02" } },
    { id: 4, estado: "aprobada", turno: { ...ahora, medico: "Dr. Gómez" } },
    { id: 5, estado: "aprobada", turno: { ...ahora, asistencia: "presente" } },
    { id: 6, estado: "aprobada", turno: ahora },
    { id: 7, estado: "cancelada", turno: ahora },
    { id: 8, estado: "aprobada", turno: { ...ahora, asistencia: "presente" } },
    { id: 9, estado: "cancelada", turno: ahora },      // never loaded by this computer
    { id: 10, estado: null, turno: null },
  ];
  assert.deepEqual(S.cambiosPorPasar(registro, cambios).map((c) => [c.id, c.accion]),
    [[1, "cancelar"], [2, "mover"], [3, "mover"], [4, "mover"], [5, "presente"]]);
  // A move wins over attendance: the turno goes to its new place first.
  assert.equal(S.cambiosPorPasar({ 1: cargado }, [{ id: 1, estado: "aprobada", turno: { ...ahora, hora: "11:00", asistencia: "presente" } }])[0].accion, "mover");
  // The old slot travels with the change: it is what is looked for in HSI.
  assert.deepEqual(S.cambiosPorPasar(registro, cambios)[1].antes, cargado);
});

test("cambios: un turno cancelado en HSI y aprobado de nuevo solo se crea; el turno de cambio y el lugar recordado", () => {
  const lugar = { dia: "2026-10-29", hora: "09:00", medico: "Dra. Pérez" };
  const t = { id: 5, dia_iso: "2026-11-02", hora: "10:30", medico: "Dra. Pérez", paciente: "Ana Gómez", dni: "12.345.678", sexo: "F", contacto: "223 555", nacimiento: "1990-01-02", cobertura: "PAMI", servicio: "Clínica", especialidad: "Clínica médica", centro: "Hospital", centro_id: "hospital", hsi_institucion: "Inst. 12" };
  const r = S.cambiosPorPasar({ 5: { ...lugar, cancelado: true } }, [{ id: 5, estado: "aprobada", turno: t }]);
  assert.deepEqual(r.map((c) => c.accion), ["crear"]);
  // Loaded like any turno, at its new place; the old place is what cancel/present look for.
  const nuevo = S.turnoDeCambio(t);
  assert.equal(nuevo.dia, "2026-11-02");
  assert.equal(nuevo.hora, "10:30");
  assert.equal(nuevo.dni, "12345678");
  assert.equal(nuevo.institucion, "12");
  assert.equal(nuevo.centroId, "hospital");
  const viejo = S.turnoDeCambio(t, lugar);
  assert.deepEqual([viejo.dia, viejo.hora, viejo.medico], ["2026-10-29", "09:00", "Dra. Pérez"]);
  assert.deepEqual(S.lugarDeTurno(nuevo), { dia: "2026-11-02", hora: "10:30", medico: "Dra. Pérez" });
  assert.deepEqual(S.lugarDeTurno({ dia: "2026-11-02", hora: "10:30" }), { dia: "2026-11-02", hora: "10:30", medico: "" });
  assert.equal(S.diaYHora("2026-11-02", "10:30"), "02/11 10:30");
  assert.equal(S.diaYHora("", "10:30"), "10:30");
});

test("cambios: el turno de la grilla se reconoce por su hora y el de la ventana por su DNI", () => {
  assert.equal(S.horaDeEvento("10:00 PRUEBAUNO, MARTA."), "10:00");
  assert.equal(S.horaDeEvento("  9:30 Gómez, Ana"), "09:30");
  assert.equal(S.horaDeEvento("PRUEBAUNO 10:00"), "");
  assert.equal(S.horaDeEvento(null), "");
  assert.equal(S.dniDeVentana("Turno DNI 90100101 - ID 1"), "90100101");
  assert.equal(S.dniDeVentana("dni 12.345.678 - ID 7"), "12345678");
  assert.equal(S.dniDeVentana("sin documento"), "");
});

test("cambios: sin registro, sin cambios o con datos a medias no se inventa nada", () => {
  const ahora = { dia_iso: "2026-10-29", hora: "09:00", medico: "Dra. Pérez", asistencia: null };
  // Nothing remembered or nothing answered: nothing to pass on.
  assert.deepEqual(S.cambiosPorPasar(null, [{ id: 1, estado: "cancelada", turno: ahora }]), []);
  assert.deepEqual(S.cambiosPorPasar({ 1: { dia: "2026-10-29", hora: "09:00" } }, null), []);
  assert.deepEqual(S.cambiosPorPasar(undefined, undefined), []);
  // A state that is neither cancelled nor approved (pending) leaves HSI as it is.
  assert.deepEqual(S.cambiosPorPasar({ 1: { dia: "2026-10-29", hora: "09:00", medico: "Dra. Pérez" } },
    [{ id: 1, estado: "pendiente", turno: ahora }]), []);
  // A turno loaded without a doctor never counts as moved by doctor; day or hour still do.
  const sinMedico = { 1: { dia: "2026-10-29", hora: "09:00" } };
  assert.deepEqual(S.cambiosPorPasar(sinMedico, [{ id: 1, estado: "aprobada", turno: { ...ahora, medico: "Dr. Gómez" } }]), []);
  assert.deepEqual(S.cambiosPorPasar(sinMedico, [{ id: 1, estado: "aprobada", turno: { ...ahora, medico: undefined } }]), []);
  assert.equal(S.cambiosPorPasar(sinMedico, [{ id: 1, estado: "aprobada", turno: { ...ahora, hora: "09:30" } }])[0].accion, "mover");
  // A doctor remembered but gone from the answer is a move to «no doctor».
  assert.equal(S.cambiosPorPasar({ 1: { dia: "2026-10-29", hora: "09:00", medico: "Dra. Pérez" } },
    [{ id: 1, estado: "aprobada", turno: { ...ahora, medico: undefined } }])[0].accion, "mover");
  // A row with only an id still gives a loadable turno, with empty text fields and no document.
  const vacio = S.turnoDeCambio({ id: 9, dia_iso: "2026-11-02", hora: "10:30" });
  assert.deepEqual(vacio, { id: 9, dia: "2026-11-02", hora: "10:30", paciente: "", dni: "", sexo: "", contacto: "", nacimiento: "",
    cobertura: "", servicio: "", medico: "", especialidad: "", centro: "", centroId: "", institucion: "" });
  // The place wins over the row's own day, hour and doctor.
  const mudado = S.turnoDeCambio({ id: 9, dia_iso: "2026-11-02", hora: "10:30", medico: "Dr. Gómez" }, { dia: "2026-10-29", hora: "09:00" });
  assert.deepEqual([mudado.dia, mudado.hora, mudado.medico], ["2026-10-29", "09:00", "Dr. Gómez"]);
  // A day without an hour is just the day; no day is just the hour; neither is empty.
  assert.equal(S.diaYHora("2026-11-02", ""), "02/11");
  assert.equal(S.diaYHora("2026-11-02"), "02/11");
  assert.equal(S.diaYHora("", ""), "");
  assert.equal(S.diaYHora(), "");
  assert.equal(S.dniDeVentana(null), "");
  assert.equal(S.dniDeVentana(undefined), "");
});

// 2026-11-05: en la PC de la mesa quedaron tres cancelados de días pasados con «Cancelar en
// HSI…»; HSI no deja cancelar un turno que ya pasó, y el panel no tenía cómo cerrarse.
test("cambios: lo que ya pasó no va a HSI; la hora es la de la instalación", () => {
  const ahora = "2026-11-05 09:49";
  const lugar = (dia, hora) => ({ dia, hora, medico: "Dra. Pérez" });
  const turno = (dia_iso, hora, extra) => ({ dia_iso, hora, medico: "Dra. Pérez", asistencia: null, ...extra });
  const registro = {
    1: lugar("2026-11-04", "10:20"),   // cancelled, yesterday
    2: lugar("2026-11-05", "08:00"),   // cancelled, earlier today
    3: lugar("2026-11-05", "10:00"),   // cancelled, later today
    4: lugar("2026-11-20", "09:00"),   // cancelled, in two weeks
    5: lugar("2026-11-04", "10:00"),   // moved from yesterday to the future: only the new place loads
    6: lugar("2026-11-20", "09:00"),   // moved from the future to yesterday: only the old place frees
    7: lugar("2026-11-03", "09:00"),   // moved between two past days: nothing
    8: { ...lugar("2026-11-02", "10:40"), cancelado: true },  // approved again, for a past day
    9: { ...lugar("2026-11-02", "10:40"), cancelado: true },  // approved again, for the future
    10: lugar("2026-11-05", "08:00"),  // arrived today, earlier: still marked
    11: lugar("2026-11-04", "08:00"),  // arrived yesterday: that agenda is closed in HSI
    12: lugar("2026-11-20", "09:00"),  // moved inside the future: an ordinary move
  };
  const cambios = [
    { id: 1, estado: "cancelada", turno: turno("2026-11-04", "10:20") },
    { id: 2, estado: "cancelada", turno: turno("2026-11-05", "08:00") },
    { id: 3, estado: "cancelada", turno: turno("2026-11-05", "10:00") },
    { id: 4, estado: "cancelada", turno: turno("2026-11-20", "09:00") },
    { id: 5, estado: "aprobada", turno: turno("2026-11-20", "09:00") },
    { id: 6, estado: "aprobada", turno: turno("2026-11-04", "09:00") },
    { id: 7, estado: "aprobada", turno: turno("2026-11-04", "09:00") },
    { id: 8, estado: "aprobada", turno: turno("2026-11-04", "11:00") },
    { id: 9, estado: "aprobada", turno: turno("2026-11-20", "11:00") },
    { id: 10, estado: "aprobada", turno: turno("2026-11-05", "08:00", { asistencia: "presente" }) },
    { id: 11, estado: "aprobada", turno: turno("2026-11-04", "08:00", { asistencia: "presente" }) },
    { id: 12, estado: "aprobada", turno: turno("2026-11-21", "09:00") },
  ];
  const r = S.cambiosPorPasar(registro, cambios, ahora);
  assert.deepEqual(r.map((c) => [c.id, c.accion]),
    [[3, "cancelar"], [4, "cancelar"], [5, "crear"], [6, "cancelar"], [9, "crear"], [10, "presente"], [12, "mover"]]);
  // A move that can only go half way is still told as a move.
  assert.deepEqual(r.filter((c) => c.movido).map((c) => c.id), [5, 6]);
  // An old panel that does not say the hour, and no clock given: everything, as before.
  assert.equal(S.cambiosPorPasar(registro, cambios).length, 12);

  // Past: an earlier day, or today before this minute. A day without an hour: only an earlier day.
  assert.equal(S.yaPaso("2026-11-04", "23:59", ahora), true);
  assert.equal(S.yaPaso("2026-11-05", "09:48", ahora), true);
  assert.equal(S.yaPaso("2026-11-05", "9:30", ahora), true);
  assert.equal(S.yaPaso("2026-11-05", "09:49", ahora), false);
  assert.equal(S.yaPaso("2026-11-05", "", ahora), false);
  assert.equal(S.yaPaso("2026-11-04", "", ahora), true);
  assert.equal(S.yaPaso("2026-11-06", "08:00", ahora), false);
  assert.equal(S.yaPaso("", "08:00", ahora), false);
  assert.equal(S.yaPaso("2026-11-04", "08:00", ""), false);
  // The hour the panel said, a while later; and the computer's own clock when it said none.
  assert.equal(S.ahoraMas("2026-11-05 23:50", 15 * 60 * 1000), "2026-11-06 00:05");
  assert.equal(S.ahoraMas("2026-11-05 09:49", 0), "2026-11-05 09:49");
  assert.equal(S.ahoraMas("", 1000), "");
  assert.equal(S.ahoraLocal(new Date(2026, 10, 5, 9, 7)), "2026-11-05 09:07");
});

test("cambios: un rechazo de HSI por un turno que ya pasó no es un error; lo vigente sí", () => {
  const c = { id: 3, estado: "cancelada", accion: "cancelar",
    antes: { dia: "2026-11-05", hora: "10:00", medico: "Dra. Pérez" },
    turno: { dia_iso: "2026-11-05", hora: "10:00", medico: "Dra. Pérez", asistencia: null } };
  assert.equal(S.cambioPasado(c, "2026-11-05 09:49"), false);
  assert.equal(S.cambioPasado(c, "2026-11-05 10:01"), true);
  assert.equal(S.cambioPasado(c, ""), false);
  // A move whose old place passed meanwhile still has its new place to load: not past.
  const mover = { ...c, estado: "aprobada", accion: "mover", turno: { ...c.turno, dia_iso: "2026-11-20" } };
  assert.equal(S.cambioPasado(mover, "2026-11-05 10:01"), false);
  assert.equal(S.cambioPasado(mover, "2026-11-21 08:00"), true);
});

test("cambios: lo ocultado no vuelve hasta que el turno cambie otra vez", () => {
  const lugar = { dia: "2026-11-20", hora: "09:00", medico: "Dra. Pérez" };
  const t = { dia_iso: "2026-11-20", hora: "09:00", medico: "Dra. Pérez", asistencia: null };
  const ahora = "2026-11-05 09:49";
  const c = { id: 1, estado: "cancelada", turno: t };
  assert.deepEqual(S.cambiosPorPasar({ 1: lugar }, [c], ahora).map((x) => x.accion), ["cancelar"]);
  // Hidden for the whole desk: the panel says so.
  assert.deepEqual(S.cambiosPorPasar({ 1: lugar }, [{ ...c, oculto: true }], ahora), []);
  // Hidden on this computer only (a panel that does not keep it yet): its mark is the change.
  const marca = S.firmaDeCambio(c);
  assert.deepEqual(S.cambiosPorPasar({ 1: { ...lugar, oculto: marca } }, [c], ahora), []);
  // The turno changes again -- approved and moved to another day: a new change, it comes back.
  const movido = { id: 1, estado: "aprobada", turno: { ...t, dia_iso: "2026-11-21" } };
  assert.notEqual(S.firmaDeCambio(movido), marca);
  assert.deepEqual(S.cambiosPorPasar({ 1: { ...lugar, oculto: marca } }, [movido], ahora).map((x) => x.accion), ["mover"]);
  // The change in the panel carries what its mark is made of.
  assert.equal(S.firmaDeCambio(S.cambiosPorPasar({ 1: lugar }, [c], ahora)[0]), marca);
});

test("el zip que se le entrega a la mesa está al día con el código", () => {
  const dist = path.join(EXT, "../dist");
  assert.ok(fs.existsSync(path.join(dist, "hsi-humanizador.zip")), "falta el zip — corré scripts/build_extension.py");
  // The store's package carries manifest.json at its root, never in a folder.
  const tienda = fs.readFileSync(path.join(dist, "hsi-humanizador-store.zip")).toString("latin1");
  assert.ok(tienda.includes("manifest.json") && !tienda.includes("hsi-humanizador/manifest.json"));
  const h = crypto.createHash("sha256");
  for (const r of archivosQueViajan(EXT).sort()) {
    h.update(Buffer.from(r + "\0", "utf8"));
    h.update(fs.readFileSync(path.join(EXT, r)));
    h.update(Buffer.from("\0"));
  }
  const marca = fs.readFileSync(path.join(dist, "hsi-humanizador.sha256"), "utf8").trim();
  assert.equal(marca, h.digest("hex"), "el zip quedó viejo — corré scripts/build_extension.py");
});

test("«Cargar en HSI»: la fecha de nacimiento vacía no bloquea la carga; el sexo vacío sí", () => {
  // content-turnos.js is a page script (an IIFE over the DOM): take the one
  // function under test out of its own source and run it with the real shared lib.
  const src = leer("content-turnos.js");
  const m = src.match(/function camposQueFaltan\(turno\) \{[\s\S]*?\n  \}\n/);
  assert.ok(m, "camposQueFaltan ya no está en content-turnos.js");
  const camposQueFaltan = new Function("S", m[0] + "return camposQueFaltan;")(S);
  const completo = { paciente: "Ana Pérez", sexo: "F", nacimiento: "1990-05-01" };
  const claves = (t) => camposQueFaltan(t).map((c) => c.clave);
  assert.deepEqual(claves(completo), []);
  // A patient HSI already has needs no birth date; one it lacks gets it asked in the alta.
  assert.deepEqual(claves({ ...completo, nacimiento: "" }), []);
  assert.deepEqual(claves({ ...completo, nacimiento: undefined }), []);
  assert.deepEqual(claves({ ...completo, nacimiento: "no es fecha" }), []);
  // The sex and the name still block: HSI finds a patient by DNI + sex.
  assert.deepEqual(claves({ ...completo, sexo: "" }), ["sexo"]);
  assert.deepEqual(claves({ ...completo, paciente: " " }), ["paciente"]);
  assert.deepEqual(claves({ paciente: "", sexo: "", nacimiento: "" }), ["paciente", "sexo"]);
  // The alta still asks for it (content-hsi.js pedirDatosDelAlta).
  assert.match(leer("content-hsi.js"), /clave: "nacimiento", etiqueta: "Fecha de nacimiento"/);
});
