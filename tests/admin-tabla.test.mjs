import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const src = fs.readFileSync(new URL('../static/platform/admin-table.mjs', import.meta.url), 'utf8');
const cola = fs.readFileSync(new URL('../static/platform/admin-queue.mjs', import.meta.url), 'utf8');
const colaTpl = fs.readFileSync(new URL('../templates/admin/cola.html', import.meta.url), 'utf8');
const directCss = fs.readFileSync(new URL('../static/root/admin-direct.css', import.meta.url), 'utf8');
const content = fs.readFileSync(new URL('../static/platform/admin-content.mjs', import.meta.url), 'utf8');
const patients = fs.readFileSync(new URL('../static/platform/admin-patients.mjs', import.meta.url), 'utf8');
const users = fs.readFileSync(new URL('../static/platform/admin-users.mjs', import.meta.url), 'utf8');

test('the canonical table engine is a manifest-loaded module', () => {
  assert.doesNotMatch(colaTpl, /static\('admin-table\.js'\)/);
  assert.match(cola, /sanaImportStatic\("platform\/admin-table\.mjs"\)/);
  assert.match(src, /export function crear/);
  assert.doesNotMatch(src, /window\.SanaTable/);
});

test('la presentación de tabla y el selector de columnas existen antes del comportamiento', () => {
  assert.match(directCss, /\.cnt-table\{width:100%;border-collapse:collapse;font-size:16px/);
  assert.match(directCss, /\.cnt-table th,\.cnt-table td\{padding:9px 12px;text-align:left/);
  // 44px: the owner's touch-target rule (2026-09-10 phone audit).
  assert.match(directCss, /\.cnt-gear-btn\{[^}]*width:44px;height:44px/);
  assert.match(directCss, /\.cnt-gear-pop\{position:absolute/);
  assert.doesNotMatch(src, /sana-tabla-css|document\.createElement\("style"/);
});

test('la cola dejó de tener su propia copia y consume el motor compartido', () => {
  assert.match(cola, /m\.crear\(\{/);
  // lo propio de la pantalla se queda: columnas y prioridad de caída
  // (COLS as they are; with the HSI extension's button, the patient column
  // gets its room — launch plan P2, colsConHsi)
  assert.match(cola, /cols: colsConHsi,/);
  assert.match(cola, /function colsConHsi\(\) \{\n    var td = celdaHsi\(\);\n    if \(!td\) return COLS;/);
  assert.match(cola, /drop: DROP/);
  // lo genérico se fue: nada de medir/arrastrar/persistir acá
  ['function autoFit\\(\\) \\{\\n    var host', 'USER_W', 'aplicarAnchos', 'leerAnchosGuardados']
    .forEach((frag) => assert.doesNotMatch(cola, new RegExp(frag), `sobró ${frag} en static/platform/admin-queue.mjs`));
  // y los anchos ya elegidos siguen valiendo: misma clave de localStorage
  assert.match(cola, /almacen: LS_COLS/);
  assert.match(cola, /var LS_COLS = "st_cola_cols";/);
});

test('la manija la monta el motor, no cada módulo en su HTML', () => {
  assert.match(src, /data-tbl-res/);
  assert.doesNotMatch(cola, /data-cola-res/);
  // ordenar por una columna no se dispara al soltar la manija
  assert.match(cola, /if \(th && !e\.target\.closest\("\[data-tbl-res\]"\)/);
});

test('una tabla sin configurar igual anda: la metadata se deriva del encabezado', () => {
  assert.match(src, /function derivarCols\(tb\)/);
  assert.match(src, /new MutationObserver\(pedirBarrido\)/);
  // una columna sin título (acciones) no descarta la tabla entera
  assert.match(src, /labels\.push\(t \|\| \("col" \+ i\)\);/);
});

test('el ancho se vuelve a medir cuando cambia el medio que contiene la tabla', () => {
  assert.match(src, /function observarMedio\(medio\)/);
  // the observer's refit waits a frame: run inside its delivery it would be
  // the browser's «ResizeObserver loop» error
  assert.match(src, /new window\.ResizeObserver\(function \(\) \{ fitEnCuadro\(\); \}\)/);
  assert.match(src, /observarMedio\(medio\);/);
});

test('la tabla redibujada tiene sus anchos antes de pintarse: el ajuste no espera al cuadro siguiente', () => {
  // 2026-11-10: con requestAnimationFrame la tabla se pintaba una vez a su
  // ancho natural y saltaba al llegar el ajuste (CLS). Todas las tablas del
  // panel pasan por programarFit(); ninguna espera un cuadro.
  const body = src.slice(src.indexOf("function programarFit()"), src.indexOf("function fitEnCuadro()"));
  assert.match(body, /queueMicrotask\(/);
  assert.doesNotMatch(body, /requestAnimationFrame/);
});

test('las tablas con las mismas columnas comparten ancho', () => {
  // la identidad es el CONJUNTO de encabezados, ordenado alfabéticamente
  assert.match(src, /labels\.slice\(\)\.sort\(\)\.join\("\|"\)/);
});

test('antes que aplastar todas las columnas, se vuelve al ancho natural', () => {
  // sin prioridad de caída declarada, encajar a la fuerza recorta todo
  assert.match(src, /if \(!\(cfg\.drop && cfg\.drop\.length\)\)/);
  assert.match(src, /ws\[i\] < techo \* 0\.6/);
  assert.match(src, /aplastada\) \{ limpiarAnchos\(\); return; \}/);
});

test('una columna con `max` no arrastra a la tabla entera al ancho natural', () => {
  // El resumen de una entrevista previa mide un párrafo: comparar contra su
  // ancho natural crudo hacía abandonar el encaje de todo el grupo de tablas.
  assert.match(src, /var techo = Math\.min\(nat\[i\], COLS\[i\]\.max \|\| 9999\);/);
});

test('el ancho elegido a mano gana, se guarda y el doble click lo devuelve al auto', () => {
  assert.match(src, /if \(USER_W\[c\.key\] > 0\) return USER_W\[c\.key\];/);
  assert.match(src, /_auto: function \(i\)/);
  assert.match(src, /delete USER_W\[COLS\[i\]\.key\];/);
  assert.match(src, /document\.addEventListener\("dblclick"/);
});

// Product owner 28.07, 05.09: «configurable columns in all the tables; the gear in
// one place». The catalog lists had two copies of the picker of their own.
test('one column picker: the catalog lists use the shared columnas(), no copy of their own', () => {
  for (const own of ['function catalogGearHtml', 'function medGearHtml', 'function wireCatalogGear', 'function wireMedGear',
    'id="med-gear-btn"', "-gear-btn\"", '-gear-pop"', 'CATALOG_COLS_VISIBLE', 'MED_COLS_VISIBLE']) {
    assert.ok(!content.includes(own), `admin-content.mjs still carries ${own}`);
  }
  assert.match(content, /Table\.columnas\(\{\s*clave: "catalog-" \+ tipo,/);
  // the choice people saved before keeps working
  assert.match(content, /legado: "catalog\.columns\." \+ tipo/);
  assert.match(src, /cfg\.legado/);
});

test('the column button sits in one place on every list: right end of the toolbar, before the create action', () => {
  // Pacientes, Usuarios and the catalog lists: `.cnt-toolbar-right`, first.
  // Pacientes has no create action in the toolbar (it sits on the title row),
  // so the column button is the only thing at the right end.
  assert.match(patients, /'<\/div><div class="cnt-toolbar-right">'[\s\S]{0,400}COLPICK\.gearHtml\(\)[\s\S]{0,40}'<\/div><\/div>'/);
  assert.doesNotMatch(patients, /gearHtml\(\)[^\n]*\+ nuevo/);
  // «Descargar» (the table engine's export button) sits right after it; the
  // create action is on the title row (one screen header, 2026-11-08).
  // Both only on a list worth filtering (Filters.filtersWorth, 2026-11-08 D14).
  assert.match(users, /<\/select><\/div><div class="cnt-toolbar-right">'\s*\+ \(herramientas && COLPICK \? COLPICK\.gearHtml\(\) : ""\)\s*\+ \(herramientas && Table && Table\.exportHtml \? Table\.exportHtml\(\{ scope: "#usr-results" \}\) : ""\)\s*\+ '<\/div><\/div>'/);
  assert.match(users, /var herramientas = Filters\.filtersWorth\(items\.length\);/);
  assert.doesNotMatch(users, /cnt-add/);
  assert.match(content, /html \+= '<div class="cnt-toolbar-right">';[\s\S]{0,200}html \+= catalogPicker\(tipo\)\.gearHtml\(\);/);
  // the rule is written where the button is built
  assert.match(src, /gearHtml: function \(clase\) \{\s*\/\/ ONE place on every list/);
});

test('the column button is an Icons8 file, never a drawn glyph', () => {
  assert.doesNotMatch(src, /view_column/);
  assert.doesNotMatch(content, /<svg/);
  assert.match(directCss, /\.cnt-gear-btn \.adm-icon\{[^}]*mask:url\(\/static\/icons\/table-columns-sf-regular\.svg\) center\/contain no-repeat/);
  assert.doesNotMatch(directCss, /\.cnt-gear-btn \.adm-icon\{[^}]*linear-gradient/);
  assert.ok(fs.existsSync(new URL('../static/icons/table-columns-sf-regular.svg', import.meta.url)));
});
