// Guardas de fuente para la lista de Pacientes (responsable del producto, 2026-07-28: filtros,
// teléfono normalizado a WhatsApp, mail clickeable, el LINK en el nombre y no
// en el DNI, edad en vez de fecha de nacimiento, columnas configurables).
// Mismo patrón que admin-cola.test.mjs: node --test sobre el texto del módulo.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const pac = readFileSync(join(ROOT, "static/platform/admin-patients.mjs"), "utf8");
const usr = readFileSync(join(ROOT, "static", "platform", "admin-users.mjs"), "utf8");
const tabla = readFileSync(join(ROOT, "static/platform/admin-table.mjs"), "utf8");
const base = readFileSync(join(ROOT, "templates", "admin", "base.html"), "utf8");
const pacientesTpl = readFileSync(join(ROOT, "templates", "admin", "pacientes.html"), "utf8");

// Una entrada de COLS_ALL: desde `{ key: "x"` hasta el arranque de la
// siguiente (o el cierre del array). Balancear llaves a mano sería más
// exacto pero acá alcanza: el array tiene una columna por bloque.
function col(src, key) {
  const arr = src.slice(src.indexOf("function colsAll() {"));
  const desde = arr.indexOf(`{ key: "${key}"`);
  assert.ok(desde > 0, `no existe la columna ${key}`);
  const resto = arr.slice(desde + 1);
  const hasta = resto.search(/\n\s*\{ key: "|\n\s*\];/);
  return resto.slice(0, hasta === -1 ? resto.length : hasta);
}

test("al paciente se entra por el NOMBRE, no por el DNI", () => {
  // Product owner: "customer is defined by its name, not by bureaucratic number". El
  // DNI sigue en la fila —con eso se verifica a alguien en la mesa— pero en su
  // propia columna (responsable del producto, 2026-11-03: «DNI, entonces, aparte»), sin ser un link.
  const nombre = col(pac, "nombre");
  assert.match(nombre, /href="\/admin\/pacientes\?patient=/);
  assert.doesNotMatch(nombre, /pat-dni-sub/, "ni una segunda línea bajo el nombre");
  assert.doesNotMatch(nombre, /<a[^>]*>[^<]*fmtDni/);
  assert.doesNotMatch(col(pac, "dni"), /<a /);
});

test("la persona es UNA línea «Nombre Apellido, 48»; el DNI es su columna y la Edad no es columna", () => {
  // Responsable del producto, 2026-11-03: «Dos líneas no hagas. Hacé una, listo. DNI, entonces, aparte».
  assert.match(col(pac, "nombre"), /Format\.personaEdad\(/);
  assert.match(col(pac, "dni"), /"default": true/);
  assert.doesNotMatch(pac, /\{ key: "edad"/);
  assert.doesNotMatch(col(pac, "nacimiento"), /"default": true/);
});

test("el número de turnos es un número simple con el detalle en el hint; sin contadores de cuentas", () => {
  const turnos = col(pac, "turnos");
  assert.match(turnos, /class="pat-turnos-n"/);
  assert.match(turnos, /title=/);
  assert.doesNotMatch(pac, /pat-account/);
});

test("en la tarjeta del teléfono los datos se separan con espacio, sin el punto compartido que empezaba la línea", () => {
  // The shared " · " is keyed on an earlier (hidden) cell, so the first visible
  // fact began with a stray dot; Pacientes switches it off and keeps one label.
  assert.match(pac, /td\[data-col\]:not\(\[data-col="nombre"\]\)::before\{content:none;\}/);
  // The Turnos count is a pill at the right edge of the first row: no «Turnos N» label repeats in every card (the product owner, 2026-11-04).
  assert.match(pac, /td\[data-col="turnos"\]\{order:0;margin-left:auto;\}/);
});

test("lo que se repite en cada fila no se escribe: sin «Sin cobertura», sin columna Estado por defecto; el archivado se marca junto al DNI", () => {
  // ui-expert + el responsable del producto, 2026-11-02: solo se muestra lo que difiere.
  assert.doesNotMatch(col(pac, "servicio"), /no_coverage/);
  assert.doesNotMatch(pac, /\{ key: "cobertura"/, "Cobertura ya no es columna del listado (responsable del producto, 2026-11-04): vive en «Editar datos»");
  assert.doesNotMatch(col(pac, "estado"), /"default": true/);
  assert.match(col(pac, "dni"), /status_archived/);
});

test("una columna sin ningún valor en las filas visibles se oculta entera, encabezado incluido, y vuelve con el primer valor", () => {
  for (const k of ["servicio", "contacto", "turnos"]) assert.match(col(pac, k), /ocultaVacia: true/, k);
  assert.match(pac, /return todas\.filter\(function \(c\) \{ return !\(c\.ocultaVacia && VACIAS\[c\.key\]\); \}\);/);
  assert.match(pac, /calcularVacias\(filtradas\(ITEMS\)\);\s+return todas\.filter/);
});

test("en el teléfono los datos de la tarjeta tienen el tamaño y el color del DNI, para que todas las filas midan igual", () => {
  assert.match(pac, /td\[data-col\]:not\(\[data-col="nombre"\]\)\{font-size:14px;line-height:1\.4;color:var\(--text\);/);
  assert.doesNotMatch(pac, /td\[data-col\]:not\(\[data-col="nombre"\]\)\{font-size:16px/);
});

test("el teléfono y el mail son iconos del formateador compartido, nunca texto", () => {
  // Responsable del producto, 2026-11-03: «¿Para qué le escribiste a todos los teléfonos y mails?
  // Hacé iconos: toca y abre el cliente; botón derecho copia».
  assert.match(col(pac, "contacto"), /Format\.contactoIconoHtml/);
  assert.doesNotMatch(pac, /\{ key: "email"/, "el mail no es una columna aparte: es un icono de Contacto");
  assert.doesNotMatch(pac, /Format\.contactoHtml|Format\.emailHtml|Format\.telHtml/, "ningún teléfono ni mail impreso");
  assert.doesNotMatch(pac, /href="tel:/);
  assert.doesNotMatch(usr, /href="tel:/);
});

test("nadie más escribe un teléfono a mano en el panel", () => {
  for (const [nombre, src] of Object.entries({
    "static/platform/admin-content.mjs": readFileSync(join(ROOT, "static", "platform", "admin-content.mjs"), "utf8"),
    "static/platform/admin-my-appointments.mjs": readFileSync(join(ROOT, "static", "platform", "admin-my-appointments.mjs"), "utf8")
  })) {
    assert.doesNotMatch(src, /href="tel:/, `${nombre} todavía arma un tel: propio`);
    assert.doesNotMatch(src, /href=\$\{?["'`]?tel:/, `${nombre} todavía arma un tel: propio`);
  }
});

// Tercera ola (responsable del producto, 2026-11-04). Guardas de fuente: el comportamiento vivo lo
// miden admin-patients-lifecycle.test.mjs y la prueba e2e con el navegador.
test("la ficha abierta no repite «Pacientes»: el título de la pantalla se esconde y el nombre es el único h1", () => {
  assert.match(pac, /if \(h1c\) h1c\.style\.display = "none";/);
  assert.match(pac, /<h1 class="pat-head-name">/);
  assert.doesNotMatch(pac, /<h2 class="pat-head-name">/);
});

test("la fila del título (nombre + Editar datos; Pacientes + Nuevo paciente) se alinea por la línea base del texto", () => {
  assert.match(pac, /#sec-pacientes \.pat-head\{display:flex;flex-wrap:wrap;align-items:baseline;/);
  assert.match(pac, /#sec-pacientes \.pat-head-meta\{display:flex;flex-wrap:wrap;align-items:baseline;/);
  assert.match(pac, /\.adm-h1row:has\(> a\.btn\.pat-title-new\)\{align-items:baseline;\}/);
});

test("«Persona a cargo» se dice una vez: el botón abre el formulario sin encabezado propio y el botón se va mientras está abierto", () => {
  // El h3 existe solo al EDITAR a una persona concreta (lleva su nombre).
  assert.match(pac, /\(editando\s*\? '<h3 class="pat-dep-h3">'/);
  assert.match(pac, /seccion\.setAttribute\("data-form", "new"\)/);
  assert.match(pac, /#pat-deps\[data-form="new"\] #pac-dep-add\{display:none;\}/);
});

test("la tabla de turnos de la ficha mide igual con o sin botones y no repite el nombre del paciente", () => {
  // Height 60px is border-box: the 1px row border counts, so the bottom padding is 7px.
  assert.match(pac, /#sec-pacientes \.pat-appts td\{padding-top:8px;padding-bottom:7px;height:60px;/);
  assert.match(pac, /td\.pat-appt-acc \.btn\{min-height:44px;/);
  assert.match(pac, /td\.pat-appt-acc\{padding-top:8px;min-height:52px;/);
  const desde = pac.indexOf("pat-appt-acc\">' + turnoAccionesHtml");
  assert.ok(desde > 0, "la celda de acciones existe");
  const fila = pac.slice(Math.max(0, desde - 1500), desde);
  assert.doesNotMatch(fila, /pat-appt-paciente|t\.paciente|t\.nombre/, "ni el nombre del paciente en la fila de la ficha");
});

// Revisión con Opus (2026-11-05). Guardas de fuente; el ancho y la altura
// vivos los miden admin-patients-lifecycle.spec.mjs y admin-table-columns-fit.
test("las columnas se miden por su contenido, a ancho natural, y el que sobra va al médico", () => {
  assert.match(pac, /medirClase: "pat-measuring",/);
  assert.match(pac, /#pat-table\.pat-measuring \.pat-table\{table-layout:auto!important;width:auto!important;\}/);
  // El nombre no es elástico: ni toma lo que sobra ni se corta con «…» a
  // 768-1024 (medico y servicio ceden primero).
  assert.doesNotMatch(col(pac, "nombre"), /flex:/);
  assert.match(col(pac, "medico"), /flex: 1,/);
  // En la compu el servicio no envuelve: una fila no crece por él.
  assert.doesNotMatch(pac, /td\[data-col="servicio"\]\{white-space:normal;/);
});

test("un solo borde derecho y una sola altura en la barra de herramientas", () => {
  assert.match(pac, /#sec-pacientes \.pat-table :is\(th,td\):last-child,#sec-pacientes \.pat-appts td:last-child\{padding-right:0;\}/);
  // The toolbar's one 44px height is the shared list-row rule in
  // admin-direct.css (tests/admin-list-row-height.test.mjs); the screen keeps none.
  assert.doesNotMatch(pac, /\.cnt-toolbar-filters \.adm-sel\{height:44px/);
  // Tableta: los filtros con el ancho de lo que dicen, junto a la búsqueda;
  // si los tres no entran a su lado, juntos en el renglón siguiente.
  assert.match(pac, /@media\(min-width:641px\) and \(max-width:1024px\)\{[^}]*\}\s*#sec-pacientes \.cnt-toolbar-filters\{display:flex;flex-wrap:wrap;flex:1 1 0;min-width:0;width:auto;container:pat-filtros \/ inline-size;\}/);
  assert.match(pac, /#sec-pacientes \.cnt-toolbar-filters \.adm-sel\{flex:0 0 auto;width:var\(--control-fit-width,auto\);min-width:var\(--control-fit-width,max-content\);grid-column:auto;\}/);
  assert.doesNotMatch(pac, /\.adm-sel\{flex:1 1 11rem;/);
  assert.match(pac, /@container pat-filtros \(max-width:599px\)\{\s*#sec-pacientes \.cnt-toolbar-filters:has\(\.adm-sel:not\(\[hidden\]\) ~ \.adm-sel:not\(\[hidden\]\) ~ \.adm-sel:not\(\[hidden\]\)\) \.cnt-search\{flex-basis:100%;\}/);
});

test("la tarjeta del teléfono: servicio y médico comparten un renglón", () => {
  assert.match(pac, /#sec-pacientes \.pat-table tbody tr::after\{content:"";order:4;flex:0 0 100%;height:0;\}/);
  assert.match(pac, /td\[data-col="servicio"\]\{order:5;flex:0 1 auto;min-width:0;max-width:60%;/);
  assert.match(pac, /td\[data-col="medico"\]\{order:6;flex:1 1 0;min-width:0;/);
  assert.doesNotMatch(pac, /td\[data-col="servicio"\]\{order:5;flex:0 0 100%/);
});

test("ficha: el DNI sin etiqueta, el nombre con su renglón en el teléfono y los botones al lado del turno", () => {
  assert.match(pac, /'<span class="pat-head-dni">' \+ \(Format\.dniHtml\(dni\) \|\| ""\)/);
  assert.match(pac, /#sec-pacientes \.pat-head-name\{flex:1 1 100%;\}/);
  assert.match(pac, /#sec-pacientes \.pat-head-edit\{margin-left:auto;order:3;\}/);
  assert.match(pac, /#sec-pacientes \.pat-appts td\.pat-appt-main\{width:1%;\}/);
  assert.match(pac, /\.pat-appt-l2\{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;width:0;min-width:100%;\}/);
  assert.match(pac, /'<div class="pat-appt-l2" title="' \+ esc\(sub\) \+ '">'/);
});

test("el «+N» de médicos se vuelve a medir cuando cambia la celda, no solo la tabla", () => {
  assert.match(pac, /var docs = cont\.querySelector\("\.pat-docs"\);/);
  assert.match(pac, /if \(docs\) medObs\.observe\(docs\);/);
  assert.match(pac, /doc\(\)\.fonts\.ready\.then\(programarMedicos\)/);
});

test("el WhatsApp sale del formateador compartido, no de cada pantalla", () => {
  assert.doesNotMatch(pac, /wa\.me/);
  assert.doesNotMatch(usr, /wa\.me/);
});

test("los filtros son <select>, nunca pills", () => {
  // ui-rules: elegir UNA opción de un conjunto es un dropdown. Y el valor por
  // defecto ES la etiqueta del campo (mismo idioma que los filtros de
  // Médicos), no un "Todos" genérico.
  for (const id of ["pac-f-estado", "pac-f-banda", "pac-f-cobertura"]) {
    assert.match(pac, new RegExp(`<select class="adm-sel" id="${id}"`), `falta el filtro ${id}`);
  }
  assert.doesNotMatch(pac, /pac-f-edad/, "no hay filtro por edad (responsable del producto, 2026-11-03)");
  assert.match(pac, /opts\(\[\["activo", T\("admin\.queue\.patients\.filter\.active", null, "Activos"\)\], \["archivado", T\("admin\.queue\.patients\.filter\.archived", null, "Archivados"\)\]\], FILTROS\.estado, T\("admin\.queue\.patients\.col\.status", null, "Estado"\)\)/);
  assert.doesNotMatch(pac, /class="pill/);
});

test("el vacío dice si no hay pacientes o si los filtros no dejan pasar a ninguno", () => {
  assert.match(pac, /Ningún paciente coincide con los filtros/);
  assert.match(pac, /Todavía no hay pacientes/);
});

test("las columnas se eligen con el mismo motor que las demás tablas", () => {
  assert.match(pac, /Table\.columnas\(\{\s*clave: "patients"/);
  assert.match(usr, /Table\.columnas\(\{\s*clave: "users"/);
  assert.match(tabla, /function columnas\(cfg\)/);
  // Un solo engranaje abierto a la vez y la elección guardada por persona.
  assert.match(tabla, /data-tblcol-gear/);
  assert.match(tabla, /"table\.columns\." \+ String\(cfg\.clave/);
});

test("apagar la columna por la que se ordena no deja la tabla sin orden", () => {
  assert.match(pac, /if \(!activas\.some\(function \(c\) \{ return c\.key === ORDEN\.key; \}\)\)/);
});

test("Usuarios también se ordena con el teclado", () => {
  // La mesa incluye a una persona que trabaja con lector de pantalla.
  assert.match(usr, /keydown/);
  assert.match(usr, /e\.key !== "Enter" && e\.key !== " "/);
  assert.match(usr, /aria-sort/);
});

// (La guarda vieja "Reportes va con lo de dirección" leía la nav del
// admin.html por hash: esa nav era markup a mano con class="adm-nav-sep".
// La nav viva es el loop server-side de screen_nav.nav_items(rol) — el
// ORDEN (pacientes antes de reportes) lo congela el golden de
// tests/test_admin_screen_nav.py, y el separador visual no existe en el
// cascarón directo.)

// Combo de médico (responsable del producto, 2026-08-06: "add the filter by doctor… combo box
// so people can type"). Un control nuevo copia al que ya hace ese trabajo
// (admin-combo.mjs/.cmb-in/.cmb-list, el mismo que "Asignar un médico" en
// Solicitudes) — nunca reinventa el desplegable con un <select> o markup a
// mano.
test("no hay filtro por médico: al médico se lo busca escribiendo en la búsqueda", () => {
  // Responsable del producto, 2026-11-02: «No hace falta un filtro aparte por médicos. Son
  // muchos: que lo escriban en la búsqueda.» El servidor ya busca por médico.
  assert.doesNotMatch(pac, /pat-medico-sel|MEDICO_FILTRO|pac-f-medico/);
  assert.doesNotMatch(pac, /admin-combo/);
  assert.match(pac, /\?q=/);
  assert.doesNotMatch(pac, /medico=/);
});

test("la pantalla de Pacientes publica el formatter ESM y carga dependencias antes que su controlador", () => {
  assert.match(base, /static_manifest\(\)/);
  assert.doesNotMatch(base, /static\('format\.js'\)/);
  assert.match(pac, /importStatic\("platform\/format\.mjs"\)/);
  const orden = ["platform/admin-patients.mjs"];
  const pos = orden.map(s => pacientesTpl.indexOf(`static('${s}')`));
  assert.ok(pos.every(p => p > 0), "falta algún script");
  assert.deepEqual(pos.slice().sort((a, b) => a - b), pos, "el orden de carga cambió");
  assert.doesNotMatch(pacientesTpl, /admin-combo\.js/);
  assert.match(pac, /var dependencies = Promise\.all\(\[[\s\S]*importStatic\("platform\/admin-table\.mjs"\)[\s\S]*dispose\.ready = dependencies\.then/);
  assert.match(pac, /return Prefs\.load\(\)\.then\([\s\S]*return route\(\)/);
});

test("ficha: a turno without a date shows nothing in its place, never «Sin fecha»", () => {
  assert.doesNotMatch(pac, /patient_appts\.no_date/);
  assert.doesNotMatch(pac, /"Sin fecha"/);
  assert.match(pac, /if \(!t\.dia\) cuando = "";/);
  // No empty «when» span either: the fit pass reads data-largo/data-corto off it.
  assert.match(pac, /\(cuando \? '<span class="pat-appt-when" data-largo="/);
});
