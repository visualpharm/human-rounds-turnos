// Guardas de fuente para "Pacientes de hoy" (#cola, 2026-07-24). Igual
// patrón que admin-reports-summary.test.mjs/admin-sistema-ui.test.mjs: node
// --test sobre el texto de los archivos, sin levantar un navegador —
// suficiente para clavar decisiones que ya se relitigaron una vez (acá:
// un click por botón, nunca combo+Guardar; #cola sale de TODOS los
// hide-arrays de los demás routers).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { fixture, flush, click } from './helpers/queue-runtime-fixture.mjs';
import { BASE } from './shared-shell-contract.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const colaPath = join(ROOT, 'static/platform/admin-queue.mjs');
const COLA = readFileSync(colaPath, 'utf8');
const COLA_SCREEN = readFileSync(join(ROOT, 'templates/admin/cola.html'), 'utf8');
const COLA_INITIAL = readFileSync(join(ROOT, 'templates/admin/partials/day_queue_initial.html'), 'utf8');
const COLA_ROUTER = readFileSync(join(ROOT, 'app/admin_panel/admin_cola_screen.py'), 'utf8');
const APP = readFileSync(join(ROOT, 'static', 'root', 'admin-app.js'), 'utf8');
const DIRECT_CSS = readFileSync(join(ROOT, 'static', 'root', 'admin-direct.css'), 'utf8');
const QUEUE_CSS = readFileSync(join(ROOT, 'static/platform/admin-queue.css'), 'utf8');

test('policy: direct /admin/cola fingerprints native module and document stylesheet, never classic script', () => {
  assert.ok(existsSync(colaPath), 'native admin queue module must exist');
  assert.match(COLA_SCREEN, /static\('platform\/admin-queue\.mjs'\)/);
  assert.match(COLA_SCREEN, /static\('platform\/admin-queue\.css'\)/);
  assert.doesNotMatch(COLA_SCREEN, /<script[^>]+src=[^>]*admin-queue/);
});

