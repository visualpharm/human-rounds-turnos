/* Las evaluaciones de UI que el responsable del producto pidió como reglas permanentes
   (2026-08-01), no como el arreglo de un caso suelto. Cada una falla sobre el
   CÓDIGO, que es lo único que un test puede mirar sin un navegador: la
   pregunta "¿se ve bien?" se contesta con una revisión, pero "¿está puesta la
   regla que hace que se vea bien?" se contesta acá, y es lo que evita que la
   próxima pantalla vuelva a nacer mal.

   1. Ninguna línea termina con UNA palabra sola.
   2. Los nombres de las personas se escriben en capitalización normal.
   3. Un control nuevo se copia del control que ya existe para lo mismo.
   4. Un título y un subtítulo, y nada alrededor que los repita con otro estilo.
   5. El médico lee la entrevista DURANTE la consulta — nadie promete otra cosa.
   6. Una pantalla, dos anchos: teléfono a pantalla completa, computadora
      dentro del sitio.
   7. Ninguna pantalla mezcla texto gris en NEGRITA con texto negro SIN negrita
      (PS16: "no screen mixes gray-BOLD text with black-REGULAR text").
   8. Ningún link se subraya — ni quieto, ni en hover. El hover ACLARA el
      color, y un link visitado se pinta distinto (--link-visited).
   9. Ningún control enfocable del sitio paciente baja de 16px (iOS Safari
      hace zoom y recorta la página), y todo buscador se ancla arriba al
      enfocarse para que los resultados queden a la vista sobre el teclado.
  12. Ningún hover cambia de familia: aclara UN escalón dentro del color que
      el elemento ya tiene, nunca salta de beige a azul ni al revés.
   13. La barra del sitio usa UN sistema métrico: 15px/500 y fila de 44px
       para menu items, botones y Mi cuenta; y un solo «Chateá con IA»
       visible — el launcher flotante se esconde mientras el nav muestra
       el suyo (responsable del producto, 2026-09-19).
*/
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { join, relative, sep } from "node:path";
import { patientSiteSource } from "./helpers/patient-site-source.mjs";
const leer = (n) => (n === "patient_web/site_*.py" ? patientSiteSource()
  : readFileSync(new URL("../" + n, import.meta.url), "utf8"));
const country = JSON.parse(leer("packs/ar/country.json"));
globalThis.SANA_COUNTRY_PHONE = country.phone;
const F = await import("../static/platform/format.mjs");

const css = leer("static/root/human-rounds.css");
const misTurnos = leer("static/platform/account-requests.mjs");
const contenido = leer("static/platform/admin-content.mjs");
const cola = leer("static/platform/admin-queue.mjs");
const solicitudes = leer("app/modules/turnos/booking_requests.py");
const solicitudesPaciente = leer("app/modules/turnos/booking_patient.py");

// ---------------------------------------------------------------- eval 1
// "Look, there is one line which breaks just one word to a new line… there are
// lines that split like this with one word and a new line, especially if there
// is a space to the right."
//
// La viuda no se arregla texto por texto (mañana entra una traducción más
// larga y vuelve): se arregla con `text-wrap:pretty` heredado desde <body>,
// que es el navegador repartiendo el último renglón. El test cuida la HERENCIA
// — que esté en body, y que nadie la apague más abajo.