test('visible queue dates use dates.mjs without a parallel Intl formatter', () => {
  assert.match(COLA, /Dates\.corta\(diaIso/);
  const formatter = COLA.slice(COLA.indexOf('function fechaCorta(iso)'), COLA.indexOf('// Ícono de documento'));
  assert.doesNotMatch(formatter, /Intl\.DateTimeFormat|SanaDates|\bMESES\b/);
  assert.match(COLA, /Promise\.all\(\[i18nReady, datesReady, formatReady, filterControlsReady, forecastReady, prefsReady, tableReady, sessionReady, explainerReady\]\)/);
  assert.match(COLA, /function localizeColumns\(\)/);
});

test('the server-routed cola screen owns the section id and its container', () => {
  assert.match(COLA_ROUTER, /_SECTION_ID = "cola"/);
  assert.match(COLA_SCREEN, /<div id="sec-cola">\{% include "admin\/partials\/day_queue_initial\.html" %\}<\/div>/);
  assert.match(COLA_SCREEN, /id="queue-initial-data"/);
  assert.match(COLA_INITIAL, /data-queue-server-rendered/);
});

test('the queue updates through a real EventSource invalidation stream', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  const router = readFileSync(join(ROOT, 'app/modules/turnos/doctor_appointments_router.py'), 'utf8');
  const broker = readFileSync(join(ROOT, 'app/modules/turnos/queue_events.py'), 'utf8');
  assert.match(js, /new window\.EventSource\("\/api\/admin\/day-queue\/events"\)/);
  assert.match(js, /queue-update/);
  assert.match(js, /function refreshQueue\(eventDay\)/);
  assert.match(js, /listen\(queueStream, "ready"/);
  assert.match(router, /class _FeedResponse\(StreamingResponse\)/);
  assert.match(router, /text\/event-stream/);
  assert.doesNotMatch(router, /Connection.*keep-alive/);
  assert.match(broker, /Last-Event-ID/);
  assert.match(broker, /day_queue_events_after/);
  assert.match(broker, /LISTEN/);
});

test('the no-JS queue table uses shared CSS and reads as a business card, no repeated field labels', () => {
  assert.doesNotMatch(COLA_SCREEN, /<style>/);
  assert.match(DIRECT_CSS, /\.queue-server-table\{width:100%;table-layout:auto;/);
  assert.match(DIRECT_CSS, /\.queue-server-table td::before\{content:none;\}/);
});

test('applyRol mentions cola so the medico role keeps it visible', () => {
  const m = /function applyRol\(rol[^)]*\)\{[\s\S]*?\n\}/.exec(APP);
  assert.ok(m, 'applyRol debe existir');
  assert.match(m[0], /nav-cola/);
});

test('attendance control is one-click buttons, never a select+save combo', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  // Los filtros se construyen por el módulo ESM compartido; la celda de
  // asistencia (botonesHtml/asistenciaCellHtml) nunca dibuja un select.
  const botones = /function botonesHtml\([\s\S]*?\n  \}/.exec(js)[0];
  const celda = /function asistenciaCellHtml\([\s\S]*?\n  \}/.exec(js)[0];
  assert.ok(!/<select/.test(botones), 'botonesHtml no debe dibujar un <select>');
  assert.ok(!/<select/.test(celda), 'asistenciaCellHtml no debe dibujar un <select>');
  // El HTML vive sólo en admin-filter-controls.mjs, no hay una segunda
  // fábrica de selects en esta pantalla.
  const selects = js.match(/<select /g) || [];
  assert.equal(selects.length, 0, 'admin-queue no duplica la fábrica de selects');
  assert.match(js, /FilterControls\.select/);
  assert.match(js, /data-asis="/);
  assert.match(js, /admin\.queue\.attendance\.presente", null, "Atendió"/);
  assert.match(js, /admin\.queue\.attendance\.ausente", null, "No vino"/);
  assert.match(js, /data-corregir="/);
  // Corregir es un LÁPIZ desde el handoff 2026-07-27 (la palabra repetida en
  // cada fila era la línea más ruidosa); su nombre accesible sigue diciendo de
  // quién es el turno, que es lo único que oye un lector de pantalla.
  assert.doesNotMatch(js, />corregir</);
  assert.match(js, /admin\.queue\.attendance\.aria_corregir", \{ who: quien\(t\) \}, "Corregir la asistencia de \{who\}"/);
  assert.match(js, /var LAPIZ = /);
});

test('attendance POST hits the shared endpoint with presente/ausente', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /\/api\/admin\/requests\/.+\/attendance/);
  assert.match(js, /data-valor="presente"/);
  assert.match(js, /data-valor="ausente"/);
});

test('day-queue fetch hits GET /api/admin/day-queue', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /\/api\/admin\/day-queue/);
});

test('the row is hour · patient · doctor; the gear only offers extras, none by default', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  const m = /var COLS_ALL = \[([\s\S]*?)\n  \];/.exec(js);
  assert.ok(m, 'COLS_ALL debe existir');
  const keys = [...m[1].matchAll(/key: "([a-z]+)"/g)].map((x) => x[1]);
  // Product owner 2026-11-09: the row says who comes, when and to whom. DNI, the
  // service column, what the AI read, the referral column, the history
  // column and attendance moved into the appointment card the name opens.
  // A trailing empty column still carries the row to the block's edge.
  assert.deepEqual(keys, ['hora', 'paciente', 'medico', 'confirmo', 'solicitado', 'cobertura', 'entrevista', 'relleno']);
  assert.doesNotMatch(m[1], /"default": true/);
  // The age is not a column: «Name, N años» is one link.
  assert.ok(!keys.includes('edad'));
  assert.match(js, /function nombreEdad\(t\)/);
  assert.match(js, /class="queue-pat-link" data-cola-abrir="/);
  assert.doesNotMatch(js, /class="queue-age"/);
  assert.match(js, /st_cola_visible_cols/);
  assert.match(js, /data-cola-gear/);
  assert.match(js, /function theadHtml\(\)/);
  assert.match(js, /<colgroup>/);
});

test('el engranaje de columnas vive siempre en Pacientes de hoy, incluso con 0 turnos', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  // One controls row (the product owner, 2026-11-08): search, the day, then the actions as
  // icons — columns, «Descargar» (the table engine's button) and the print
  // slot — and the filters folded behind the funnel on their own line.
  assert.match(js, /buscarInputHtml\(\) \+ dateNavHtml\(\)\s*\+ '<div class="queue-actions">' \+ columnGearHtml\(\) \+ exportButtonHtml\(\)/);
  assert.match(js, /data-cola-print-slot><\/span><\/div>'\s*\+ '<div class="queue-filters">'/);
  assert.doesNotMatch(js, /totalTodos \? columnGearHtml\(\) : ""/);
});

test('column widths are measured, not hardcoded — no px nth-child rules survive', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  const motor = readFileSync(new URL('../static/platform/admin-table.mjs', import.meta.url), 'utf8');
  // The product owner (ronda 3): "don't fix the width of the columns and let the browser
  // do it" — la medición no son reglas px por nth-child. Desde la 2ª vuelta
  // del 2026-07-24 el motor vive en admin-table.js (lo comparten todas las
  // tablas del admin); acá se sigue exigiendo la medición, allá.
  assert.doesNotMatch(js, /queue-table th:nth-child\(\d\)[^"]*width:\d+px/);
  assert.match(js, /medirClase: "queue-measuring"/);
  assert.match(js, /\.queue-measuring \.queue-table\{table-layout:auto!important;width:auto!important;\}/);
  assert.match(motor, /function autoFit\(/);
  // el máximo se toma sobre TODAS las tablas de médico (siguen alineadas).
  assert.match(motor, /if \(w > nat\[i\]\) nat\[i\] = w;/);
});

test('columns are drag-resizable like Excel and the choice persists', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  const motor = readFileSync(new URL('../static/platform/admin-table.mjs', import.meta.url), 'utf8');
  const directCss = readFileSync(new URL('../static/root/admin-direct.css', import.meta.url), 'utf8');
  assert.match(js, /st_cola_cols/);       // los anchos ya elegidos siguen valiendo
  assert.match(motor, /data-tbl-res/);
  assert.match(directCss, /cursor:col-resize/);
  assert.match(motor, /pointerdown/);
  assert.match(motor, /pointermove/);
  // doble click en la manija = volver al ancho automático.
  assert.match(motor, /dblclick/);
});

test('headers sort the queue, Hora ascending by default', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /sort: \{ key: "hora", dir: 1 \}/);
  assert.match(js, /cnt-th-sortable/);
  assert.match(js, /data-cola-sort="/);
  assert.match(js, /function cmpCol\(/);
  // con búsqueda activa manda la relevancia hasta que se toca una cabecera.
  assert.match(js, /STATE\.sortTouched/);
});

test('cells stay compact on desktop and keep hour, patient and doctor on tablet', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  // The age rides in the name's own link, with its unit: «Ana Paz, 47 años».
  assert.match(js, /Tn\("admin\.queue\.table\.edad_anios", edad, null, "\{n\} año", "\{n\} años"\)/);
  assert.match(js, /T\("admin\.queue\.table\.nombre_edad", \{ nombre: nombre, edad: anios \}, "\{nombre\}, \{edad\}"\)/);
  assert.match(js, /return Format\.edad\(iso\);/);
  // The referral is the panel's paperclip with the word «Orden», inline after
  // the name and the history bars; no referral, nothing (the product owner, 2026-11-09).
  assert.match(js, /'<span class="queue-pat-line">' \+ pac \+ historialBarrasHtml\(t\) \+ ordenBotonHtml\(t, nombre\) \+ "<\/span>"/);
  assert.match(js, /if \(!t\.tiene_orden\) return "";/);
  assert.match(js, /queue-orden-ico" aria-hidden="true"><\/span>' \+ esc\(T\("admin\.queue\.table\.orden", null, "Orden"\)\)/);
  assert.match(QUEUE_CSS, /\.queue-orden-ico\{[^}]*mask:url\(\/static\/icons\/chat-attach-sf-regular\.svg\)/);
  assert.match(QUEUE_CSS, /\.queue-orden\{display:inline-flex;[^}]*white-space:nowrap;/);
  assert.doesNotMatch(js, /var DOC = '<svg|queue-referral-check|queue-referral-no|class="queue-age"/);
  // The doctor with the service under the name.
  assert.match(js, /esc\(nombre\) \+ "<\/button>"\s*\+ \(servicio \? '<span class="queue-sub" data-export-omit>' \+ esc\(servicio\) \+ "<\/span>" : ""\)/);
  assert.match(QUEUE_CSS, /\.queue-sub\{display:block;[^}]*font-size:14px;/);
  // sobreturno: marca mínima pegada a la hora (the product owner: "much more compact and it
  // relates to the hour, not name").
  assert.match(js, /admin\.queue\.free_slot\.sobreturno", null, "Sobreturno"/);
  assert.match(js, /queue-overbooking" title="' \+ esc\(sobreTitle\) \+ '" aria-label="' \+ esc\(sobreTitle\) \+ '">S\/T</);
  assert.doesNotMatch(js, /<span>Sobre<\/span><span>turno<\/span>/);
  // una hora que ya pasó sin registrar se dice en la hora misma.
  assert.match(js, /queue-hour-overdue/);
  // 641–1024px: the hour keeps its width, the patient takes the rest, the
  // doctor a third; the gear's extras share what is left.
  assert.match(js, /@media\(min-width:641px\) and \(max-width:1024px\)/);
  assert.match(js, /\.queue-table col\[data-col='hora'\]\{width:84px!important;\}/);
  assert.match(js, /\.queue-table col\[data-col='paciente'\]\{width:auto!important;\}/);
  assert.match(js, /\.queue-table col\[data-col='medico'\]\{width:32%!important;\}/);
  assert.match(js, /\.queue-table col\[data-col='entrevista'\]\{width:18%!important;\}/);
  assert.doesNotMatch(js, /queue-compact-detail|Servicio y práctica|queue-th-wide/);
  assert.match(js, /var DROP = \["solicitado", "cobertura", "confirmo"\]/);
});

test('la tabla comparte la alineación izquierda del bloque y no suma padding lateral', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /\.queue-table th,.queue-table td\{padding:9px 5px;text-align:left;\}/);
  assert.match(js, /\.queue-table th:first-child,.queue-table td:first-child\{padding-left:0;\}/);
  assert.match(js, /\.queue-table th:last-child,.queue-table td:last-child\{padding-right:0;\}/);
  assert.match(DIRECT_CSS, /\.adm-content-wrap:has\(> #sec-cola:not\(\[style\*="none"\]\)\)\{padding:12px 0 80px;\}/);
  assert.match(DIRECT_CSS, /#sec-cola \.queue-controls,#sec-cola \.queue-doctor-head,#sec-cola>\.adm-empty\{padding-left:var\(--adm-gutter\);padding-right:var\(--adm-gutter\);\}/);
  // «Ver N agendas…» is a button: margin, so its box keeps off the phone's edge too.
  assert.match(DIRECT_CSS, /#sec-cola \.queue-show-empty\{margin-left:var\(--adm-gutter\);margin-right:var\(--adm-gutter\);\}/);
  // The HSI install line keeps the search's left edge on the phone, not the screen edge.
  assert.match(DIRECT_CSS, /#sec-cola \.queue-entrevista-cfg,#sec-cola \.queue-ai-notice,#sec-cola>\.hsi-instalar\{padding-left:var\(--adm-gutter\);padding-right:var\(--adm-gutter\);\}/);
});

test('empty state and «Mostrar horarios libres» read at full size in dark grey', () => {
  // ui-expert 2026-11-08: the empty day measured ~13 px on 320 and the free-slots
  // toggle sat at alpha 0.50 (3.3:1). Both stay on --hint-ink, the empty line at 16 px.
  assert.match(DIRECT_CSS, /\.adm-empty\{color:var\(--hint-ink\);font-size:16px;line-height:1\.5;padding:18px 0;\}/);
  assert.match(QUEUE_CSS, /\.queue-free-chk\{[^}]*color:var\(--hint-ink\);/);
  // No translucent --text-2/3/4 text left on the queue's own stylesheet.
  assert.doesNotMatch(QUEUE_CSS.replace(/\/\*[\s\S]*?\*\//g, ''), /(?<![-\w])color\s*:\s*var\(\s*--text-[234]\b/);
});

test('Prácticas/Diagnóstico cells unwrap ai_campos, clip to one line with a title tooltip', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /function campoTexto\(/);
  assert.match(js, /campoTexto\(t\.ai_campos, "practicas"\)/);
  assert.match(js, /campoTexto\(t\.ai_campos, "diagnostico"\)/);
  assert.match(js, /queue-td-clip/);
  assert.match(js, /text-overflow:ellipsis/);
  assert.match(js, /' title="' \+ esc\(valor\) \+ '"'/);
  assert.match(js, /queue-td-empty/);
  assert.match(js, /queue-table td\.queue-td-empty\{display:none;\}/);
});

test('médico filter is a native select, never pills', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /queue-doctor-filter/);
  assert.match(js, /filtroSelectHtml\("queue-doctor-filter"/);
  assert.match(js, /FilterControls\.select/);
});

test('service filter is a <select> and free-text search ranks by relevance', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  // filtro por servicio = native select (nunca pills), buscador = input search.
  assert.match(js, /filtroSelectHtml\("queue-service-filter"/);
  assert.match(js, /data-cola-servfiltro/);
  assert.match(js, /<input type="search" class="adm-search queue-search"/);
  assert.match(js, /data-cola-buscar/);
  // la búsqueda puntúa por relevancia sobre paciente/servicio/médico/campos…
  assert.match(js, /function scoreTurno\(/);
  assert.match(js, /norm\(t\.paciente\)/);
  assert.match(js, /norm\(campoTexto\(t\.ai_campos, "practicas"\)\)/);
  // repinta sólo #cola-results (el input no pierde foco al tipear).
  assert.match(js, /function renderResultados\(/);
  assert.match(js, /getElementById\("cola-results"\)/);
});

test('empty day state names the day and never dead-ends (date nav stays)', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /No hay turnos aprobados para/);
  // el nav de fecha se arma ANTES del chequeo de vacío, así que siempre está.
  const fnBody = /function render\(\) \{[\s\S]*?\n  \}/.exec(js)[0];
  const navIdx = fnBody.indexOf('dateNavHtml()');
  const emptyIdx = fnBody.indexOf('No hay turnos aprobados');
  assert.ok(navIdx !== -1 && emptyIdx !== -1 && navIdx < emptyIdx);
});

test('overlay reuses the .solmod classes (paciente + orden open it)', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /data-cola-abrir="/);
  assert.match(js, /solmod-back/);
  assert.match(js, /solmod-orden/);
});

test('ordenHtml renders img for image/*, embed for pdf, object fallback otherwise — never a typeless <object> for a known mime', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  const fn = /function ordenHtml\([\s\S]*?\n  \}/.exec(js);
  assert.ok(fn, 'ordenHtml debe existir');
  const body = fn[0];
  assert.match(body, /image\\\//, 'debe testear el prefijo image\\/');
  assert.match(body, /<img class="solmod-orden"/);
  assert.match(body, /<embed class="solmod-orden" type="application\/pdf"/);
  assert.match(body, /<object class="solmod-orden"/);
  // la imagen es una foto ÚNICA: click abre el original en pestaña nueva,
  // sin flechas de galería (photo-zoom: single photo, no lightbox nav).
  assert.match(body, /target="_blank" rel="noopener"/);
  assert.doesNotMatch(body, /solmod-arrow|lightbox-(next|prev)/);
});

test('the overlay cancels the appointment, confirmed inside the dialog (the reference-deployment counterpart, 2026-10-19)', () => {
  const fn = /function cancelarHtml\([\s\S]*?\n  \}/.exec(COLA);
  assert.ok(fn, 'cancelarHtml debe existir');
  // only while the appointment is still ahead and unmarked: a past one is «No vino»
  assert.match(fn[0], /if \(t\.asistencia \|\| esPasado\(t\.hora\)\) return "";/);
  assert.match(COLA, /"\/api\/admin\/requests\/" \+ encodeURIComponent\(id\) \+ "\/cancel"/);
  assert.match(COLA, /\+ cuerpo \+ cancelarHtml\(t\) \+ "<\/div><\/div>";/);
  const flow = COLA.slice(COLA.indexOf('function cancelarTurno('), COLA.indexOf('function abrirOverlay('));
  assert.match(flow, /refreshQueue\(\);/);
  assert.doesNotMatch(flow + fn[0], /window\.confirm/);
  assert.match(COLA, /box\.innerHTML = cancelarConfirmHtml\(t\);/);
});

test('the card is 760 wide, widens to min(1200px,94vw) only beside a referral', () => {
  // The width is the panel's one overlay-card rule (admin-direct.css,
  // 2026-11-09): the queue keeps no width or column rule of its own.
  const js = COLA + '\n' + DIRECT_CSS;
  assert.match(DIRECT_CSS, /\n\.solmod\{[^}]*max-width:min\(760px,94vw\);/);
  assert.match(DIRECT_CSS, /\n\.solmod:has\(\.solmod-body:not\(\.solmod-body--solo\)\)\{max-width:min\(1200px,94vw\);\}/);
  assert.doesNotMatch(QUEUE_CSS, /max-width:min\((760|1200)px/);
  assert.doesNotMatch(QUEUE_CSS, /(^|\n)\.queue-card\{max-width/);
  // After the two-column rule, so the solo card really is one column.
  assert.ok(DIRECT_CSS.indexOf('.solmod-body--solo{grid-template-columns:1fr;}') > DIRECT_CSS.indexOf('.solmod-body{display:grid;'));
  assert.match(js, /'<div class="solmod-body' \+ \(conOrden \? "" : " solmod-body--solo"\) \+ '">'/);
  // El DOCUMENTO primero y grande, los campos leídos al lado: la ficha se abre
  // para comparar el papel con lo que leyó el modelo (handoff 2026-07-27).
  assert.match(js, /\.solmod-body\{display:grid;grid-template-columns:minmax\(0,1fr\) 340px;/);
  assert.match(js, /@media\(max-width:800px\)\{\.solmod-body\{grid-template-columns:1fr;\}\s*\}/);
  assert.match(js, /solmod-label">' \+ esc\(T\("admin\.queue\.overlay\.datos_extraidos", null, "Datos extraídos"\)\)/);
  // What the AI read carries the AI mark right above it, nothing else.
  assert.match(js, /\+ marcaIAHtml\(\) \+ campos \+ "<\/div>"/);
  assert.doesNotMatch(js, /Lectura automática\. Verificá contra la imagen antes de usarla\./);
  assert.match(js, /max-height:78vh/);
});

// 'cola' salía de un `OTRAS` hand-maintained por cada router hermano — el
// Tier 2 module-contract framework (docs/roadmap/module-contract-design.md
// "Admin shell contract" #2) centraliza esa lista en SanaAdminSections,
// derivada de GET /api/admin/modules. Cada router llama
// SanaAdminSections.otras(<su propio id>) en vez de mantener su propio
// literal — 'cola' vive en PLATFORM_SECTIONS (admin-sections.js) así que
// sigue estando en la lista de todos.
test('queue is a server-owned direct screen, not an orphaned router section', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(COLA_ROUTER, /_SECTION_ID = "cola"/);
  assert.match(COLA_SCREEN, /id="sec-cola"/);
  assert.doesNotMatch(js, /SanaAdminRouter|SanaAdminSections|location\.hash|hashchange/);
  assert.doesNotMatch(COLA_SCREEN, /admin-router\.js|admin-sections\.js/);
});

test('la respuesta del paciente al recordatorio se ve en la agenda', () => {
  // Reunión hospital 2026-07-24: "la respuesta del paciente se guarda asociada
  // al turno y es visible desde la agenda para el administrativo".
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /function confirmoHtml\(t\)/);
  assert.match(js, /t\.confirmacion === "si"/);
  assert.match(js, /t\.confirmacion === "no"/);
  assert.match(js, /admin\.queue\.table\.confirmo", null, "Confirmó"/);
  // Sin respuesta = un guión apagado, NUNCA un "no": todavía no contestó, que
  // es otro estado (handoff 2026-07-27; antes era una celda vacía y se leía
  // como un dato que faltó cargar).
  assert.match(js, /admin\.queue\.confirm\.sin_respuesta", null, "Sin respuesta"/);
  assert.match(js, /admin\.queue\.confirm\.aviso_no", null, "Avisó que no viene"/);
  assert.match(js, /admin\.queue\.confirm\.no", null, "No"/);
  // Ordenable como cualquier otra columna.
  assert.match(js, /case "confirmo":/);
});

// ---- huecos libres del día (2026-07-27) ----------------------------------
// reference-deployment counterpart, reunión 2026-07-24: "acá Roger de 8 a 9:30, pero si hay un
// turno libre de 8:30 no lo veo para poder dárselo a alguien que vino".

test('los huecos libres se intercalan en la misma tabla, en su hora', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /function filaLibreHtml\(hora, g\)/);
  assert.match(js, /queue-tr-free/);
  // una sola secuencia ordenada por hora, no una lista aparte; a la misma
  // hora el turno dado va antes que el hueco
  assert.match(js, /filas\.sort\(function \(a, b\) \{ return String\(a\.hora\)\.localeCompare\(String\(b\.hora\)\) \|\| \(\(a\.t \? 0 : 1\) - \(b\.t \? 0 : 1\)\); \}\);/);
  // in the list of several doctors the free hour names whose it is
  assert.match(js, /var deQuien = COLS\.some\(function \(c\) \{ return c\.key === "medico"; \}\) && g/);
  // y sólo cuando la tabla ES la jornada en orden
  assert.match(js, /return !STATE\.qBusca && STATE\.sort\.key === "hora"/);
});

test('dar un hueco abre Crear turno con médico, día y hora puestos', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /data-cola-dar/);
  assert.match(js, /openCreateAppointment\(\{[\s\S]{0,200}hora: dar\.getAttribute\("data-cola-dar"\)/);
});

// Responsable del producto, 2026-08-05: "clicking an empty slot creates a turno". El botón chico
// al costado no alcanzaba — el blanco es el renglón entero.
test('el hueco entero se clickea, no sólo el botón', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  const fn = js.match(/function filaLibreHtml\(hora, g\) \{[\s\S]*?\n  \}/)[0];
  // el <tr> lleva los mismos data-* que el botón, así el delegado no cambia
  assert.match(fn, /<tr class="cnt-tr queue-tr-free[\s\S]*?'\s*\+\s*dar\s*\+\s*">"/);
  assert.match(fn, /queue-tr-give/);
  // y el botón sigue existiendo: es lo que se enfoca con el teclado
  assert.match(fn, /<button type="button" class="btn btn-quiet btn-sm queue-give"/);
  assert.match(js, /\.queue-tr-give\{cursor:pointer;\}/);
});

test('un día sin turnos dados pero con agenda abierta igual se muestra', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /var totalLibres = /);
  assert.match(js, /if \(!totalTodos && !totalLibres && !STATE\.entrevistasSinTurno\.length\)/);
});

test('los horarios libres son una casilla que controla filas libres y agendas enteras', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  // control binario = checkbox, no un <select> de dos opciones
  assert.match(js, /data-cola-libres="1"[\s\S]{0,160}admin\.queue\.filters\.mostrar_libres", null, "Mostrar horarios libres"/);
  assert.match(js, /if \(cronologico && STATE\.verLibres\) \{\s*grupos\.forEach\(function \(g\) \{\s*\(g\.libres \|\| \[\]\)\.forEach/);
  assert.match(js, /if \(!filas\.length && \(palabras\.length \|\| !STATE\.verLibres/);
});

test('un nombre gritado por el padrón se muestra en Nombre Propio', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /function nombrePropio\(v\)/);
  // La implementación se unificó en format.mjs:
  // acá queda el alias, y las reglas (mixto se respeta, partículas en
  // minúscula) se prueban en ui-evals.test.mjs contra el módulo compartido.
  assert.match(js, /return Format\.nombre\(v\)/);
  assert.doesNotMatch(js, /SanaFormat/);
  // se aplica al paciente Y al médico, en la tabla y en la ficha
  assert.ok(js.includes('var nombre = nombrePropio(t.paciente) || T("admin.queue.attendance.sin_nombre", null, "paciente sin nombre");'));
  assert.match(js, /nombrePropio\(g\.medico\) \|\| T\("admin\.queue\.group\.sin_medico", null, "Sin médico asignado"\)/);
  assert.match(js, /nombrePropio\(\(t && t\.paciente\) \|\| ""\) \|\| T\("admin\.queue\.attendance\.sin_nombre", null, "paciente sin nombre"\)/);
  // lo que viaja al backend sigue siendo el valor original
  assert.match(js, /openCreateAppointment\(actual && actual\.medico \? \{ medico: actual\.medico, dia: STATE\.dia \} : \{\}, titleCreate\)/);
  assert.match(js, /data-cola-dar-medico="' \+ esc\(medico \|\| ""\)/);
});

// ---- el responsable del producto 2026-10-26: no counters, no own-name row, compact attendance, forecast as bars ----

test('sin contadores: «Ver agendas sin turnos» sin número y sin «N días con turnos» bajo el mes', () => {
  assert.match(COLA, /admin\.queue\.empty\.ver_agendas", null, "Ver agendas sin turnos"/);
  assert.doesNotMatch(COLA, /ver_agendas_(one|many)|occupied_days|queue-month-footer/);
});

test('la vista propia del médico no tiene una fila con su propio nombre', () => {
  const head = COLA.slice(COLA.indexOf('function medicoElegidoHtml('), COLA.indexOf('function listaDelDiaHtml('));
  assert.match(head, /if \(!g \|\| window\.CURRENT_ROLE === "medico"\) return "";/);
  // ni columna Médico: todas las filas son suyas
  assert.match(COLA, /function columnaMedico\(grupos\) \{\s*return window\.CURRENT_ROLE !== "medico" && !STATE\.filtro && !!grupos\.length;/);
  // Its only control over the table is «Imprimir» (the day on HSI's sheet,
  // 2026-11-07), an icon in the controls row since 2026-11-08.
  const print = COLA.slice(COLA.indexOf('function pintarImprimir('), COLA.indexOf('function elegirMedico('));
  assert.match(print, /var propio = window\.CURRENT_ROLE === "medico" \|\|/);
  assert.match(print, /slot\.innerHTML = propio \? imprimirHtml\(uno\) : "";/);
});

test('«Imprimir» abre el día del médico elegido en la hoja de HSI, sólo con turnos y con médico', () => {
  const fn = COLA.slice(COLA.indexOf('function imprimirHtml('), COLA.indexOf('function medicoElegidoHtml('));
  assert.match(fn, /if \(!g \|\| !g\.medico_id \|\| !\(g\.turnos \|\| \[\]\)\.length\) return "";/);
  assert.match(fn, /"\/admin\/cola\/print\?dia=" \+ encodeURIComponent\(STATE\.dia \|\| ""\)/);
  assert.match(fn, /"&medico=" \+ encodeURIComponent\(g\.medico_id\) \+ "&auto=1"/);
  assert.match(fn, /target="_blank" rel="noopener"/);
  assert.match(fn, /T\("admin\.queue\.print\.print", null, "Imprimir"\)/);
  // an icon with its name as aria-label and tooltip, like the other actions
  assert.match(fn, /aria-label="' \+ esc\(lbl\) \+ '" title="' \+ esc\(lbl\) \+ '"><span class="queue-icon queue-icon-print"/);
  // there while the list is one doctor's day: the doctor's own, or narrowed to one
  assert.match(COLA, /var propio = window\.CURRENT_ROLE === "medico" \|\| \(uno && STATE\.filtro && grupoKey\(uno\) === STATE\.filtro\);/);
});

test('Atendió / No vino es el segmento del panel a la altura de la fila; el estado marcado es punto + texto', () => {
  const fn = COLA.slice(COLA.indexOf('function botonesHtml('), COLA.indexOf('function asistenciaCellHtml('));
  assert.match(fn, /class="queue-seg queue-seg--row queue-attendance-btns" role="group"/);
  assert.doesNotMatch(fn, /class="btn /);
  assert.match(fn, /attendance-dot attendance-dot--' \+ \(vino \? "good" : "bad"\)/);
  assert.match(QUEUE_CSS, /\.queue-seg--row\{height:32px;\}/);
  assert.match(QUEUE_CSS, /\.queue-attendance-yes\{color:var\(--text\);/);
});

test('el pronóstico de asistencia va junto al nombre con las TRES barras de la pieza compartida (H11 → F1)', () => {
  // Product owner 2026-11-09: like a phone's signal bars, next to the name, not a column.
  assert.doesNotMatch(COLA, /key: "historial"|HISTORIAL_OFFERED|function offerOnce/);
  assert.match(COLA, /window\.sanaImportStatic\("platform\/attendance-forecast\.mjs"\)/);
  assert.match(COLA, /ownCleanup\(m\.bind\(document, T\)\)/);
  assert.match(COLA, /function routeDependencies\(\) \{ return Promise\.all\(\[[^\]]*forecastReady/);
  assert.match(COLA, /function readyRoute\(\) \{ return routeDependencies\(\)\.then\(/);
  assert.match(COLA, /dispose\.loaded = routeDependencies\(\)\.then\(/);
  const fn = /function historialBarrasHtml\(t\) \{[\s\S]*?\n  \}/.exec(COLA)?.[0] || '';
  assert.match(fn, /if \(!Forecast \|\| !Forecast\.level\(t\.ranking\)\) return "";/);
  assert.match(fn, /Forecast\.cellHtml\(t\.ranking, T\)/);
  // the card says it in words next to the same bars
  assert.match(COLA, /Forecast\.barsHtml\(t\.ranking, T\)\s*\+ "<span>" \+ esc\(Forecast\.patientLine\(t\.ranking, T\)\)/);
  // ninguna copia local de las barras ni de la escala de cinco tramos
  assert.doesNotMatch(COLA, /forecastSegments|Math\.min\(5, Math\.ceil|i <= 5; i\+\+/);
  assert.match(DIRECT_CSS, /\.queue-forecast--bad \.med-occupancy-segment\.is-filled\{background:var\(--danger\);\}/);
  assert.match(DIRECT_CSS, /\.queue-forecast--warn \.med-occupancy-segment\.is-filled\{background:var\(--warn\);\}/);
});

test('F3: las barras del historial abren su explicación y no ordenan ni abren otra cosa', () => {
  // no Historial header any more: the bars are the button that explains them
  assert.doesNotMatch(COLA, /Forecast\.infoButtonHtml/);
  assert.doesNotMatch(QUEUE_CSS, /th\[data-col='historial'\]/);
  // a click or Enter/Space on the bars reaches neither sorting nor the row
  assert.match(COLA, /if \(e\.target\.closest && e\.target\.closest\("\[data-forecast-info\]"\)\) return;\n\s*var th = e\.target\.closest && e\.target\.closest\("\[data-cola-sort\]"\);/);
  assert.match(COLA, /if \(th && !e\.target\.closest\("\[data-tbl-res\]"\) && !e\.target\.closest\("\[data-forecast-info\]"\)\)/);
  assert.match(QUEUE_CSS, /\.queue-pat-line \.forecast-bars-btn\{min-height:24px;flex:none;\}/);
});

test('el lápiz de corregir alinea con el texto de la asistencia', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /\.queue-attendance-marked\{display:inline-flex;align-items:center;/);
  assert.match(js, /\.queue-fix\{[^}]*display:inline-flex;align-items:center;\}/);
});

// Antes el médico veía el hueco y no podía darlo. Desde 2026-08-05 sí puede,
// porque #cola le muestra UNA agenda —la suya— y el paciente ya está enfrente.
// Lo que sigue siendo del mostrador es abrir Crear turno a mano en la cabecera
// de un médico: eso es agendar por otro.
test('un rol medico da los huecos de su propia agenda, no los de otros', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  const m = /function puedeCrearTurno\(\)\s*\{[\s\S]*?\n  \}/.exec(js);
  assert.ok(m, 'puedeCrearTurno debe existir');
  assert.match(m[0], /"admisionista"/);
  assert.match(m[0], /"medico"/);
  // «Crear turno» del título (con el médico elegido puesto) es de la mesa
  const cab = /function syncCrearTurno\(\) \{[\s\S]*?var agenda = window\.CURRENT_ROLE[^;]*;/.exec(js);
  assert.ok(cab, '«Crear turno» del título debe tener su propio gate');
  assert.doesNotMatch(cab[0], /"medico"/);
  assert.doesNotMatch(js, /data-crear-medico/);
});

// ---- accesibilidad de la mesa de admisión (2026-07-27) -------------------
// La administrativa de turnos del hospital es NO VIDENTE (reunión 2026-07-15).
// Estas guardas fijan lo que un lector de pantalla necesita para que la
// pantalla sea usable, no sólo "técnicamente correcta".

test('cada tabla del día se identifica sola (caption)', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /<caption class="sr-only">' \+ esc\(applyVars\(T\("admin\.queue\.table\.turnos_de", \{ fecha: fechaParaTexto\(STATE\.dia\) \}, "Turnos de \{fecha\}"\)/);
  // la clase se inyecta también acá para que #cola funcione standalone
  assert.match(js, /\.sr-only\{position:absolute/);
});

test('los botones de asistencia dicen de quién son', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /admin\.queue\.attendance\.aria_mark_present", \{ ref: ref \}, "Marcar que atendió a \{ref\}"/);
  assert.match(js, /admin\.queue\.attendance\.aria_mark_absent", \{ ref: ref \}, "Marcar que no vino \{ref\}"/);
  assert.match(js, /function quien\(t\)/);
});

test('lo que cambia sin recargar se anuncia', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /function anunciar\(msg\)/);
  assert.match(js, /id="cola-live" class="sr-only" role="status" aria-live="polite"/);
  // marcar asistencia, cambiar de día y fallar tienen su anuncio
  assert.match(js, /admin\.queue\.attendance\.announce_presente", \{ who: quien\(t\) \}, "Atendió: \{who\}"/);
  assert.match(js, /admin\.queue\.attendance\.announce_ausente", \{ who: quien\(t\) \}, "No vino: \{who\}"/);
  assert.match(js, /admin\.queue\.attendance\.save_error_specific", null, "No se pudo guardar la asistencia\."/);
});

test('la tabla se puede ordenar con el teclado y dice por dónde está ordenada', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /aria-sort="' \+ sort \+ '"/);
  assert.match(js, /tabindex="0"/);
  assert.match(js, /e\.key !== "Enter" && e\.key !== " "/);
});

test('la ficha del paciente es un diálogo y devuelve el foco al cerrarse', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /role="dialog" aria-modal="true"/);
  assert.match(js, /var volverA = document\.activeElement/);
  assert.match(js, /if \(!disposed && volverA && volverA\.isConnected && volverA\.focus\) volverA\.focus\(\)/);
});

test('el toast del panel es una región viva', () => {
  const shell = readFileSync(join(ROOT, 'static', 'root', 'admin-shell.js'), 'utf8');
  assert.match(BASE, /static\('admin-shell\.js'\)/);
  assert.doesNotMatch(BASE, /<div class="toast" id="toast"/);
  assert.match(shell, /t\.setAttribute\("role", "status"\)/);
  assert.match(shell, /t\.setAttribute\("aria-live", "polite"\)/);
  assert.match(shell, /t\.setAttribute\("aria-atomic", "true"\)/);
});

// ---- entrevista previa en la cola (fase 4, modules/intake/) --------------

test('la columna Entrevista no viene de fábrica (SanaTable no esconde columnas todas vacías)', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  const entrada = /\{ key: "entrevista"[^}]*\}/.exec(js)?.[0];
  assert.ok(entrada, 'COLS_ALL debe tener la entrada "entrevista"');
  assert.doesNotMatch(entrada, /"default": true/);
});

test('la celda de Entrevista pinta el triage, el resumen y "Iniciar consulta" cuando está hecha', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /function entrevistaCellHtml\(t\)/);
  assert.match(js, /if \(col\.key === "entrevista"\) return entrevistaCellHtml\(t\);/);
  // sin entrevista = celda vacía, ningún guión inventado
  assert.match(js, /if \(!iv\) return '<td data-col="entrevista" data-label="' \+ esc\(lbl\) \+ '" class="queue-td-empty"><\/td>';/);
  assert.match(js, /queue-triage-curso/);          // estado 'active' = "En curso"
  assert.match(js, /data-iniciar-consulta="/);      // estado 'done' = acción
  assert.match(js, /ROJO: T\("admin\.queue\.triage\.rojo", null, "Rojo"\)/);
  assert.match(js, /NARANJA: T\("admin\.queue\.triage\.naranja", null, "Naranja"\)/);
  assert.match(js, /AMARILLO: T\("admin\.queue\.triage\.amarillo", null, "Amarillo"\)/);
  assert.match(js, /VERDE: T\("admin\.queue\.triage\.verde", null, "Verde"\)/);
});

test('una consulta ya confirmada dice "Atendido" y "Ver consulta", nunca "Iniciar consulta" otra vez (problems.md №1)', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  const accion = /function consultaAccionHtml\(iv\) \{[\s\S]*?\n  \}/.exec(js)?.[0];
  assert.ok(accion, 'una sola función decide la acción de la consulta');
  assert.match(accion, /if \(iv\.consulta_confirmada\) \{[\s\S]*admin\.queue\.triage\.atendido", null, "Atendido"[\s\S]*admin\.queue\.triage\.ver_consulta", null, "Ver consulta"[\s\S]*\n    \}\n    if \(String\(iv\.estado \|\| ""\) !== "done"\) return "";/);
  // la tabla y la lista de entrevistas sin turno usan la misma decisión
  // the definition, the table cell, the card and the list without appointment
  assert.equal((js.match(/consultaAccionHtml\(iv\)/g) || []).length, 4);
  assert.equal((js.match(/admin\.queue\.triage\.iniciar_consulta"/g) || []).length, 1);
});

test('"Actualizar entrevista" (2026-08-01): la celda marca "Actualizada" cuando iv.actualizada viene true', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /if \(iv\.actualizada\) \{/);
  assert.match(js, /queue-triage-actualizada/);
  assert.match(js, /admin\.queue\.triage\.actualizada", null, "Actualizada"/);
});

test('"Iniciar consulta" navigates to the direct consultation URL', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /var consulta = e\.target\.closest\("\[data-iniciar-consulta\]"\)/);
  assert.match(js, /location\.assign\("\/admin\/consult\?interview=" \+ encodeURIComponent\(interviewId\)\)/);
  assert.doesNotMatch(js, /location\.hash\s*=/);
});

test('el médico ve "Entrevistas sin turno" como lista real, no tarjetas sueltas', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /function entrevistasSinTurnoHtml\(\)/);
  assert.match(js, /window\.CURRENT_ROLE !== "medico" \|\| !lista\.length/);
  assert.match(js, /<ul class="queue-unmatched-list">/);
  assert.match(js, /<li class="queue-unmatched-item">/);
  assert.match(js, /STATE\.entrevistasSinTurno = data\.entrevistas_sin_turno \|\| \[\];/);
});

// ---- encabezado de la entrevista previa del propio médico (fase 7) -------

test('entrevistaHeaderHtml es el interruptor del panel (.hor-switch), sólo para rol médico y sólo con config', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /function entrevistaHeaderHtml\(\)/);
  assert.match(js, /if \(window\.CURRENT_ROLE !== "medico" \|\| !cfg\) return "";/);
  assert.match(js, /queue-entrevista-cfg/);
  assert.match(js, /<span class="hor-switch"><input type="checkbox" role="switch" data-entrevista-toggle="' \+ \(cfg\.efectivo \? "0" : "1"\) \+ '"/);
  // One name for the feature, the one «Sistema» uses (the product owner, 2026-11-08).
  assert.match(js, /T\("admin\.queue\.intake\.switch", null, "Captura de síntomas"\)/);
  // The door sign only while the capture is on: off, there is no QR to post.
  assert.match(js, /\(cfg\.efectivo \? '<a href="' \+ esc\(cfg\.cartel_url\) \+ '"[^\n]*admin\.queue\.intake\.cartel", null, "Cartel para la puerta"/);
  // Product owner 2026-10-26: no small line with a browser-default button. The only
  // button is «¿Qué es?», the panel's styled icon button on the same line.
  const header = js.slice(js.indexOf('function entrevistaHeaderHtml()'), js.indexOf('function hayContenidoIA()'));
  const buttons = header.match(/<button[^']*'/g) || [];
  assert.deepEqual(buttons, ['<button type="button" class="queue-icon-btn" data-cola-explainer="1" aria-expanded="\'']);
  assert.match(header, /<span class="queue-icon queue-icon-info" aria-hidden="true">/);
  assert.match(QUEUE_CSS, /\.queue-entrevista-cfg \.queue-icon-btn\{display:inline-flex;width:44px;height:44px;/);
  assert.match(QUEUE_CSS, /\.queue-entrevista-cfg\{[^}]*font-size:16px/);
  assert.match(DIRECT_CSS, /\.hor-switch\{position:relative;display:block;width:48px;height:28px;/);
});

// Demo check 07.11 at 390px: the triangle of «Qué es la captura de síntomas»
// sat past the screen's left edge, and «Elegir columnas» stood alone on a row
// under the day. The explainer takes the phone gutter; at phone width the
// search and the action icons (with the column picker) come first, the day
// and the funnel share the next line; the opened month comes under both.
test('cola en el teléfono: la explicación respeta el margen y «Elegir columnas» va junto a la búsqueda', () => {
  assert.match(DIRECT_CSS, /#sec-cola \.queue-intake-explainer\{padding-left:var\(--adm-gutter\);padding-right:var\(--adm-gutter\);\}/);
  // Since 2026-11-08 the column picker is one of the action icons, on the
  // search's line at every width; the phone moves the day and the funnel to
  // the next line and the opened month under them.
  assert.match(QUEUE_CSS, /\.queue-controls>\.adm-search\{order:0;\}\n\.queue-controls>\.queue-date-picker\{order:1;margin-right:auto;\}\n\.queue-controls>\[data-filter-toggle\]\{order:2;\}\n\.queue-controls>\.queue-actions\{order:3;\}\n\.queue-controls>\.queue-filters\{order:4;flex:1 1 100%;\}/);
  const phone = QUEUE_CSS.slice(QUEUE_CSS.indexOf('@media(max-width:640px){\n/* The phone: the search'));
  assert.match(phone, /\.queue-controls>\.queue-actions\{order:1;\}\n\.queue-controls>\.queue-date-picker\{order:2;flex:1 1 auto;width:auto;margin-right:0;\}\n/);
  assert.match(phone, /\.queue-controls:has\(\.queue-month-calendar\)>\.queue-date-picker\{display:contents;\}\n\.queue-controls:has\(\.queue-month-calendar\) \.queue-datenav\{order:2;flex:1 1 auto;\}\n\.queue-controls:has\(\.queue-month-calendar\) :is\(\.queue-month-calendar,\.queue-today-link\)\{order:5;\}\n\.queue-controls:has\(\.queue-month-calendar\) \.queue-month-calendar\{flex:0 0 100%;margin-top:0;\}\n\.queue-controls>\[data-filter-toggle\]\{order:3;\}/);
  assert.match(COLA, /'<div class="queue-actions">' \+ columnGearHtml\(\)/);
});

test('el encabezado va antes del aviso de IA, de "Entrevistas sin turno" y del resto de la tabla', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /entrevistaHeaderHtml\(\) \+ entrevistaExplainerHtml\(\) \+ avisoIAHtml\(\) \+ entrevistasSinTurnoHtml\(\);/);
});

// Product owner, 2026-11-08: «Hoy» was written twice and the day row was a row of
// boxed buttons. The short date once, then bare icons (anterior / «Hoy» /
// siguiente) and the month as a calendar icon, shaped only on hover/focus.
test('la fila del día: fecha corta, anterior/Hoy/siguiente y el mes como ícono; sin enlace de horarios', () => {
  const nav = COLA.slice(COLA.indexOf('function dateNavHtml()'), COLA.indexOf('// El filtro de médico es un <select>'));
  assert.match(nav, /<div class="queue-day-seg" role="group"/);
  assert.match(nav, /dayStepHtml\(-1, !prev\) \+[\s\S]*data-cola-hoy="1"[\s\S]*dayStepHtml\(1, !next\)/);
  assert.match(nav, /<button type="button" class="queue-icon-btn queue-month-btn" data-cola-calendar-toggle="1" aria-pressed="' \+ calendarOpen \+ '" aria-label="' \+ esc\(mes\) \+ '" title="' \+ esc\(mes\) \+ '">/);
  assert.match(nav, /queue-icon queue-icon-calendar/);
  assert.doesNotMatch(nav, /queue-seg|Horarios de atención|<a href/);
  const fecha = COLA.slice(COLA.indexOf('function fechaLabel('), COLA.indexOf('function fechaParaTexto('));
  assert.doesNotMatch(fecha, /date\.today|Dates\.larga/);
  assert.match(QUEUE_CSS, /\.queue-datenav\{display:flex;align-items:center;gap:8px;margin:0;flex-wrap:nowrap;\}/);
  // Product owner 2026-11-09: bare icons only after the search. The day's three
  // buttons are ONE outlined segment and the month an outlined button.
  assert.match(QUEUE_CSS, /\.queue-day-seg\{display:inline-flex;align-items:stretch;height:44px;box-sizing:border-box;border:1px solid var\(--border-strong\);/);
  assert.match(QUEUE_CSS, /\.queue-day-seg>button\{height:auto;min-height:0;margin:0;border:0;border-left:1px solid var\(--border-strong\);/);
  assert.equal((QUEUE_CSS.match(/\.queue-day-seg\{/g) || []).length, 1, 'one rule shapes the segment');
  assert.match(QUEUE_CSS, /\.queue-controls \.queue-month-btn\{border-color:var\(--border-strong\);background:var\(--panel\);\}/);
  assert.match(QUEUE_CSS, /\.queue-day-hoy\{box-sizing:border-box;padding:0 14px;background:transparent;/);
  assert.match(QUEUE_CSS, /\.queue-icon-calendar\{--queue-icon:url\(\/static\/icons\/calendar-sf-regular\.svg\);\}/);
  // the attendance buttons keep the boxed segment
  assert.match(QUEUE_CSS, /\.queue-seg>button\{[^}]*height:100%/);
});

test('«Ver mes» abierto: la búsqueda y los filtros quedan arriba, en la línea de la fecha', () => {
  // A centred row put the search halfway down the month (audit 2026-11-05): on
  // a wide screen the month leaves the date's box and takes a line of its own
  // under the toolbar, which keeps the closed row's geometry.
  assert.match(QUEUE_CSS, /\.queue-month-calendar\{--queue-month-w:520px;width:var\(--queue-month-w\);/);
  const wide = /@media\(min-width:641px\)\{\n\.queue-controls:has\(\.queue-month-calendar\)[\s\S]*?\n\}/.exec(QUEUE_CSS);
  assert.ok(wide, 'the open-month rule lives in the wide-screen block');
  assert.match(wide[0], /\.queue-controls:has\(\.queue-month-calendar\)>\.queue-date-picker\{display:contents;\}/);
  // after the actions (order 3) and the folded filters (order 4)
  assert.match(wide[0], /\.queue-month-calendar\{order:5;margin-top:0;margin-right:calc\(100% - var\(--queue-month-w\)\);\}/);
  assert.doesNotMatch(QUEUE_CSS, /\.queue-controls[^{]*\{[^}]*align-items:flex-start/);
});

test('la marca de IA va en la ficha, justo arriba del texto de la IA; la lista no lleva aviso', () => {
  const fn = COLA.slice(COLA.indexOf('function hayContenidoIA()'), COLA.indexOf('function avisoIAHtml()'));
  assert.match(fn, /STATE\.entrevistasSinTurno && STATE\.entrevistasSinTurno\.length/);
  assert.match(COLA, /if \(!hayContenidoIA\(\)\) return "";/);
  // Product owner 2026-11-09: no notice over the day's list. The card marks the AI
  // summary and what the AI read from the referral, right above each.
  const lista = COLA.slice(COLA.indexOf('function listaDelDiaHtml('), COLA.indexOf('function pintarImprimir('));
  assert.doesNotMatch(lista, /avisoIATexto|grupoConIA/);
  assert.match(lista, /return medicoElegidoHtml\(elegido\) \+ tablaDelDia\(filas, salida\);/);
  assert.doesNotMatch(COLA, /function grupoConIA/);
  const marca = /function marcaIAHtml\(\) \{[\s\S]*?\n  \}/.exec(COLA)?.[0] || '';
  assert.match(marca, /<p class="adm-ai-notice queue-card-ai" data-ai-notice-inline="clinical">/);
  assert.match(COLA, /\+ \(resumen \? marcaIAHtml\(\) : ""\)/);
  assert.match(COLA, /\+ marcaIAHtml\(\) \+ campos \+ "<\/div>"/);
  assert.match(QUEUE_CSS, /\.queue-card-ai\{margin:0 0 8px;\}/);
});

test('STATE.entrevistaConfig llega fresco en cada carga del día, no se persiste', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /entrevistaConfig: null/);
  assert.match(js, /STATE\.entrevistaConfig = data\.entrevista_config \|\| null;/);
});

test('Activar/Desactivar es dos estados nada más (nunca "heredar") y POSTea my-settings', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  const start = js.indexOf('var entToggle = e.target.closest("[data-entrevista-toggle]");');
  assert.ok(start !== -1, 'el handler de data-entrevista-toggle debe existir');
  const end = js.indexOf('var vac = e.target.closest("[data-cola-vacias]");', start);
  assert.ok(end > start, 'el límite del siguiente handler debe existir');
  const bloque = js.slice(start, end);
  assert.match(bloque, /"\/api\/admin\/intake\/my-settings"/);
  assert.match(bloque, /method: "POST"/);
  assert.match(bloque, /JSON\.stringify\(\{ override: nuevoOn \}\)/);
  assert.match(bloque, /cargar\(\);/);
});

test('las cuatro fichas de triage tienen su color en el cascarón directo (rojo/naranja/amarillo/verde)', () => {
  assert.match(DIRECT_CSS, /\.queue-triage--rojo\{/);
  assert.match(DIRECT_CSS, /\.queue-triage--naranja\{/);
  assert.match(DIRECT_CSS, /\.queue-triage--amarillo\{/);
  assert.match(DIRECT_CSS, /\.queue-triage--verde\{/);
});

// Preferences are observed through the actual mounted controller's table
// configuration and rendered gear, never extracted private closure functions.
async function mountedColumns(t, stored, data, { delayed = false, writes = [], offered = false } = {}) {
  const initialData = { dia: '2026-09-09', grupos: [{ medico_id: 'm1', medico: 'Doctor One',
    turnos: [{ id: 'one', hora: '09:00', paciente: 'Synthetic Patient', servicio: 'Consulta' }], libres: [] }], ...data };
  const f = fixture(t, { queueDom: true, initialData }), configs = [];
  let release;
  const store = new Map(stored == null ? [] : [['day_queue.columns', stored]]);
  if (offered) store.set('day_queue.columns_card_moved', true);
  const prefs = { load: async () => {}, get: (key, fallback) => store.has(key) ? store.get(key) : fallback, set(key, value) { store.set(key, value); writes.push([key, value]); } };
  f.modules.set('platform/admin-preferences.mjs', delayed
    ? new Promise(resolve => { release = resolve; }) : Promise.resolve(prefs));
  const owner = f.mount(), readiness = [];
  owner.ready.then(() => readiness.push(true));
  f.readyTable(configs);
  if (delayed) {
    await flush();
    assert.deepEqual(readiness, [], 'readiness must wait for preference hydration');
    release(prefs);
  }
  await owner.ready;
  assert.equal(configs.length, 1);
  return { ...f, columns: configs[0].cols(), html: f.nodes.get('sec-cola').innerHTML };
}
// Pedido de la mesa, 2026-10-08: la mesa recarga cada turno en HSI por documento.
const DNI_DAY = { grupos: [{ medico_id: 'm1', medico: 'Doctor One', libres: [], turnos: [
  { id: 'one', hora: '09:00', paciente: 'Synthetic Patient', dni: '30.111.222', servicio: 'Consulta' },
  { id: 'two', hora: '09:20', paciente: 'Anotado a mano', dni: null, servicio: 'Consulta' }] }] };
// Product owner 2026-11-09: the row is «Name, N años» as one link to the card; the DNI,
// Confirmó and attendance live in the card, not in a column.
test('the row is hour, the name link and the doctor; no DNI column', async t => {
  const r = await mountedColumns(t, null, DNI_DAY);
  assert.deepEqual(r.columns.map(c => c.key).filter(k => k !== 'relleno'), ['hora', 'paciente', 'medico']);
  const rows = r.nodes.get('cola-results').innerHTML;
  assert.match(rows, /<button type="button" class="queue-pat-link" data-cola-abrir="one"/);
  assert.doesNotMatch(rows, /data-col="dni"|data-copy-dni/);
});
test('a saved list loses «Confirmó» once (it moved to the card); old keys are ignored', async t => {
  const writes = [];
  const r = await mountedColumns(t, ['dni', 'servicio', 'confirmo', 'cobertura'], DNI_DAY, { writes });
  assert.deepEqual(writes, [['day_queue.columns_card_moved', true], ['day_queue.columns', ['dni', 'servicio', 'cobertura']]]);
  const keys = r.columns.map(c => c.key);
  assert.equal(keys.includes('confirmo'), false);
  assert.equal(keys.includes('cobertura'), true);
  assert.equal(keys.includes('dni') || keys.includes('servicio'), false);
});
test('once moved, a ticked «Confirmó» stays and nothing is rewritten', async t => {
  const writes = [];
  const r = await mountedColumns(t, ['confirmo'], DNI_DAY, { writes, offered: true });
  assert.equal(r.columns.some(c => c.key === 'confirmo'), true);
  assert.deepEqual(writes, []);
});
const INTERVIEW_DAY = { grupos: [{ medico_id: 'm1', medico: 'Doctor One', libres: [],
  turnos: [{ id: 'one', hora: '09:00', paciente: 'Synthetic Patient', entrevista: { triage: 'ROJO' } }] }] };
const interviewVisible = result => result.columns.some(column => column.key === 'entrevista');

test('mounted table auto-shows an interview when no column preference is stored', async t => {
  const r = await mountedColumns(t, null, INTERVIEW_DAY);
  assert.equal(interviewVisible(r), true);
  assert.match(r.html, /data-cola-col="entrevista" checked/);
});
test('mounted table auto-shows unmatched interviews even without day appointments', async t => {
  const r = await mountedColumns(t, null, { grupos: [], entrevistas_sin_turno: [{ id: 'unmatched', triage: 'VERDE' }] });
  assert.equal(interviewVisible(r), true);
});
test('mounted table omits interview when both data and installation feature are absent', async t => {
  const r = await mountedColumns(t, null, {});
  assert.equal(interviewVisible(r), false);
  assert.doesNotMatch(r.html, /data-cola-col="entrevista" checked/);
});
test('enabled interview capture includes its column before the first interview arrives', async t => {
  const r = await mountedColumns(t, null, { entrevista_config: { efectivo: true } });
  assert.equal(interviewVisible(r), true);
});
test('an explicit saved column selection wins over interview auto-show', async t => {
  const r = await mountedColumns(t, ['servicio', 'confirmo'], INTERVIEW_DAY);
  assert.equal(interviewVisible(r), false);
  assert.doesNotMatch(r.html, /data-cola-col="entrevista" checked/);
});
test('an explicitly selected interview column remains on an interview-free day', async t => {
  const r = await mountedColumns(t, ['entrevista'], {});
  assert.equal(interviewVisible(r), true);
});
test('delayed preferences gate readiness then compute actual default interview columns', async t => {
  const r = await mountedColumns(t, null, INTERVIEW_DAY, { delayed: true });
  assert.equal(interviewVisible(r), true);
});

test('la carga ESM recalcula columnas después de hidratar preferencias', () => {
  assert.match(COLA, /return m\.load\(\)\.then\(function \(\) \{\s*if \(disposed\) return;\s*VISIBLE_KEYS = loadVisibleKeys\(\);\s*HAS_STORED_COLS = storedVisibleKeys\(\) !== null;/);
});

test('el engranaje refleja el auto-show: el checkbox de Entrevista sale tildado sin estar en VISIBLE_KEYS', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /var auto = autoShowEntrevista\(\);/);
  assert.match(js, /var on = VISIBLE_KEYS\.indexOf\(c\.key\) !== -1 \|\| \(auto && c\.key === "entrevista"\);/);
});

test('tocar CUALQUIER checkbox del engranaje fija la preferencia (apaga el auto-show desde ahí)', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  assert.match(js, /HAS_STORED_COLS = true;/);
  // el flag se fija DESPUÉS de guardar el array y ANTES de recalcular COLS
  // (si no, ese mismo guardado seguiría auto-mostrando la columna aunque no
  // esté tildada, o el repintado usaría el flag viejo).
  assert.match(js, /Prefs\.set\("day_queue\.columns", VISIBLE_KEYS\);[\s\S]{0,320}HAS_STORED_COLS = true;[\s\S]{0,40}COLS = activeCols\(\);/);
});

test('COLS se recalcula en cada render (el auto-show depende de los datos del día, no sólo de una vez)', () => {
  const js = COLA + '\n' + QUEUE_CSS; // Source policy spans the real module and its document stylesheet.
  const renderFn = /function render\(\) \{[\s\S]*?\n  \}/.exec(js)?.[0];
  assert.ok(renderFn, 'function render() no encontrada');
  assert.match(renderFn, /hydratePrefs\(\);/);
  assert.match(renderFn, /COLS = activeCols\(\);/);
});

// One flat list for the day (the product owner, 2026-11-08): every doctor's patients in
// one table, the doctor as a column whose name narrows the list to that
// doctor. Tested through mounted events on the real module.
function doctorGroup(id, patient) {
  return { medico_id: id, medico: id ? 'Doctor ' + id : '', especialidad: 'Consulta', centro: 'Synthetic Center',
    turnos: [{ id: patient, paciente: patient, hora: '09:00', servicio: 'Consulta' }], libres: [] };
}
async function doctors(t, groups = [doctorGroup('a', 'Patient Alpha'), doctorGroup('b', 'Patient Beta')]) {
  const f = fixture(t, { queueDom: true, initialData: { dia: '2026-09-09', grupos: groups } });
  const owner = f.mount(); f.readyTable(); await owner.ready;
  return { ...f, rendered: () => f.nodes.get('cola-results').innerHTML };
}
function selectDoctor(f, id) { click(f, '[data-cola-doctor]', { 'data-cola-doctor': id }); }
const tbodies = html => (html.match(/<tbody>/g) || []).length;

test('a single doctor sees appointments immediately without a redundant choice', async t => {
  const f = await doctors(t, [doctorGroup('a', 'Patient Alpha')]);
  assert.match(f.rendered(), /Patient Alpha/);
  assert.doesNotMatch(f.rendered(), /queue-detail-empty|queue-doctor-head|data-grupo-toggle/);
});
test('several doctors share ONE list, each row signed with its doctor', async t => {
  const f = await doctors(t);
  const html = f.rendered();
  assert.match(html, /Patient Alpha/); assert.match(html, /Patient Beta/);
  assert.equal(tbodies(html), 1, 'one table body for the whole day');
  assert.match(html, /<th [^>]*data-col="medico"/);
  assert.match(html, /<button type="button" class="queue-doctor-link" data-cola-doctor="a"[^>]*>Doctor a<\/button>/);
  assert.match(html, /data-cola-doctor="b"[^>]*>Doctor b<\/button>/);
  assert.doesNotMatch(html, /queue-detail-empty|queue-doctor-head|aria-selected/);
});
test('a doctor name narrows the list, names the doctor once and «Ver todos» brings everyone back', async t => {
  const f = await doctors(t);
  selectDoctor(f, 'a');
  let html = f.rendered();
  assert.match(html, /Patient Alpha/); assert.doesNotMatch(html, /Patient Beta/);
  assert.match(html, /<div class="queue-doctor-head" data-cola-elegido="a"><h2 data-export-title-part>Doctor a<\/h2>/);
  assert.match(html, /class="queue-doctor-all" data-cola-doctor="">Ver todos los médicos</);
  assert.doesNotMatch(html, /data-col="medico"/, 'the narrowed list does not repeat the name in a column');
  selectDoctor(f, 'b');
  html = f.rendered();
  assert.match(html, /Patient Beta/); assert.doesNotMatch(html, /Patient Alpha/);
  selectDoctor(f, '');
  html = f.rendered();
  assert.match(html, /Patient Alpha/); assert.match(html, /Patient Beta/);
  assert.doesNotMatch(html, /queue-doctor-head/);
});
test('search narrows the flat list to the matching rows', async t => {
  const f = await doctors(t);
  const input = { value: 'Beta', closest: selector => selector === '[data-cola-buscar]' ? input : null };
  f.doc.emit('input', { target: input });
  assert.match(f.rendered(), /Patient Beta/); assert.doesNotMatch(f.rendered(), /Patient Alpha/);
});
test('a search with nothing for the chosen doctor keeps the way back under the sentence', async t => {
  const f = await doctors(t); selectDoctor(f, 'a');
  const input = { value: 'Beta', closest: selector => selector === '[data-cola-buscar]' ? input : null };
  f.doc.emit('input', { target: input });
  assert.match(f.rendered(), /No se encontraron turnos para «Beta»/);
  assert.match(f.rendered(), /data-cola-doctor="">Ver todos los médicos</);
});
test('unassigned appointments are a value of the doctor column and narrow like any other', async t => {
  const f = await doctors(t, [doctorGroup('a', 'Patient Alpha'), doctorGroup(null, 'Patient Lab')]);
  assert.match(f.rendered(), /data-cola-doctor="_sin_medico"[^>]*>Sin médico asignado<\/button>/);
  selectDoctor(f, '_sin_medico');
  assert.match(f.rendered(), /Patient Lab/); assert.doesNotMatch(f.rendered(), /Patient Alpha/);
});
test('Enter and Space activate the actual doctor selection delegate', async t => {
  const f = await doctors(t);
  for (const [key, id, patient, other] of [['Enter', 'a', 'Patient Alpha', 'Patient Beta'], [' ', 'b', 'Patient Beta', 'Patient Alpha']]) {
    let prevented = false;
    const row = { closest: selector => selector === '[data-cola-doctor]' ? row : null, click: () => selectDoctor(f, id) };
    f.doc.emit('keydown', { key, target: row, preventDefault() { prevented = true; } });
    assert.equal(prevented, true); assert.match(f.rendered(), new RegExp(patient));
    assert.doesNotMatch(f.rendered(), new RegExp(other));
  }
});

// La vista propia del médico (rol wire 'medico') busca SOLO paciente — nombre
// o DNI: el placeholder lo dice, scoreTurno lo cumple y no se ofrece filtro ni
// búsqueda de médico. La cola de la mesa (admin/operador/admisionista) sigue
// buscando por médico, servicio y el resto de los campos de siempre.
function pacienteGroup(medicoId, nombreMedico) {
  return {
    medico_id: medicoId, medico: nombreMedico, especialidad: 'Clínica', centro: 'CAPS',
    turnos: [
      { id: 't1', paciente: 'Paciente Alfa', dni: '36555444', hora: '09:00', servicio: 'Clínica médica' },
      { id: 't2', paciente: 'Paciente Beta', dni: '27888666', hora: '09:30', servicio: 'Pediatría' }
    ], libres: []
  };
}
async function mountedQueue(t, role, groups, savedFilters) {
  const f = fixture(t, { queueDom: true, initialData: { dia: '2026-09-09', grupos: groups } });
  if (role) f.win.CURRENT_ROLE = role;
  if (savedFilters) f.modules.set('platform/admin-preferences.mjs', Promise.resolve({
    load: async () => {}, set() {},
    get: (key, fallback) => key === 'day_queue.filters' ? savedFilters : fallback
  }));
  const owner = f.mount(); f.readyTable(); await owner.ready;
  return {
    ...f,
    controls: () => f.nodes.get('sec-cola').innerHTML,
    rendered: () => f.nodes.get('cola-results').innerHTML,
    type(text) {
      const input = { value: text, closest: selector => selector === '[data-cola-buscar]' ? input : null };
      f.doc.emit('input', { target: input });
    }
  };
}

test('la vista propia del médico promete nombre o DNI y no ofrece filtro de médico', async t => {
  const f = await mountedQueue(t, 'medico', [pacienteGroup('med-1', 'Dra. Ana Pérez'), pacienteGroup('med-2', 'Dr. Bruno López')]);
  assert.match(f.controls(), /placeholder="Paciente o DNI"/);
  assert.match(f.controls(), /aria-label="Buscar paciente de hoy por nombre o DNI"/);
  assert.doesNotMatch(f.controls(), /data-cola-medfiltro/);
});
test('la mesa conserva el buscador y el filtro de médico de siempre', async t => {
  const f = await mountedQueue(t, null, [pacienteGroup('med-1', 'Dra. Ana Pérez'), pacienteGroup('med-2', 'Dr. Bruno López')]);
  assert.match(f.controls(), /placeholder="Paciente o médico"/);
  assert.match(f.controls(), /aria-label="Buscar en la cola por paciente, médico, servicio o fecha"/);
  assert.match(f.controls(), /data-cola-medfiltro/);
});
test('buscar por médico matchea en la mesa y no matchea en la vista del médico', async t => {
  const medico = await mountedQueue(t, 'medico', [pacienteGroup('med-1', 'Dra. Ana Pérez')]);
  medico.type('Ana');
  assert.match(medico.rendered(), /No se encontraron turnos para «Ana»/);
  const mesa = await mountedQueue(t, null, [pacienteGroup('med-1', 'Dra. Ana Pérez')]);
  mesa.type('Ana');
  assert.match(mesa.rendered(), /Dra\. Ana Pérez/);
});
test('el servicio matchea para la mesa y deja de matchear en la vista del médico', async t => {
  const medico = await mountedQueue(t, 'medico', [pacienteGroup('med-1', 'Dra. Ana Pérez')]);
  medico.type('Pediatría');
  assert.match(medico.rendered(), /No se encontraron turnos para «Pediatría»/);
  const mesa = await mountedQueue(t, null, [pacienteGroup('med-1', 'Dra. Ana Pérez')]);
  mesa.type('Pediatría');
  assert.match(mesa.rendered(), /Paciente Beta/);
});
test('en la vista del médico el nombre y el DNI del paciente sí matchean', async t => {
  const f = await mountedQueue(t, 'medico', [pacienteGroup('med-1', 'Dra. Ana Pérez')]);
  f.type('Alfa');
  assert.match(f.rendered(), /Paciente Alfa/); assert.doesNotMatch(f.rendered(), /Paciente Beta/);
  f.type('27888666');
  assert.match(f.rendered(), /Paciente Beta/); assert.doesNotMatch(f.rendered(), /Paciente Alfa/);
});
test('el DNI escrito con puntos, como lo muestra la cola, encuentra al paciente', async t => {
  for (const role of [null, 'medico']) {
    const f = await mountedQueue(t, role, [pacienteGroup('med-1', 'Dra. Ana Pérez')]);
    f.type('27.888.666');
    assert.match(f.rendered(), /Paciente Beta/, String(role));
    assert.doesNotMatch(f.rendered(), /Paciente Alfa/, String(role));
  }
});
// Requisito H3 (requisitos de la cola del médico, responsable del producto 21.10 ítem 13): el
// buscador del médico promete nombre o DNI, pero una fecha escrita abre ese día igual
// que en la mesa — no queda como texto que no encuentra a nadie.
test('una fecha escrita en el buscador del médico abre ese día', async t => {
  const f = await mountedQueue(t, 'medico', [pacienteGroup('med-1', 'Dra. Ana Pérez')]);
  f.type('2026-09-10');
  f.tick(260);
  assert.deepEqual(f.requests.map(r => r.url), ['/api/admin/day-queue?dia=2026-09-10']);
  assert.doesNotMatch(f.rendered(), /No se encontraron turnos/);
});
// Product owner 27.10 (q1044534, «Hidden, per the checkbox»): los horarios libres arrancan ocultos,
// «Mostrar horarios libres» los muestra y la elección se guarda con la pantalla.
function libresGroup(id, name, turnos) {
  return { medico_id: id, medico: name, especialidad: 'Clínica', centro: 'Hospital',
    turnos: turnos.map((t, i) => ({ id: id + '-t' + i, paciente: t, dni: '2000000' + i, hora: '10:0' + i, servicio: 'Clínica' })),
    libres: ['08:00', '08:20'] };
}
test('los horarios libres arrancan ocultos', async t => {
  const f = await mountedQueue(t, 'medico', [libresGroup('med-1', 'Dra. Ana Pérez', ['Paciente Alfa'])]);
  assert.match(f.rendered(), /Paciente Alfa/);
  assert.doesNotMatch(f.rendered(), /queue-tr-free|data-cola-dar=/, 'sin filas libres de fábrica');
  assert.doesNotMatch(f.controls(), /data-cola-libres="1" checked/, 'la casilla arranca sin tildar');
});
test('un día sin turnos y con agenda abierta lo dice y señala la casilla', async t => {
  const f = await mountedQueue(t, 'medico', [libresGroup('med-1', 'Dra. Ana Pérez', [])]);
  assert.doesNotMatch(f.rendered(), /data-cola-dar=/);
  assert.match(f.rendered(), /Mostrar horarios libres/);
});
// Responsable del producto 05.11: «Servicio» que repite la cabecera del médico en todas las filas no se pinta
// (a 1280 recortaba el nombre con la edad); la práctica pasa debajo del nombre.
function mismoServicioGroup(servicios) {
  return { medico_id: 'med-1', medico: 'Dra. Agustina Conti', especialidad: 'Dermatología', centro: 'Hospital',
    turnos: servicios.map((s, i) => ({ id: 'd' + i, paciente: 'Paciente ' + i, dni: '3000000' + i, hora: '10:0' + i, servicio: s,
      ai_campos: i === 0 ? { practicas: { valor: ['Biopsia'] } } : {} })), libres: [] };
}
test('la lista de todos los médicos lleva el servicio debajo del médico, no una columna', async t => {
  const plana = await mountedQueue(t, null, [mismoServicioGroup(['Dermatología', 'Cirugía menor'])]);
  const html = plana.rendered();
  assert.doesNotMatch(html, /data-col="servicio"/, 'no Servicio column, header, <col> or cell');
  assert.match(html, /<td data-col="medico"[^>]*><button type="button" class="queue-doctor-link" data-cola-doctor="[^"]+"[^>]*>[^<]+<\/button><span class="queue-sub" data-export-omit>Cirugía menor<\/span><\/td>/);
  assert.doesNotMatch(html, /data-col="paciente"(?:(?!<\/td>)[\s\S])*queue-sub/, 'the doctor column carries it');
});
test('sin columna de médico el servicio va bajo el nombre sólo si el día mezcla servicios', async t => {
  const igual = await mountedQueue(t, null, [mismoServicioGroup(['Dermatología', 'dermatologia', 'Dermatología'])], { filtro: 'med-1' });
  assert.match(igual.rendered(), /<\/h2><span class="queue-doctor-sub">Dermatología · Hospital<\/span>/);
  assert.doesNotMatch(igual.rendered(), /data-col="medico"|class="queue-sub"/, 'the header already says it');
  const mixto = await mountedQueue(t, null, [mismoServicioGroup(['Dermatología', 'Cirugía menor'])], { filtro: 'med-1' });
  assert.match(mixto.rendered(), /data-col="paciente"(?:(?!<\/td>)[\s\S])*<span class="queue-sub" data-export-omit>Cirugía menor<\/span><\/td>/);
  const propia = await mountedQueue(t, 'medico', [mismoServicioGroup(['Dermatología', 'Dermatología'])]);
  assert.doesNotMatch(propia.rendered(), /data-col="medico"|class="queue-sub"/);
});
// 2026-11-09: on the phone the interview cell is a block and its parts sit
// inline — without a space in the markup «Amarillo» touched «Iniciar consulta».
test('the interview cell keeps a space between the triage chip, «Actualizada» and the button', async t => {
  const g = { medico_id: 'med-1', medico: 'Dra. Ana Pérez', especialidad: 'Clínica', centro: 'Hospital', libres: [], turnos: [
    { id: 'e1', paciente: 'Paciente Uno', dni: '20000001', hora: '10:00', servicio: 'Clínica',
      entrevista: { id: 'iv1', estado: 'done', triage: 'AMARILLO', actualizada: true } },
    { id: 'e2', paciente: 'Paciente Dos', dni: '20000002', hora: '10:20', servicio: 'Clínica',
      entrevista: { id: 'iv2', estado: 'done', triage: 'VERDE', consulta_confirmada: true } }] };
  const html = (await mountedQueue(t, 'medico', [g])).rendered();
  assert.match(html, /queue-triage--amarillo">[^<]+<\/span> <span class="queue-triage-actualizada"[^>]*>[^<]+<\/span> <button data-export-omit type="button" class="btn btn-quiet btn-sm queue-consult"/);
  assert.match(html, /queue-triage--verde">[^<]+<\/span> <span class="queue-triage-atendido">[^<]+<\/span> <button data-export-omit/);
  // The phone lays the parts out in a row with a real gap; an empty cell stays hidden.
  const phone = QUEUE_CSS.slice(QUEUE_CSS.indexOf('@media(max-width:640px){'), QUEUE_CSS.indexOf('@media(max-width:359px){'));
  assert.match(phone, /\.queue-table tr>td\.queue-td-triage:not\(\.queue-td-empty\)\{display:flex;flex-direction:row;flex-wrap:wrap;align-items:center;gap:4px 10px;\}/);
});
test('quien eligió ver los horarios libres los sigue viendo al volver', async t => {
  const f = await mountedQueue(t, 'medico', [libresGroup('med-1', 'Dra. Ana Pérez', ['Paciente Alfa'])], { verLibres: true });
  assert.match(f.rendered(), /data-cola-dar="08:00"/);
  assert.match(f.controls(), /data-cola-libres="1" checked/);
});
test('tildar «Mostrar horarios libres» los muestra y guarda la elección', async t => {
  const saved = [];
  const f = fixture(t, { queueDom: true, initialData: { dia: '2026-09-09', grupos: [libresGroup('med-1', 'Dra. Ana Pérez', ['Paciente Alfa'])] } });
  f.win.CURRENT_ROLE = 'medico';
  f.modules.set('platform/admin-preferences.mjs', Promise.resolve({
    load: async () => {}, set(key, value) { saved.push([key, value]); }, get: (key, fallback) => fallback
  }));
  const owner = f.mount(); f.readyTable(); await owner.ready;
  const box = { checked: true, closest: s => s === '[data-cola-libres]' ? box : null };
  f.doc.emit('change', { target: box });
  assert.match(f.nodes.get('cola-results').innerHTML, /data-cola-dar="08:00"/);
  const filters = saved.filter(([k]) => k === 'day_queue.filters').pop();
  assert.ok(filters, 'se guardan los filtros de la pantalla');
  assert.equal(filters[1].verLibres, true);
});
test('un filtro de médico guardado por la mesa no vacía el día del médico', async t => {
  const f = await mountedQueue(t, 'medico',
    [pacienteGroup('med-1', 'Dra. Ana Pérez'), pacienteGroup('med-2', 'Dr. Bruno López')],
    { filtro: 'med-2', filtroMedicoEtiqueta: 'Dr. Bruno López' });
  assert.doesNotMatch(f.controls(), /data-cola-medfiltro/);
  assert.equal((f.rendered().match(/data-cola-row=/g) || []).length, 4, 'every row of the day stays');
  assert.doesNotMatch(f.rendered(), /queue-doctor-head|data-col="medico"/, 'a doctor\'s own day names nobody');
});
test('los catálogos es/en traen las cadenas del buscador del médico', () => {
  const es = JSON.parse(readFileSync(join(ROOT, 'locales', 'es', 'admin.queue.json'), 'utf8'));
  const en = JSON.parse(readFileSync(join(ROOT, 'locales', 'en', 'admin.queue.json'), 'utf8'));
  assert.equal(es['admin.queue.filters.buscar_placeholder_medico'], 'Paciente o DNI');
  assert.equal(es['admin.queue.filters.buscar_aria_medico'], 'Buscar paciente de hoy por nombre o DNI');
  assert.equal(en['admin.queue.filters.buscar_placeholder_medico'], 'Patient or DNI');
  assert.equal(en['admin.queue.filters.buscar_aria_medico'], "Search today's patients by name or DNI");
});

test('el nombre del médico en la lista pasa a una segunda línea, nunca termina en puntos suspensivos', () => {
  const sel = '.queue-table td[data-col="medico"].queue-td-clip{';
  const at = QUEUE_CSS.indexOf(sel);
  assert.notEqual(at, -1, 'falta la regla de la columna Médico');
  const body = QUEUE_CSS.slice(at + sel.length, QUEUE_CSS.indexOf('}', at));
  assert.match(body, /white-space:normal/);
  assert.match(body, /text-overflow:clip/);
});

// Review of 2026-11-09: the last column draws no divider (nothing follows it),
// «Orden» keeps 16 px from the doctor's column, the card's centre reads in the
// text's colour, not as a hint.
test('review 2026-11-09: last divider, gap after the patient line, centre colour', () => {
  const lastSel = "th:is(:last-child,:has(+ th[data-col='relleno']))>.tbl-res:not(:hover)::after{";
  const at = DIRECT_CSS.indexOf(lastSel);
  assert.notEqual(at, -1, 'falta la regla del divisor de la ultima columna');
  assert.match(DIRECT_CSS.slice(at, DIRECT_CSS.indexOf('}', at)), /background:transparent/);
  assert.match(QUEUE_CSS, /@media\(min-width:1025px\)\{\.queue-table td\.queue-td-pat\{padding-right:11px;\}\}/);
  const head = COLA.slice(COLA.indexOf('var svc = \'<p class="solmod-svc">\''), COLA.indexOf('var asis ='));
  assert.ok(head.includes('esc(t.centro)'), 'el centro va en la cabecera de la ficha');
  assert.doesNotMatch(head, /cnt-muted/);
});