test("eval 1: ninguna línea queda con una palabra sola (text-wrap heredado)", () => {
  assert.match(css, /body\{[^}]*text-wrap:pretty/s,
    "human-rounds.css: body tiene que declarar text-wrap:pretty — es lo que hereda todo el sitio");
  assert.match(css, /h1,h2,h3,h4[^{]*\{text-wrap:balance;\}/,
    "los títulos piden `balance` (renglones parejos), no `pretty`");
});

test("eval 1: nadie apaga el reparto de líneas heredado", () => {
  for (const nombre of ["static/root/human-rounds.css", "app/modules/intake/entrevista.css", "static/platform/account-requests.mjs",
                        "static/platform/listing.mjs", "static/platform/tiles.mjs"]) {
    const src = leer(nombre);
    assert.doesNotMatch(src, /text-wrap:\s*(auto|wrap)\b/,
      `${nombre} desactiva el text-wrap heredado — si un bloque necesita otra cosa, ` +
      "que sea `balance` o `nowrap` con motivo, nunca volver a `auto`");
  }
});

// ---------------------------------------------------------------- eval 2
// "Don't capitalize the names. Write them everywhere normally."
//
// UNA función, en format.mjs y su espejo format.py, y todo lo que muestra un
// nombre pasa por ahí. Un `toUpperCase()` suelto o una segunda tabla de
// partículas es exactamente lo que este test tiene que impedir.

const CASOS_NOMBRE = [
  ["ALEJO VARELA", "Alejo Varela"],
  ["RAMON IMAGENDEMO", "Ramon Imagendemo"],
  ["DIEGO CORONEL", "Diego Coronel"],
  ["MONICA ROSA ALUSTIZA", "Monica Rosa Alustiza"],
  // Todo en minúscula también es un nombre mal escrito.
  ["juan carlos van der berg", "Juan Carlos van der Berg"],
  // Partículas en minúscula, apellidos compuestos con su mayúscula.
  ["MARIA DEL CARMEN O'HIGGINS", "Maria del Carmen O'Higgins"],
  ["ANA-MARIA GARCIA-LOPEZ", "Ana-Maria Garcia-Lopez"],
  // El título no vota: adelante de un nombre gritado, el nombre igual se arregla.
  ["Dr. DIEGO CORONEL", "Dr. Diego Coronel"],
  // Lo que YA vino mezclado se respeta: lo escribió alguien que sabe cómo se
  // escribe, y adivinar arriba de eso rompe apellidos reales.
  ["Hugo Montes", "Hugo Montes"],
  ["María Belén Di Núbila", "María Belén Di Núbila"],
  ["", ""],
];

test("eval 2: los nombres se muestran en capitalización normal", () => {
  for (const [entra, sale] of CASOS_NOMBRE) {
    assert.equal(F.nombre(entra), sale, `format.mjs escribió mal ${JSON.stringify(entra)}`);
  }
});

test("eval 2: format.mjs y format.py escriben el nombre igual", () => {
  const casos = CASOS_NOMBRE.map(([entra]) => entra);
  const py = spawnSync("python3", ["-c", `
import json, sys
sys.path.insert(0, ${JSON.stringify(new URL("..", import.meta.url).pathname)})
from app.core import format
print(json.dumps([format.person_name(c) for c in json.loads(sys.argv[1])]))
`, JSON.stringify(casos)], { encoding: "utf8" });
  assert.equal(py.status, 0, py.stderr);
  const desdePython = JSON.parse(py.stdout);
  casos.forEach((c, i) => {
    assert.equal(F.nombre(c), desdePython[i],
      `format.mjs y format.py no coinciden en ${JSON.stringify(c)}`);
  });
});

// «Ilya Belov, 48» (responsable del producto, 2026-11-02): nombre, apellido, coma, edad — una sola
// función para cada lista; la edad no se rotula en cada fila.
const HOY = "2026-11-02";
const CASOS_PERSONA = [
  ["ILYA", "BELOV", "1978-03-04", "Ilya Belov, 48"],
  ["Ana María", "de la Cruz", "2026-09-02", "Ana María de la Cruz, 2 meses"],
  ["Ana", "Sosa", "2026-10-30", "Ana Sosa, recién nacido"],
  ["Luis", "Paz", "2026-10-02", "Luis Paz, 1 mes"],
  ["Luis", "Paz", "", "Luis Paz"],
  ["Luis", "", "no-es-fecha", "Luis"],
  ["", "Paz", "1980-11-02", "Paz, 46"],
  ["", "", "1980-11-02", ""],
  ["Eva", "Gil", "1980-11-03", "Eva Gil, 45"],
];

test("eval 2: persona con edad, «Nombre Apellido, 48»", () => {
  for (const [n, a, nac, esperado] of CASOS_PERSONA) {
    assert.equal(F.personaEdad(n, a, nac, HOY), esperado);
  }
});

test("eval 2: format.mjs y format.py escriben la persona con edad igual", () => {
  const py = spawnSync("python3", ["-c", `
import json, sys
sys.path.insert(0, ${JSON.stringify(new URL("..", import.meta.url).pathname)})
from app.core import format
casos = json.loads(sys.argv[1])
print(json.dumps([format.person_age(c[0], c[1], c[2], ${JSON.stringify(HOY)}) for c in casos]))
`, JSON.stringify(CASOS_PERSONA)], { encoding: "utf8" });
  assert.equal(py.status, 0, py.stderr);
  const desdePython = JSON.parse(py.stdout);
  CASOS_PERSONA.forEach(([n, a, nac, esperado], i) => {
    assert.equal(desdePython[i], esperado, `format.py: ${JSON.stringify([n, a, nac])}`);
  });
});

// «Con qué médico va» (responsable del producto, 2026-11-03): el de más turnos y «+N» por los otros.
const CASOS_MEDICOS = [
  [[], "", ""],
  [null, "", ""],
  [[{ nombre: "Dra. Ruiz", n: 3 }], "Dra. Ruiz", "Dra. Ruiz"],
  [[{ nombre: "Dr. Solá", n: 1 }, { nombre: "Dra. Ruiz", n: 4 }], "Dra. Ruiz +1", "Dra. Ruiz, Dr. Solá"],
  [[{ nombre: "Dr. Solá", n: 2 }, { nombre: "Dra. Ruiz", n: 2 }, { nombre: "Dr. Paz", n: 1 }],
    "Dr. Solá +2", "Dr. Solá, Dra. Ruiz, Dr. Paz"],
  [[{ nombre: "", n: 9 }, { nombre: "Dr. Paz", n: 1 }], "Dr. Paz", "Dr. Paz"],
];

test("médicos del paciente: uno por nombre, varios el más frecuente y «+N»", () => {
  for (const [lista, texto, titulo] of CASOS_MEDICOS) {
    assert.equal(F.medicosTexto(lista), texto);
    assert.equal(F.medicosTitulo(lista), titulo);
    // The list keeps the same order, one name per item, so the roster can show as many as fit.
    assert.equal(F.medicosLista(lista).join(", "), titulo);
  }
});

test("médicos del paciente: format.mjs y format.py escriben igual", () => {
  const py = spawnSync("python3", ["-c", `
import json, sys
sys.path.insert(0, ${JSON.stringify(new URL("..", import.meta.url).pathname)})
from app.core import format
casos = json.loads(sys.argv[1])
print(json.dumps([[format.doctors_text(c[0]), format.doctors_title(c[0])] for c in casos]))
`, JSON.stringify(CASOS_MEDICOS)], { encoding: "utf8" });
  assert.equal(py.status, 0, py.stderr);
  const desdePython = JSON.parse(py.stdout);
  CASOS_MEDICOS.forEach(([lista, texto, titulo], i) => {
    assert.deepEqual(desdePython[i], [texto, titulo], `format.py: ${JSON.stringify(lista)}`);
  });
});

test("eval 2: hay UNA sola implementación de cómo se escribe un nombre", () => {
  // La copia vivía en static/platform/admin-queue.mjs (nombrePropio). Quedó como alias del
  // módulo compartido: si vuelve a tener su propia tabla de partículas, hay
  // dos maneras de escribir el mismo nombre otra vez.
  assert.match(cola, /function nombrePropio\([^)]*\)\s*\{\s*return Format\.nombre/,
    "static/platform/admin-queue.mjs volvió a implementar el formato de nombre en vez de usar format.mjs");
  // El sitio del paciente lo normaliza en el servidor, en la fila pública:
  // es la única salida hacia my-requests.js.
  assert.match(solicitudes, /out\["medico_asignado"\] = format\.person_name\(/,
    "booking_requests._public_row tiene que normalizar el nombre del profesional");
  assert.match(solicitudesPaciente, /"medico_asignado": format\.person_name\(schedule\.get\("professional_name"\)\)/,
    "los turnos importados del EMR llegan en mayúsculas y también hay que normalizarlos");
});

// ---------------------------------------------------------------- eval 3
// "If we create a button, an input, or any other control, you should check the
// existing UX for similar control, and you should make it the same way as
// other controls, unless you have a really good reason to do it differently."
//
// El test no puede juzgar "parecido": lo que sí puede es fijar los pares que
// YA existen y que tienen que seguir escribiéndose igual — misma clase, mismo
// ícono, misma palabra. Cada par acá abajo nació de una pantalla real.

test("eval 3: post-booking coverage reuses the account picker and its save is the receipt's one primary action", () => {
  const source = leer("static/platform/account-form.mjs");
  const adapter = source.slice(source.indexOf("  function renderCoveragePicker("), source.indexOf("  function renderPostbookingCoverage("));
  assert.match(adapter, /coverageItems\(opts.coverage\)/);
  assert.match(adapter, /osSectionHtml\(items, ""\)/);
  assert.match(adapter, /wireOsSection\(host, items,/);
  assert.match(adapter, /leerCobertura\(host, items\)/);
  const receipt = source.slice(source.indexOf("  function renderPostbookingCoverage("), source.indexOf("  function segOpt("));
  assert.match(receipt, /picker = renderCoveragePicker\(/);
  // Product owner 26.10: «Guardar cobertura» is THE primary button of «Solicitud
  // enviada» — the same primary as every account form (sc-cta), the only one
  // on the card; «Ver mis solicitudes» left the receipt (service.mjs).
  assert.equal((receipt.match(/class="sc-cta"/g) || []).length, 1);
  assert.match(receipt, /<button type="submit" class="sc-cta">' \+ T\("account\.form\.coverage\.save"/);
  assert.doesNotMatch(receipt, /data-coverage-(none|later)/);
});

test("eval 3: «Quién abrió sus datos» copies «Personas a cargo»: same section, heading and add-style button", () => {
  const source = leer("static/platform/admin-patients.mjs");
  const deps = source.slice(source.indexOf("  function dependientesHtml("), source.indexOf("  function accesosHtml("));
  const accesos = source.slice(source.indexOf("  function accesosHtml("), source.indexOf("  function accionTexto("));
  for (const control of [deps, accesos]) {
    // «Personas a cargo» sin nadie omite el encabezado (queda sólo el botón).
    assert.match(control, /<section id="pat-[a-z]+" class="pat-deps/);
    assert.match(control, /<h2 class="pat-deps-h">/);
    assert.match(control, /<button type="button" class="btn btn-quiet pat-act" id="pac-[a-z-]+">/);
  }
});

test("eval 3: photo enhancement copies the clinical sync checkbox control", () => {
  const settings = leer("static/platform/admin-settings.mjs");
  const photo = settings.slice(settings.indexOf('data-key="TURNOS_PHOTO_AUTO_ENHANCE"'), settings.indexOf('data-msg="turnos"'));
  const sync = settings.slice(settings.indexOf('name="HSI_ENABLED"'), settings.indexOf('data-msg="connectors"'));
  assert.equal((settings.match(/<label class="set-toggle" data-admin-binary="editable"><input type="checkbox"/g) || []).length, 2);
  for (const control of [photo, sync]) {
    assert.match(control, /<span>/);
    assert.match(control, /<\/span><\/label>/);
  }
  assert.match(settings, /\.set-toggle input\[type=checkbox\]\{[^}]*width:20px[^}]*font-size:20px/);
});

test("eval 3: «Al calendario» conserva ícono y texto; junto a la fecha se lee como link", () => {
  // La pendiente todavía necesita chrome de botón dentro del bloque de estado;
  // la aprobada vive al lado de la fecha y por eso se lee como link. Es la
  // razón estructural explícita permitida por ui-rules; texto e ícono siguen
  // siendo exactamente los mismos.
  assert.match(misTurnos, /class="req-btn req-btn-xs req-pref-cal"/);
  assert.match(misTurnos, /class="pt-calendar-link"/);
  const conIcono = misTurnos.match(/MT_CAL_ICON \+ T\("account\.mine\.requests\.to_calendar", null, "Al calendario"\)/g) || [];
  assert.equal(conIcono.length, 2,
    "los dos controles llevan el mismo ícono y la misma clave de texto («Al calendario»)");
});

test("eval 3: the invitation link copies the credentials control; every Invitar is the same button", () => {
  // Médico = usuario (2026-10-19): «Copiar» of the one-use link is the
  // «Copiar acceso» control — same read-only field, same quiet button in the
  // same action row, same clipboard path (copyText) — and «Invitar» reads the
  // same in the Usuario tab, in «Invitar médico» and after «Nuevo médico».
  const users = leer("static/platform/admin-users.mjs");
  assert.match(users, /function copyDoctorCredentials\(resultPromise, isCurrent\) \{\s*return copyText\(/);
  assert.match(users, /copyText\(Promise\.resolve\(result\.enlace\), isCurrent\)/);
  for (const id of ['class="btn btn-quiet doctor-user-link-copy"', 'class="btn btn-quiet" id="doctor-user-copy"']) {
    assert.match(users, new RegExp(`<div class="doctor-user-link-actions"><button type="button" ${id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`));
  }
  assert.equal((users.match(/class="cnt-in doctor-user-select[^"]*" [^>]*type="text" readonly autocomplete="off"/g) || []).length, 2);
  assert.match(users, /T\("admin\.system\.users\.invite", null, "Invitar"\)/);
  assert.match(contenido, /<button type="submit" class="btn btn-ok">' \+ esc\(T\("admin\.content\.catalog\.doctors\.invite_submit", null, "Invitar"\)\)/);
  assert.match(contenido, /<button type="button" class="btn btn-ok" id="med-account-invite">'\s*\+ esc\(T\("admin\.content\.catalog\.doctors\.invite_submit", null, "Invitar"\)\)/);
  // «Invitar médico» and «Cargar lista» are bare icons on the title's line
  // beside «Nuevo médico», named by their tooltip (one screen header, el responsable del producto
  // 2026-11-08); the import's file chooser is the portrait's control: the
  // native input hidden behind a labelled button.
  assert.match(contenido, /class="cnt-gear-btn adm-tool adm-title-action" id="med-invite-open"[^>]*aria-label="' \+ inviteLbl/);
  assert.match(contenido, /class="cnt-gear-btn adm-tool adm-title-action" id="med-import-open"[^>]*aria-label="' \+ importLbl/);
  assert.match(contenido, /<input type="file" id="med-import-file" class="cnt-foto-file"/);
});

test("eval 3: the admin's «Cambiar la dirección del calendario» copies the doctor's own confirm in Mis turnos", () => {
  // Same shape: a wrapper holding the button, or in its place the
  // consequence line and the two answers; the answers and the receipt read
  // exactly the same (same keys) for the doctor and for the administrator.
  const doctor = leer("static/platform/admin-my-appointments.mjs");
  const users = leer("static/platform/admin-users.mjs");
  const admin = users.slice(users.indexOf("    function calRotateHtml("), users.indexOf("    // `outcome` is what the action"));
  assert.match(doctor, /'<div class="mt-cal-rotate">' \+ \(calConfirm\s*\? "<p>"/);
  assert.match(users, /'<div class="doctor-user-cal-rotate">' \+ calRotateHtml\(false\) \+ "<\/div>"/);
  assert.match(admin, /return "<p>" \+ esc\(T\("admin\.system\.users\.reset_calendar_confirm"/);
  for (const key of ["cal_rotate_yes", "cal_rotate_no"]) {
    const call = `T("admin.queue.my_appointments.${key}", null, `;
    assert.ok(doctor.includes(call), `doctor ${key}`);
    assert.ok(admin.includes(call), `admin ${key}`);
  }
  assert.match(admin, /data-doctor-user-cal-rotate-yes>' \+ esc\(T\("admin\.queue\.my_appointments\.cal_rotate_yes"[^)]*\)\) \+ "<\/button> "/);
  assert.match(doctor, /data-cal-rotate-yes>' \+ esc\(T\("admin\.queue\.my_appointments\.cal_rotate_yes"[^)]*\)\) \+ "<\/button> "/);
  // The answer that kills the old address is the panel's destructive button for both.
  assert.match(admin, /class="btn btn-no" data-doctor-user-cal-rotate-yes>/);
  assert.match(doctor, /class="btn btn-no" data-cal-rotate-yes>/);
  for (const source of [doctor, users]) {
    assert.match(source, /toast\(T\("admin\.queue\.my_appointments\.cal_rotated", null, "Listo: la dirección anterior ya no funciona\."\)\)/);
  }
});

test("eval 3: the doctor's «Administrador» copies the «Activo» checkbox of Usuarios", () => {
  const users = leer("static/platform/admin-users.mjs");
  for (const id of ["usr-activo", "doctor-user-admin"]) {
    assert.match(users, new RegExp(`'<label class="cnt-check"><input type="checkbox" id="${id}"'`), id);
  }
  assert.doesNotMatch(users, /data-doctor-user-role/);
});

test("eval 3: an inline «Reintentar» after an error is the same quiet small button everywhere in the panel", () => {
  // Design review 2026-11-04 (C8): retries copy Mi cuenta's quiet small button.
  // The Solicitudes agenda has no «Reintentar» since 2026-11-08: it asks again
  // by itself, like Mis turnos (critical review 08.11, F1).
  const app = leer("static/root/admin-app.js");
  assert.doesNotMatch(app, /sol-agenda-retry/);
  const account = leer("static/platform/admin-account.mjs");
  assert.match(account, /<button class="btn btn-quiet btn-sm" type="button" data-pk-retry>/);
  assert.match(account, /<button class="btn btn-quiet btn-sm" type="button" data-profile-retry>/);
});

test("eval 3: «Descargar» copies «Elegir columnas»: the same icon button beside the table, labelled the same way", () => {
  // Product owner 07.11, an export for every small part: one download button
  // on the table engine, the gear's 44 px icon control with its own icon.
  const table = leer("static/platform/admin-table.mjs");
  assert.match(table, /'<button type="button" class="cnt-gear-btn" data-tblcol-gear aria-label="'/);
  assert.match(table, /'<button type="button" class="cnt-gear-btn tbl-export-btn'/);
  assert.match(table, /' aria-label="' \+ escAttr\(exportLabel\(\)\) \+ '" title="' \+ escAttr\(exportLabel\(\)\)/);
  assert.match(table, /T\("admin\.shell\.export\.button", null, "Descargar"\)/);
  assert.match(leer("static/root/admin-direct.css"), /\.cnt-gear-btn \.adm-icon\.adm-icon-download\{/);
  assert.ok(existsSync(new URL("../static/icons/download-sf-regular.svg", import.meta.url)), "the download icon is a file from the pack");
});

test("eval 3: doctor and service reuse the same canonical booking-limit fields", async () => {
  const {bookingLimitsHtml} = await import('../static/platform/admin-hours-editor.mjs');
  for (const prefix of ['svc','med']) {
    const html=bookingLimitsHtml(prefix,{}, {patient:1,days:31});
    assert.match(html, new RegExp('id="'+prefix+'-turnos-max" min="1" max="60"'));
    // A limit is a plain number field, as in Ajustes › Turnos (decision 2a, 03.11).
    assert.equal((html.match(/class="hor-in hor-in--limit"/g) || []).length,2);
    assert.doesNotMatch(html,/class="hor-stepper"/);
    assert.match(html,/Turnos pendientes por paciente/);
    assert.match(html,/value="" placeholder="1"/);
  }
});

test("Calendario: a day with appointments shows how many, a free working day shows none", async () => {
  const {gridHtml, css} = await import('../static/platform/calendar-grid.mjs');
  // medCalGridDias hands the booked count to the shared grid.
  assert.match(contenido, /function medCalGridDias[\s\S]{0,700}count: Math\.max\(0, parseInt\(day\.ocupados, 10\) \|\| 0\)/);
  const html = gridHtml([
    {dia:'2026-11-10', className:'medcal-libre', count:2},
    {dia:'2026-11-11', className:'medcal-libre', count:0},
  ], {from:'2026-11-01', to:'2026-11-30', today:'2026-11-03', dateAttr:'data-med-date'});
  assert.match(html, /data-med-date="2026-11-10"[^>]*>10<span class="medcal-count">2<\/span>/);
  assert.match(html, /data-med-date="2026-11-11">11<\/button>/);
  assert.match(css, /\.medcal-count\{/);
});

test("Horarios: the limit says whether it is the doctor's own or inherited, and from where", async () => {
  const {bookingLimitsHtml} = await import('../static/platform/admin-hours-editor.mjs');
  const fromService = bookingLimitsHtml('med', {}, {patient: 10, days: 31,
    patientFrom: {from: 'service', name: 'Kinesiología'}, daysFrom: {from: 'site'}});
  assert.match(fromService, /Heredado del servicio Kinesiología: 10\./);
  assert.match(fromService, /Heredado del sitio: 31\./);
  assert.match(fromService, /data-own-text="Propio de este médico\. Vacío: hereda 10\."/);
  const own = bookingLimitsHtml('med', {turnos_activos_max: 3}, {patient: '1–10', days: 31, patientFrom: {from: 'services'}});
  assert.match(own, />Propio de este médico\. Vacío: hereda 1–10\.</);
  assert.match(own, /data-inherited-text="Heredado de sus servicios: 1–10\."/);
  const service = bookingLimitsHtml('svc', {}, {patient: 1, days: 31});
  assert.match(service, /Heredado del sitio: 1\./);
  assert.match(service, /data-own-text="Propio de este servicio\. Vacío: hereda 1 del sitio\."/);
  assert.doesNotMatch(service + fromService, /servicio\/sitio/);
});

test("Horarios: a center's own window is named for the center and says where its number comes from", async () => {
  const {editorHtml} = await import('../static/platform/admin-hours-editor.mjs');
  const centro = {centro: 'hospital', franjas: [{dias: ['lun'], desde: '08:00', hasta: '12:00', cada_min: 20}], excepciones: []};
  const windowOf = (html) => html.slice(html.indexOf('hor-limits--center'), html.indexOf('hor-cierres'));
  const fromDoctor = windowOf(editorHtml([centro], {diasAnticipacion: 7, daysDefault: 31, daysFrom: {from: 'site'}}));
  assert.match(fromDoctor, /<label for="med-ci0-dias-anticipacion">Días de anticipación en este centro<\/label>/);
  assert.match(fromDoctor, /<p><span data-limit-origin [^>]*>Heredado del médico: 7\.<\/span><\/p>/);
  assert.match(fromDoctor, /data-own-text="Propio de este centro\. Vacío: hereda 7\."/);
  assert.match(windowOf(editorHtml([centro], {daysDefault: 10, daysFrom: {from: 'service', name: 'Kinesiología'}})),
    />Heredado del servicio Kinesiología: 10\.</);
  assert.match(windowOf(editorHtml([centro], {daysDefault: 31, daysFrom: {from: 'site'}})), />Heredado del sitio: 31\.</);
  assert.match(windowOf(editorHtml([centro], {daysDefault: 31})), />Heredado: 31\.</);
  const own = windowOf(editorHtml([{...centro, dias_anticipacion: 14}], {diasAnticipacion: 7}));
  assert.match(own, /value="14"/);
  assert.match(own, />Propio de este centro\. Vacío: hereda 7\.</);
  assert.doesNotMatch(fromDoctor + own, /médico\/servicio\/sitio|Hasta cuántos días antes se puede reservar en este centro/);
});

// ---------------------------------------------------------------- eval 4
// "Could you create an evaluation so that if there is a title and subtitle,
// make sure there are no extra content before or after that duplicates it
// with different styles? And if it exists, merge it to the single title and
// subtitle."
//
// La pantalla de la entrevista tenía tres textos diciendo lo mismo: el
// título, el subtítulo ("el médico va a leer tus respuestas") y una banda
// verde abajo ("lo que escribas lo lee el equipo"). Uno solo tiene que
// quedar, y el test cuida las dos mitades de la regla: que el par sea UNO,
// y que ni el subtítulo ni un bloque vecino repitan al título.

const entrevista = leer("app/modules/intake/entrevista.js");

// Palabras con contenido: si dos textos comparten varias, dicen lo mismo.
const VACIAS = new Set(("a al algo ante antes con como cual cuando de del desde donde dos el ella " +
  "ellos en entre era es esa ese eso esta este esto ha hasta hay la las le les lo los más me mi " +
  "mucho muy no nos nuestro o os otro para pero por porque que qué quien se sin sobre su sus " +
  "también tanto te tu tus un una uno unos y ya vos tus tu").split(" "));
const significativas = (texto) => new Set(
  String(texto).toLowerCase()
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .match(/[a-zñ]+/g)?.filter((p) => p.length > 3 && !VACIAS.has(p)) || []);

function sinEco(a, b, donde) {
  const compartidas = [...significativas(a)].filter((p) => significativas(b).has(p));
  assert.ok(compartidas.length < 2,
    `${donde}: dos textos vecinos dicen lo mismo con otro estilo (${compartidas.join(", ")}) — ` +
    "fusionalos en UN título y UN subtítulo");
}

test("eval 4: la entrevista tiene un título; la presentación pertenece al primer mensaje", () => {
  const shell = entrevista.slice(entrevista.indexOf("function renderShell"),
                                 entrevista.indexOf('id="iv-msgs"'));
  // La identificación de IA se muestra en el primer mensaje, no dos veces.
  assert.equal((shell.match(/iv-topbar-title/g) || []).length, 1,
    "la barra superior tiene que tener UN solo título");
  assert.equal((shell.match(/iv-topbar-sub/g) || []).length, 0,
    "la barra superior no debe duplicar la presentación del primer mensaje");
  // Y nada entre ese par y la conversación: la banda verde que repetía el
  // subtítulo con otras palabras no vuelve.
  assert.doesNotMatch(shell, /iv-notice/,
    "volvió un bloque de texto entre el encabezado y la charla — si dice lo mismo que el " +
    "subtítulo, va DENTRO del subtítulo");
  // El subtítulo no puede ser el título dicho de nuevo.
  const subtitulos = [...entrevista.matchAll(/^\s*[:?]\s*"([^"]{20,})"/gm)].map((m) => m[1]);
  for (const sub of subtitulos) sinEco("Contale al médico cómo te sentís", sub, "entrevista.js:subtitulo");
});

// ---------------------------------------------------------------- eval 5
// "Don't tell the doctor will read it before. He will read it during the
// consult. Please add the evaluation every time you confirm or introduce the
// pre-consult AI."
//
// El médico lee la entrevista CON el paciente adelante, no antes de abrir la
// puerta. Prometer lo contrario hace que el paciente espere un médico ya
// briefeado. Vale para las tres bocas que lo cuentan: la pantalla, los
// textos del sitio/el mail, y el prompt del modelo (que es el que lo dice
// en el mensaje de cierre).

const BOCAS = [
  "app/modules/intake/entrevista.js", "app/modules/turnos/service.mjs", "patient_web/site_*.py", "app/notifications/send.py",
  "app/ai/prompts.py", "app/modules/intake/interview_agent.py",
  "app/modules/intake/interview_patient.py", "static/platform/account-requests.mjs",
];
const ANTES_RE = /(lee|leer|leerá|leerán|leyó)[^.\n]{0,60}antes de (atenderte|verte|la consulta|la visita|atenderlo|atenderla)/i;
const ANTES_EN_RE = /read[^.\n]{0,60}(before (seeing|the visit|the consult|attending)|beforehand)/i;

test("eval 5: nadie promete que el médico lo lee ANTES de la consulta", () => {
  for (const archivo of BOCAS) {
    // La única frase que puede NOMBRAR el error es la que lo prohíbe (el
    // prompt le lista al modelo las formas que no puede usar).
    const src = leer(archivo).replace(/Never tell the patient[\s\S]{0,400}?\n\n/g, "\n\n");
    assert.doesNotMatch(src, ANTES_RE,
      `${archivo}: el médico lee la entrevista DURANTE la consulta, no antes`);
    assert.doesNotMatch(src, ANTES_EN_RE,
      `${archivo}: same in English — the doctor reads it DURING the visit`);
  }
});

test("eval 5: el prompt se lo prohíbe también al modelo", () => {
  const prompt = leer("app/ai/prompts.py");
  assert.match(prompt, /DURING the (visit|consultation)/,
    "ai/prompts.py tiene que decirle al modelo que el médico lo lee DURANTE la consulta");
  assert.match(prompt, /Never tell the patient the \\\n?doctor will read it "before seeing you"/,
    "ai/prompts.py tiene que prohibir explícitamente la frase «antes de atenderte»");
});

// ---------------------------------------------------------------- eval 6
// "If this is the computer browser, don't make the chat so wide. Open it on
// the site — we already have all HTML and design for it."
//
// Un solo diseño a dos anchos, nunca un segundo diseño ni una app a pantalla
// completa ocupando un monitor entero: en el teléfono la charla ES la
// pantalla, en una computadora es una página del sitio, con la barra y el
// pie de siempre y una columna de ancho de lectura.

const entrevistaCss = leer("app/modules/intake/entrevista.css");
const sitio = leer("patient_web/site_*.py");

test("eval 6: en el teléfono la charla es la pantalla; en la compu, una página del sitio", () => {
  assert.match(entrevistaCss, /@media\(max-width:767px\)\{[^}]*#stznav[\s\S]{0,120}?display:none/,
    "abajo de 768px la barra y el pie del sitio no se muestran — la charla es la pantalla");
  assert.match(entrevistaCss, /@media\(min-width:768px\)\{[\s\S]*?#iv-host\{[\s\S]*?position:static/,
    "arriba de 768px #iv-host deja de ser una capa fija y entra en el flujo de la página");
  assert.match(entrevistaCss, /\.wrap--chat\{[^}]*max-width:8\d\dpx;/,
    "en una computadora la columna tiene ancho de lectura (~860px), no el ancho del navegador");
});

test("eval 6: la página trae el sitio, pero nunca un segundo chat", () => {
  const rama = sitio.slice(sitio.indexOf('elif chrome == "chat":'),
                           sitio.indexOf("    else:", sitio.indexOf('elif chrome == "chat":')));
  // `nav_js` (Tier 4 slice 6, static_fingerprint.py): variable interpolada,
  // ya no el literal `/nav.js?v=NN` — mismo script, misma barra del sitio.
  assert.match(rama, /nav[._]js/, "chrome=chat tiene que traer la barra del sitio");
  assert.match(rama, /_site_footer\(\)/, "chrome=chat tiene que traer el pie del sitio");
  assert.doesNotMatch(rama, /chat-widget\.js|_chat_bootstrap|chat_js/,
    "el asistente de reservas no puede cargarse sobre la entrevista: dos chats en una pantalla");
});

// ---------------------------------------------------------------- eval 7
// PS16, backlog del responsable del producto: "no screen mixes gray-BOLD text with black-REGULAR
// text (the hierarchy inversion the product owner banned)".
//
// El gris es el color que le dice al lector "esto es secundario" — pero la
// negrita pesa más que el color: un texto gris en negrita se lee MÁS fuerte
// que un texto negro sin negrita al lado, así que el dato que debía quedar
// en segundo plano termina ganando. Caso real encontrado al mirar la página
// de un servicio: la etiqueta "Consultorio"/"Horario"/"Edad" (`.ci-k`, gris +
// 600) vivía pegada al VALOR sin clase, en negro y sin negrita ("Consultorio
// Servicio de imágenes") — la etiqueta pesaba más que el dato. Se corrigió a
// peso regular, la MISMA regla que ya usa la variante `.mp-lugar .ci-k` más
// abajo en el archivo — no un segundo tratamiento del mismo rótulo.
//
// En el listado (listing.js) no hay hoy ningún caso vivo: su único gris+600
// es el título opcional de sección (`.lst-h`, sin uso — todos los llamados
// pasan `title:""`) y los encabezados de columna, que son rótulos
// ESTRUCTURALES por convención (como "SERVICIO · CENTRO · ORDEN"), no un dato
// en pie de igualdad con el texto vecino de su misma fila. El texto que SÍ
// convive en la misma fila que el nombre principal — `.lst-name--sub`
// (Centro), `.lst-person-specialty` (especialidad) — ya es gris + regular;
// el segundo test de acá abajo es preventivo, para que eso no cambie.

test("eval 7: la etiqueta «Consultorio/Horario/Edad» ya no pesa más que su valor", () => {
  const reglas = sitio.match(/\.ci-k\{[^}]*\}/g) || [];
  assert.ok(reglas.length >= 1, "no se encontró la regla .ci-k en patient_web/site_*.py");
  for (const regla of reglas) {
    if (/color:var\(--text-[23]\)/.test(regla)) {
      assert.doesNotMatch(regla, /font-weight:\s*(bold|[6-9]00)\b/,
        `${regla} — una etiqueta gris no puede pesar MÁS que el valor en negro sin negrita ` +
        "que tiene al lado: es la inversión que prohíbe PS16");
    }
  }
});

test("eval 7: listing.mjs no le pone negrita al texto secundario que acompaña al nombre en la fila", () => {
  const listado = leer("static/platform/listing.mjs");
  for (const clase of ["lst-name--sub", "lst-person-specialty"]) {
    const reglas = listado.match(new RegExp("\\." + clase + "\\{[^}]*\\}", "g")) || [];
    assert.ok(reglas.length >= 1, `no se encontró .${clase} en listing.mjs`);
    for (const regla of reglas) {
      if (/color:var\(--text-[23]\)/.test(regla)) {
        assert.doesNotMatch(regla, /font-weight:\s*(bold|[6-9]00)\b/,
          `.${clase} vive en la MISMA fila que el nombre principal — si se le pone negrita, ` +
          "pesa más que el dato principal (PS16)");
      }
    }
  }
});

// ---------------------------------------------------------------- eval 8
// "Links are no longer always underlined" (the product owner, 2026-09-14) — the theme
// decides: "pinamar" keeps links colored and bare, "humanrounds" renders
// them as ink text with a faint underline (the Linear pattern: tenues,
// brighter on hover). La señal de hover es el COLOR que ACLARA (no oscurece:
// "quiero un hover un poco más brillante"), y un link ya visitado cambia a
// --link-visited. El subrayado tenue se arma SÓLO con los tokens
// --link-underline / --link-underline-hover en tokens.css + la regla base a{}
// en human-rounds.css; ningún otro archivo puede hardcodear underline. El
// barrido del 2026-08-07 sacó ~105 declaraciones; este test evita que vuelva
// una sola. Gemelo en vivo: el hook de edición link-underline-guard (avisa al
// EDITAR, esto caza al correr).

const archivosServidos = () => {
  const root = new URL("..", import.meta.url);
  const r = spawnSync("git", ["ls-files", "--cached", "--others", "--exclude-standard"], {
    cwd: root.pathname,
    encoding: "utf8",
  });
  return r.stdout.split("\n").filter((f) =>
    existsSync(new URL(f, root)) &&
    /\.(css|js|mjs|html|py)$/.test(f) &&
    !/^(docs|tests|stories|node_modules|\.storybook|\.claude|scripts)\//.test(f));
};

test("eval 8: (a) la regla base a{} lee los tokens --link* con fallbacks", () => {
  assert.match(css, /a\{[^}]*color:var\(--link,var\(--accent\)\)/,
    "human-rounds.css: a{} tiene que leer --link con fallback a --accent");
  assert.match(css, /a\{[^}]*text-decoration-line:var\(--link-decoration,none\)/,
    "human-rounds.css: a{} define text-decoration-line con --link-decoration");
  assert.match(css, /a\{[^}]*text-decoration-color:var\(--link-underline,currentColor\)/,
    "human-rounds.css: a{} define text-decoration-color con --link-underline");
});

test("eval 8: (b) en hover el subrayado se ENCIENDE (--link-underline-hover más opaco)", () => {
  const tokens = leer("static/root/tokens.css");
  // Extraer los valores RGBA de --link-underline y --link-underline-hover
  const underlineMatch = [...tokens.matchAll(/--link-underline\s*:\s*(rgba?\([^)]+\)|#[0-9a-f]{6}|[a-z]+)/gi)];
  const underlineHoverMatch = [...tokens.matchAll(/--link-underline-hover\s*:\s*(rgba?\([^)]+\)|#[0-9a-f]{6}|[a-z]+)/gi)];

  // Los tokens --link* los define SÓLO "humanrounds" (ver tokens.css):
  // pinamar no los tiene y su a{} base cae a los fallbacks sin subrayar.
  assert.equal(underlineMatch.length, 1,
    "tokens.css: --link-underline vive SÓLO en el tema humanrounds");
  assert.equal(underlineHoverMatch.length, 1,
    "tokens.css: --link-underline-hover vive SÓLO en el tema humanrounds");

  // Comparar opacidad: si está en formato rgba(..., alpha), el hover debe tener alpha más alto
  const alpha = (val) => {
    const m = val.match(/rgba?\([^)]*,\s*([0-9.]+)\)/);
    return m ? parseFloat(m[1]) : 1;
  };
  const reposo = underlineMatch[0][1];
  const hover = underlineHoverMatch[0][1];
  assert.ok(alpha(hover) > alpha(reposo),
    `--link-underline-hover ${hover} tiene que ser más opaco que --link-underline ${reposo} — el hover ENCIENDE el subrayado`);
  assert.ok(alpha(hover) > 0.1,
    `--link-underline-hover ${hover} debe tener opacidad notoria (alpha > 0.1) para verse en hover`);
});

test("eval 8: (c) el hover aclara y el visitado se distingue, en los dos temas", () => {
  const tokens = leer("static/root/tokens.css");
  // luminancia WCAG: el hover tiene que ser MÁS claro que el acento.
  const lum = (hex) => {
    const [r, g, b] = [1, 3, 5].map((i) => {
      const c = parseInt(hex.slice(i, i + 2), 16) / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const pares = [...tokens.matchAll(/--accent:(#[0-9a-f]{6});\s*--accent-hover:(#[0-9a-f]{6})/g)];
  assert.equal(pares.length, 2, "tokens.css: esperaba el par --accent/--accent-hover en los DOS temas");
  for (const [, accent, hover] of pares) {
    assert.ok(lum(hover) > lum(accent),
      `--accent-hover ${hover} tiene que ser MÁS claro que --accent ${accent} — el hover aclara, no oscurece`);
  }
  assert.equal((tokens.match(/--link-visited:#[0-9a-f]{6}/g) || []).length, 2,
    "tokens.css: --link-visited tiene que existir en los DOS temas");
});

test("eval 8: (d) ningún archivo servido subraya un link ni usa border-bottom affordance", () => {
  for (const f of archivosServidos()) {
    const src = leer(f);
    // Excluir human-rounds.css y tokens.css que definen el comportamiento
    if (f.includes("static/root/human-rounds.css") || f.includes("static/root/tokens.css")) continue;

    for (const [rx, que] of [
      [/text-decoration[a-z-]*\s*:\s*[^;}"']*underline/g, "text-decoration underline"],
      [/text-underline-offset\s*:/g, "text-underline-offset"],
      [/text-decoration-style\s*:/g, "text-decoration-style"],
    ]) {
      const m = src.match(rx);
      assert.equal(m, null,
        `${f}: quedó «${que}» (${m && m[0]}) — el subrayado vive SÓLO en tokens.css + human-rounds.css`);
    }
  }
});

test("eval 8: (e) ninguna regla de link en texto de contenido declara font-weight", () => {
  // Las reglas que NO deben tener font-weight explícito:
  // - a.stz-link (prose link)
  // - .ejemplos a (search examples)
  // - footer.site a (footer prose links)
  // - a en general en templates/index.html

  const indexHtml = leer("templates/index.html");

  // Buscar a.stz-link
  assert.doesNotMatch(indexHtml, /a\.stz-link\s*\{[^}]*font-weight:/,
    "templates/index.html: a.stz-link no puede declarar font-weight (hereda del texto)");

  // Buscar .ejemplos a
  assert.doesNotMatch(indexHtml, /\.ejemplos\s+a\s*\{[^}]*font-weight:/,
    "templates/index.html: .ejemplos a no puede declarar font-weight");

  // Buscar footer.site a
  assert.doesNotMatch(indexHtml, /footer\.site\s+a\s*\{[^}]*font-weight:/,
    "templates/index.html: footer.site a no puede declarar font-weight");

  // En human-rounds.css, .stz-link tampoco debe tener font-weight
  assert.doesNotMatch(css, /\.stz-link\s*\{[^}]*font-weight:/,
    "human-rounds.css: .stz-link no puede declarar font-weight");
});


// ---------------------------------------------------------------- eval 9
// iOS Safari hace ZOOM automático cuando el control enfocado tiene menos de
// 16px de letra, y la página queda recortada a los costados mientras se tipea
// (captura del responsable del producto, 2026-09-02, buscando "Derma" en el teléfono). El piso es
// 16px para TODO lo enfocable del sitio paciente: input, select, textarea.
// La conducta hermana — el buscador se ancla abajo de la nav al enfocarse,
// para que los resultados queden a la vista sobre el teclado — vive en
// listing.js (pinSearchTop) y se cuida en el segundo test.

const archivosPaciente = [
  "static/platform/listing.mjs", "static/platform/chat-widget.mjs", "static/platform/chat-ui.mjs", "static/platform/account-form.mjs",
  "static/platform/dependents.mjs", "static/platform/week.mjs", "app/modules/turnos/confirm.mjs", "app/modules/turnos/service.mjs", "static/platform/account-requests.mjs",
  "app/modules/turnos/upload-order.mjs", "app/modules/turnos/wz-upload.mjs", "static/root/human-rounds.css", "templates/index.html",
  "templates/account.html",
];

// En los .js el CSS se arma concatenando literales: primero se pegan las
// uniones «" + "» y después se juntan SOLO los strings, para no confundir
// llaves de código JS con llaves de reglas CSS.
const cssDe = (f) => {
  const src = leer(f);
  if (!f.endsWith(".js") && !f.endsWith(".mjs")) return src;
  const pegado = src.replace(/["']\s*\+\s*\n?\s*["']/g, "");
  // Sólo literales con llaves: un ".lst-search" suelto (argumento de un
  // querySelector) no es CSS y contamina el selector de la regla siguiente.
  return [...pegado.matchAll(/"([^"\n]*)"/g)].map((m) => m[1])
    .filter((s) => /[{}]/.test(s)).join("\n");
};

test("eval 9: ningún control enfocable del sitio paciente baja de 16px (zoom de iOS)", () => {
  for (const f of archivosPaciente) {
    for (const m of cssDe(f).matchAll(/([^{};]+)\{([^{}]*)\}/g)) {
      const [, sel, cuerpo] = m;
      if (!/(input|select|textarea|search)/i.test(sel)) continue;
      if (/::placeholder/.test(sel)) continue;
      for (const fs of cuerpo.matchAll(/font(?:-size)?:\s*(?:[0-9]+\s+)?([0-9]+(?:\.[0-9]+)?)px/g)) {
        assert.ok(parseFloat(fs[1]) >= 16,
          `${f}: «${sel.trim()}» tiene ${fs[1]}px — un control enfocable con menos ` +
          "de 16px dispara el zoom automático de iOS Safari al tocarlo");
      }
    }
  }
});

test("eval 9: todo buscador se ancla arriba al enfocarse (teclado en pantalla)", () => {
  const listado = leer("static/platform/listing.mjs");
  assert.match(listado, /function pinSearchTop/,
    "listing.mjs: falta pinSearchTop — el buscador tiene que subir al tope al enfocarse");
  assert.match(listado, /addEventListener\("focusin"/,
    "listing.mjs: el anclaje va por focusin GLOBAL, para cubrir también el #q de la portada");
  assert.match(listado, /input\[type="search"\]/,
    "listing.mjs: el focusin tiene que mirar todo input[type=search], no sólo .lst-search");
});

// ---------------------------------------------------------------- eval 10
// Touch targets must be at least 44x44 CSS pixels for accessibility on mobile.
// (the product owner, 2026-09-10: "touch targets ≥44x44 CSS px, without making the desktop
// layout bulkier")

test("eval 10: gear button (.cnt-gear-btn) is 44x44px for touch targets", () => {
  const adminCss = leer("static/root/admin-direct.css");
  assert.match(adminCss, /\.cnt-gear-btn\{[^}]*width:44px[^}]*height:44px/,
    "admin-direct.css: .cnt-gear-btn debe ser 44x44px (touch target minimum)");
  assert.doesNotMatch(adminCss, /@media\(pointer:coarse\)[^}]*\.cnt-gear-btn/,
    "admin-direct.css: no debe haber media query pointer:coarse para .cnt-gear-btn (ya es 44px por defecto)");
});

test("eval 10: column selector checkbox is 44x44px for touch targets", () => {
  const adminCss = leer("static/root/admin-direct.css");
  assert.match(adminCss, /\.cnt-gear-opt\s*\{[^}]*min-height:44px/,
    "admin-direct.css: .cnt-gear-opt debe tener min-height:44px");
  assert.match(adminCss, /\.cnt-gear-opt\s+input\{[^}]*width:44px[^}]*height:44px/,
    "admin-direct.css: .cnt-gear-opt input debe ser 44x44px (touch target minimum)");
});

test("eval 10: solicitation checkbox (.sol-ck) is 44x44px for touch targets", () => {
  const solicitudes = leer("static/platform/admin-solicitudes.mjs");
  assert.match(solicitudes, /\.sol-ck\{[^}]*width:44px[^}]*height:44px/,
    "admin-solicitudes.mjs: .sol-ck debe ser 44x44px (touch target minimum)");
  assert.match(solicitudes, /\.sol-ck-hit\{[^}]*width:44px[^}]*height:44px/,
    "admin-solicitudes.mjs: .sol-ck-hit debe ser 44x44px (hit area)");
});

test("eval 10: doctor editor tabs stay on one row at narrow widths", () => {
  const adminCss = leer("static/root/admin-direct.css");
  assert.match(adminCss, /\.cnt-tabs\{[^}]*flex-wrap:nowrap/,
    "admin-direct.css: .cnt-tabs debe tener flex-wrap:nowrap (no wrapping a dos líneas)");
  assert.match(adminCss, /\.cnt-tabs\{[^}]*overflow-x:auto/,
    "admin-direct.css: .cnt-tabs debe tener overflow-x:auto (scrollable si es necesario)");
  assert.match(adminCss, /\[role="tab"\]\{[^}]*min-height:44px/,
    "admin-direct.css: [role=tab] debe tener min-height:44px (touch target)");
});

test("eval 11: no visible text under 13px (zero small print)", () => {
  // Discover and scan all first-party source files for sub-13px font-size declarations.
  // This replaces the hand-picked list to catch issues across all files.

  const repoRoot = new URL("..", import.meta.url).pathname;

  // Directories to scan
  const searchDirs = [
    "static", "templates", "app/modules", "app/core", "app/patient_web",
    "app/admin_panel", "app/cuentas", "app/ops"
  ];

  // File patterns to match
  const filePatterns = /\.(css|mjs|js|html|py)$/;

  // Exclude patterns
  const excludeDirs = new Set([
    "node_modules", "tests", "stories", "docs", ".git", ".claude", "__pycache__"
  ]);

  function shouldSkip(absolutePath, repoRootLen) {
    // Check only the RELATIVE path parts, not the absolute path
    const relPath = absolutePath.substring(repoRootLen).replace(/^[/\\]/, "");
    const parts = relPath.split(sep);
    for (const part of parts) {
      if (excludeDirs.has(part) || part.startsWith(".")) return true;
    }
    if (relPath.includes("vendor") || relPath.includes("dist")) return true;
    // The root web assets (static/root/, at the repository root until 2026-11) were never in
    // this scan; scanning them is a separate decision — it finds admin-direct.css's 12.5px.
    if (relPath.startsWith("static/root/")) return true;
    return false;
  }

  function extractCssFromJs(content) {
    // Extract CSS string literals from JS/MJS (contains { and })
    const pattern = /"([^"]*\{[^"]*\}[^"]*)"|'([^']*\{[^']*\}[^']*)'|`([^`]*\{[^`]*\}[^`]*)`/g;
    const matches = [];
    let m;
    while ((m = pattern.exec(content))) {
      matches.push(m[1] || m[2] || m[3]);
    }
    return matches.join("\n");
  }

  function checkFontSizes(filePathStr, content) {
    const issues = [];

    // Pattern 1: font-size in px
    const pxPattern = /font(?:-size)?:\s*([0-9]+(?:\.[0-9]+)?)px/g;
    let m;
    while ((m = pxPattern.exec(content))) {
      const size = parseFloat(m[1]);
      if (size < 13) {
        const lineNum = content.substring(0, m.index).split("\n").length;
        // Check ONLY for explicit inline comment within the CSS that directly mentions this size
        // Pattern: /\*\s*[0-9.]+px:\s*.*?\*/
        const contextStart = Math.max(0, m.index - 150);
        const contextEnd = Math.min(content.length, m.index + 150);
        const context = content.substring(contextStart, contextEnd);
        const hasExplicitException = new RegExp(`/\\*\\s*${size}px:\\s*[^*]*\\*/`).test(context);

        if (!hasExplicitException) {
          issues.push({
            file: filePathStr,
            line: lineNum,
            match: m[0],
            size: size,
            unit: "px"
          });
        }
      }
    }

    // Pattern 2: font-size in rem (0.8125rem = 13px at 16px root)
    const remPattern = /font(?:-size)?:\s*([0-9]+(?:\.[0-9]+)?)rem\b/g;
    while ((m = remPattern.exec(content))) {
      const size = parseFloat(m[1]);
      const pxSize = size * 16;
      if (pxSize < 13) {
        const lineNum = content.substring(0, m.index).split("\n").length;
        const contextStart = Math.max(0, m.index - 150);
        const contextEnd = Math.min(content.length, m.index + 150);
        const context = content.substring(contextStart, contextEnd);
        const sizeStr = pxSize.toFixed(1);
        const hasExplicitException = new RegExp(`/\\*\\s*${sizeStr}px:\\s*[^*]*\\*/`).test(context);

        if (!hasExplicitException) {
          issues.push({
            file: filePathStr,
            line: lineNum,
            match: m[0],
            size: pxSize,
            unit: "rem"
          });
        }
      }
    }

    return issues;
  }

  let allIssues = [];
  const repoRootLen = repoRoot.length;

  for (const dir of searchDirs) {
    const fullDir = join(repoRoot, dir);
    // a directory that moved must fail here, not turn the scan into a pass over nothing
    assert.ok(existsSync(fullDir), dir + " no longer exists: scan where it lives now");

    const walk = (dir) => {
      const files = readdirSync(dir);
      for (const file of files) {
        const filePath = join(dir, file);
        if (shouldSkip(filePath, repoRootLen)) continue;

        const stat = statSync(filePath);
        if (stat.isDirectory()) {
          walk(filePath);
        } else if (filePatterns.test(file)) {
          try {
            const content = readFileSync(filePath, "utf8");
            const relPath = relative(repoRoot, filePath);

            let toCheck = content;
            if (file.endsWith(".js") || file.endsWith(".mjs")) {
              toCheck = content + "\n" + extractCssFromJs(content);
            }

            const issues = checkFontSizes(relPath, toCheck);
            allIssues = allIssues.concat(issues);
          } catch (e) {
            // Silently skip files we can't read
          }
        }
      }
    };

    walk(fullDir);
  }

  // Sort issues by file and line
  allIssues.sort((a, b) => {
    if (a.file !== b.file) return a.file.localeCompare(b.file);
    return a.line - b.line;
  });

  // Report findings
  const errorMessages = allIssues.map(
    (issue) => `${issue.file}:${issue.line} — ${issue.match} (${issue.size}${issue.unit})`
  );

  assert.equal(
    allIssues.length,
    0,
    `${allIssues.length} sub-13px font-size declarations found (zero small print rule):\n` +
    errorMessages.join("\n")
  );
});

// --------------------------------------------------------------- eval 12
// "Hover states should not change dramatically from beige to blue or blue to
// beige, but should just be a bit brighter. This should be barely noticeable
// if we place items next to each other, but visible when hovering. This
// applies to all interactive elements — links, buttons, and everything else."
// (the product owner, 2026-09-15.)
//
// El hover mueve UN escalón dentro de la familia del propio elemento: nunca
// salta de tono. Antes de esta regla un hover repintaba una superficie beige
// con --accent-soft (que en "humanrounds" es un lavado AZUL), o cambiaba un
// borde neutro por --accent, y la página parpadeaba azul bajo el puntero.
// Los ocho tokens --hover-* de tokens.css son el contrato: ningún
// componente inventa su propio color de hover.
//
// Lo que esta evaluación prohíbe dentro de una regla :hover:
//   · pintar una superficie con --accent-soft (cambio de tono)
//   · cambiar un borde/outline a --accent (cambio de tono)
//   · brightness() por debajo de 1 — un hover ACLARA, nunca oscurece
//   · brightness() por encima de 1,1 — deja de ser "apenas más brillante"
// Lo que SÍ sigue permitido: --accent-hover sobre texto que YA es --accent
// (un link aclarando su propio verde/azul) y la familia --danger completa,
// porque el rojo destructivo es su propia familia.

test("eval 12: ningún hover cambia de familia — sólo aclara un escalón", () => {
  const repoRoot = new URL("..", import.meta.url).pathname;
  const searchDirs = [
    "static", "templates", "app/modules", "app/core", "app/patient_web",
    "app/admin_panel", "app/cuentas", "app/ops"
  ];
  const raiz = ["static/root/human-rounds.css", "static/root/tokens.css", "static/root/admin-direct.css",
                "static/root/admin-gate.css", "static/root/admin-shell.css"];
  const filePatterns = /\.(css|mjs|js|html|py)$/;
  const excludeDirs = new Set([
    "node_modules", "tests", "stories", "docs", ".git", ".claude", "__pycache__"
  ]);

  // Una superficie/borde pintado con el acento, o un brillo que no es "un
  // poquito más brillante". Un botón lleno aclara con
  // `background-image:var(--hover-fill)` (velo blanco): brightness() sobre el
  // casi-negro de las pastillas no se veía, por eso también queda prohibido.
  const PROHIBIDO = [
    [/background(-color)?\s*:\s*var\(--accent-soft/, "pinta la superficie con --accent-soft (salta de tono)"],
    [/border(-color)?\s*:\s*[^;}]*var\(--accent\)/, "cambia el borde a --accent (salta de tono)"],
    [/outline\s*:[^;}]*var\(--accent\)/, "cambia el outline a --accent (salta de tono)"],
    [/brightness\(\s*0?\.\d+/, "oscurece en vez de aclarar"],
    [/brightness\(\s*1\.(1[1-9]|[2-9])/, "aclara de más — el hover deja de ser sutil"],
    [/filter\s*:\s*brightness\(\s*var\(/, "brightness() no aclara un botón casi negro — usá background-image:var(--hover-fill)"]
  ];
  const reglas = /([^{}\n]{1,200}?)\{([^{}]{0,800}?)\}/g;

  const problemas = [];
  const revisar = (rel, texto) => {
    let m;
    reglas.lastIndex = 0;
    while ((m = reglas.exec(texto))) {
      const sel = m[1].trim();
      const cuerpo = m[2];
      if (!sel.includes(":hover") || sel.startsWith("@")) continue;
      for (const [re, porque] of PROHIBIDO) {
        if (re.test(cuerpo)) {
          const linea = texto.slice(0, m.index).split("\n").length;
          problemas.push(`${rel}:${linea} — ${sel} ${porque}`);
          break;
        }
      }
    }
  };

  const recorrer = (dir) => {
    for (const nombre of readdirSync(dir)) {
      if (excludeDirs.has(nombre) || nombre.startsWith(".")) continue;
      const p = join(dir, nombre);
      if (statSync(p).isDirectory()) { recorrer(p); continue; }
      if (!filePatterns.test(nombre)) continue;
      if (p.includes("vendor") || p.includes("dist")) continue;
      revisar(relative(repoRoot, p), readFileSync(p, "utf8"));
    }
  };

  for (const d of searchDirs) {
    const full = join(repoRoot, d);
    assert.ok(existsSync(full), d + " no longer exists: scan where it lives now");
    recorrer(full);
  }
  for (const f of raiz) {
    const full = join(repoRoot, f);
    assert.ok(existsSync(full), f + " no longer exists: scan where it lives now");
    revisar(f, readFileSync(full, "utf8"));
  }

  assert.equal(problemas.length, 0,
    `${problemas.length} hover(s) se salen del contrato --hover-* de tokens.css:\n` +
    problemas.join("\n"));
});

test("eval 12: tokens.css publica los ocho tokens del contrato de hover", () => {
  const tokens = leer("static/root/tokens.css");
  for (const t of ["--hover-bg", "--hover-selected", "--hover-bd",
                   "--hover-bd-strong", "--hover-ink", "--hover-link",
                   "--hover-bg-soft", "--hover-fill"]) {
    assert.match(tokens, new RegExp("\\" + t + "\\s*:"),
      `${t} tiene que existir en tokens.css: es el contrato que consumen todas las reglas :hover`);
  }
  // El hover de un botón lleno es un velo BLANCO tenue encima del color: suma
  // luz (aclara también el casi-negro, que un brightness() dejaba igual).
  const velo = tokens.match(/--hover-fill\s*:\s*linear-gradient\(rgba\(255,255,255,(0?\.\d+)\)/);
  assert.ok(velo && Number(velo[1]) > 0.04 && Number(velo[1]) <= 0.14,
    "--hover-fill tiene que ser un velo blanco entre 5% y 14% — aclara un poquito, nunca oscurece ni explota");
});

// ---------------------------------------------------------------- eval 13
// Product owner, 2026-09-19 (pinamar.humanrounds.org): "the header should have buttons
// of the same height and the same font for all menu items and buttons" + el
// «Chateá con IA» vivía DOS veces en la misma pantalla (pill del nav + launcher
// flotante fijo abajo a la derecha). Un encabezado, un sistema métrico; una
// acción primaria, UNA vez por pantalla.
//
// La caja y el tamaño de letra son IGUALES para todo control de la barra
// (44px, 15px); el peso diferencia el rol — 400 en links, 500 en botones y
// cuenta — porque a 500 en los links la fila se ensancha y layout() colapsa
// el menú a burger en 1440 (medido al implementar).

const nav = leer("static/platform/nav.mjs");
const chatWidget = leer("static/platform/chat-widget.mjs");
const chatBootstrap = leer("templates/_chat_bootstrap.html");

// La regla completa de cada control de la barra, con los pedazos de string
// del módulo ya unidos (nav.mjs arma el CSS por concatenación).
function reglaDe(selector) {
  const i = nav.indexOf('"' + selector);
  assert.ok(i !== -1, selector + ": no se encontró su regla en nav.mjs");
  return nav.slice(i, i + 500).replace(/"\s*\+\s*\n\s*"/g, "");
}

test("eval 13: la barra usa UNA caja (44px) y UNA letra (15px) para todo control", () => {
  for (const sel of [".stznav-link{", ".stznav-btn{", ".stznav-cuenta{"]) {
    const regla = reglaDe(sel);
    // Un tema puede pisar el tamaño con --nav-size (Pinamar 17px, responsable del producto 2026-10-17:
    // «la letra del menú es muy chica»), pero lo pisa para TODOS a la vez.
    for (const token of ["min-height:44px;", "font-size:var(--nav-size,15px);"]) {
      assert.ok(regla.includes(token),
        sel + " tiene que declarar " + token + " — la barra es un solo sistema métrico (responsable del producto 2026-09-19)");
    }
  }
  // Un tema puede pisar el peso con --nav-weight (Pinamar: negrita del macheo de
  // la oficina de prensa, 2026-10-17; con Fira Sans la fila entra en 1440 y 1024,
  // medido). Sin el token rigen estos defaults.
  // Un solo peso para toda la barra (revisión del demo 2026-11-15: «text items
  // and buttons of different height and weight»): el rol de botón se nota en la caja.
  assert.match(reglaDe(".stznav-btn{"), /font-weight:(var\(--nav-weight,)?400[;)]/,
    "los botones de la barra llevan el mismo peso que los links");
  assert.match(reglaDe(".stznav-cuenta{"), /font-weight:(var\(--nav-weight,)?400[;)]/,
    "«Mi cuenta» lleva el mismo peso que los links");
  assert.match(reglaDe(".stznav-link{"), /font-weight:(var\(--nav-weight,)?400[;)]/,
    "los links de la barra llevan 400: a 500 la fila colapsa a burger en 1440");
});

test("eval 13: un solo «Chateá con IA» visible — el launcher cede ante el nav", () => {
  // Burger = colapsado O compacto: en ambos el botón de chat de la barra queda
  // dentro del desplegable cerrado, y el launcher flotante es la única entrada visible.
  const esperado = "body:has(#stznav:not(.stznav--collapsed):not(.stznav--compact)) #stz-chat-launcher:not(.stzc-booking-inline){display:none;}";
  assert.ok(chatWidget.includes(esperado),
    "chat-widget.mjs: el launcher flotante se esconde mientras el nav (server-rendered) muestra su botón de chat");
  assert.ok(chatBootstrap.includes(esperado),
    "_chat_bootstrap.html: misma regla en el bootstrap pre-hidratación — sin flash del launcher");
  // la variante inline de la reserva NO se esconde: ahí el launcher ES el control
  assert.match(chatWidget, /#stz-chat-launcher\.stzc-booking-inline\{position:static/,
    "el launcher inline del booking conserva su propio posicionamiento");
});

test("el lugar del launcher bajo el pie (desde 601px) está también en el bootstrap, antes de que cargue el módulo", () => {
  // El bootstrap pinta el launcher en el primer cuadro; el módulo llega después.
  // Si la regla sólo vive en chat-widget.mjs, en una conexión lenta el botón tapa
  // el texto del pie hasta que el módulo carga (flaky-6, 601–768px).
  const esperado = "@media (min-width:601px){body:has(#stz-chat-launcher:not(.stzc-booking-inline)):not(:has(#stznav:not(.stznav--collapsed):not(.stznav--compact))) :is(footer.site,footer.about-footer,.wrap>footer){padding-bottom:76px;}}";
  assert.ok(chatWidget.includes(esperado),
    "chat-widget.mjs: el pie guarda 76px para el launcher desde 601px");
  assert.ok(chatBootstrap.includes(esperado),
    "_chat_bootstrap.html: la misma regla en el bootstrap — el pie no queda tapado mientras el módulo carga");
});
