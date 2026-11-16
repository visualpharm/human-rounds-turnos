import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { css as calendarCss } from '../static/platform/calendar-grid.mjs';

const src = fs.readFileSync(new URL('../static/platform/admin-content.mjs', import.meta.url), 'utf8');
const shell = fs.readFileSync(new URL('../templates/admin/base.html', import.meta.url), 'utf8');
const directCss = fs.readFileSync(new URL('../static/root/admin-direct.css', import.meta.url), 'utf8');
const app = fs.readFileSync(new URL('../static/root/admin-app.js', import.meta.url), 'utf8');
const loginScreen = fs.readFileSync(new URL('../templates/admin/login.html', import.meta.url), 'utf8');
const contentScreen = fs.readFileSync(new URL('../templates/admin/content.html', import.meta.url), 'utf8');
const reportesScreen = fs.readFileSync(new URL('../templates/admin/reportes.html', import.meta.url), 'utf8');
const pacientesScreen = fs.readFileSync(new URL('../templates/admin/pacientes.html', import.meta.url), 'utf8');
const contentEs = JSON.parse(fs.readFileSync(new URL('../locales/es/admin.content.json', import.meta.url), 'utf8'));
const contentEn = JSON.parse(fs.readFileSync(new URL('../locales/en/admin.content.json', import.meta.url), 'utf8'));
const systemEs = JSON.parse(fs.readFileSync(new URL('../locales/es/admin.system.json', import.meta.url), 'utf8'));
const systemEn = JSON.parse(fs.readFileSync(new URL('../locales/en/admin.system.json', import.meta.url), 'utf8'));
const users = fs.readFileSync(new URL('../static/platform/admin-users.mjs', import.meta.url), 'utf8');
const account = fs.readFileSync(new URL('../static/platform/admin-account.mjs', import.meta.url), 'utf8');
// El editor de "Horarios de atención" vive en su propio módulo desde el
// rediseño 2026-08-04: dibuja la grilla y es dueño de su CSS.
const horas = fs.readFileSync(new URL('../static/platform/admin-hours-editor.mjs', import.meta.url), 'utf8');

test('Servicios tiene filtros funcionales por centro y modo', () => {
  assert.match(src, /var SERVICE_FILTERS = \{ centro: "", modo: "" \}/);
  assert.match(src, /id="svc-f-centro"/);
  assert.match(src, /id="svc-f-modo"/);
  assert.match(src, /wireServiceFiltros\(sec\)/);
  assert.match(src, /visible = visible\.filter\(matchesFiltrosServicios\)/);
});

// Product owner 05.09 C1, 21.10, 26.10: «fewer than ~10 records — do not draw filters
// at all… one shared helper»; 15.07, 28.07: el filtro se llama como el campo.
test('Médicos/Servicios y el calendario del médico: filtros sólo con diez filas o más, por la regla compartida', async () => {
  const Filters = await import('../static/platform/admin-filter-controls.mjs');
  const vm = await import('node:vm');
  const take = (name) => src.match(new RegExp(`  function ${name}\\([^]*?\\n  \\}\\n`))[0];
  assert.match(take('filterOffered'), /Filters\.offerFilter\(count, !!current, rows\)/);
  for (const call of ['centros, MED_FILTERS.centro, items.length', 'especialidades, MED_FILTERS.especialidad, items.length',
    'agendas, MED_FILTERS.agenda, items.length', 'Object.keys(centros).length, SERVICE_FILTERS.centro, rows',
    'Object.keys(modos).length, SERVICE_FILTERS.modo, rows']) {
    assert.ok(src.includes('filterOffered(' + call + ')'), call);
  }
  const render = (turnos, filters = {}) => vm.runInNewContext(take('filterOffered') + take('medCalFiltersHtml') + 'medCalFiltersHtml(TURNOS);', {
    Filters, TURNOS: turnos, MEDCAL_FILTERS: { q: '', servicio: '', centro: '', ...filters },
    esc: (s) => String(s), T: (_k, _v, fallback) => fallback,
  });
  const turno = (i) => ({ servicio: i % 2 ? 'Cardiología' : 'Clínica', centro: i % 2 ? 'Hospital' : 'CAPS Norte' });
  const nine = Array.from({ length: 9 }, (_, i) => turno(i));
  assert.equal(render(nine), '', 'nine appointments: no search, no filters');
  assert.match(render(nine, { centro: 'Hospital' }), /data-med-filter="centro"/, 'a set filter stays');
  const twelve = Array.from({ length: 12 }, (_, i) => turno(i));
  const html = render(twelve);
  assert.match(html, /data-med-filter="q"/);
  assert.match(html, /data-med-filter="servicio"><option value="">Servicio</);
  assert.match(html, /data-med-filter="centro"><option value="">Centro</);
  assert.doesNotMatch(src, /calendar_all_(services|centers)/);
});

// Critical review 08.11 (B10, the product owner R14): a doctor pointing at a center that does
// not exist showed its key «caps-sur»; it reads as a nameless center, no link.
test('Médicos/Servicios: un centro que no existe no muestra su clave ni un enlace', async () => {
  const vm = await import('node:vm');
  const take = (name) => src.match(new RegExp(`  function ${name}\\([^]*?\\n  \\}\\n`))[0];
  const cell = (slug, cache) => vm.runInNewContext(take('centroEncontrado') + take('centroNombre') + take('centroCellHtml') + 'centroCellHtml(SLUG);', {
    SLUG: slug, CACHE: cache, centrosVivos: () => (cache.centros ? cache.centros.items : []),
    esc: (s) => String(s), T: (_k, _v, fallback) => fallback, puede: () => true,
    itemHref: (tipo, id) => '/admin/' + tipo + '?item=' + id,
  });
  const loaded = { centros: { items: [{ slug: 'hospital', name: 'Hospital de Pinamar' }] } };
  assert.equal(cell('hospital', loaded), '<a href="/admin/centros?item=hospital">Hospital de Pinamar</a>');
  assert.equal(cell('caps-sur', loaded), 'Centro sin nombre');
  assert.equal(cell('', loaded), '<span class="cnt-muted">—</span>');
});

// Opus review of the panel (2026-11-04): «Sin médico» is a gap only where patients book;
// a walk-in service never gets a doctor, so its cell stays empty and its form asks nothing.
test('Servicios: «Sin médico» sólo en servicios con turno; los sin turno dejan la celda vacía y su ficha no pide asignar', async () => {
  const vm = await import('node:vm');
  const take = (name) => src.match(new RegExp(`  function ${name}\\([^]*?\\n  \\}\\n`))[0];
  const cell = (it) => vm.runInNewContext(take('servicioSinAgenda') + take('medicosCellHtml') + 'medicosCellHtml(IT);', {
    IT: it, esc: (s) => String(s), T: (_k, _v, fallback) => fallback, puede: () => true,
    itemHref: (tipo, id) => '/admin/' + tipo + '?item=' + id,
  });
  assert.match(cell({ modo: 'solicitud', _medicos: [] }), /<strong class="cnt-no-doctor">Sin médico<\/strong>/);
  assert.match(cell({ modo: 'solicitud_con_orden', _medicos: [] }), /Sin médico/);
  assert.match(cell({ modo: 'walkin', apertura_mismo_dia: true, _medicos: [] }), /Sin médico/, 'same-day opening offers slots');
  assert.equal(cell({ modo: 'walkin', _medicos: [] }), '');
  assert.equal(cell({ modo: 'walkin_con_orden' }), '');
  assert.match(cell({ modo: 'walkin', _medicos: [{ id: 'g', nombre: 'Gómez' }] }), /<a href="\/admin\/medicos\?item=g">Gómez<\/a>/);
  assert.match(src, /if \(\(it\.id \|\| it\.slug\) && \(svcMedicos\.length \|\| !servicioSinAgenda\(it\)\)\)/);
  assert.equal(contentEs['admin.content.catalog.services.no_doctor_hint'], 'Se asigna en la agenda de cada médico, en {medicos}.');
  assert.match(contentEn['admin.content.catalog.services.no_doctor_hint'], /\{medicos\}/);
  assert.match(directCss, /\.cnt-row\.cnt-row-static>label\{padding-top:0;\}/);
});

// bf89cd48 gave the field hints one dark-gray token; a fix applies everywhere: the
// schedule preview under «Horario» and the Ajustes card hints take it too (2026-11-04).
test('every panel form hint reads in the dark hint token', () => {
  const settings = fs.readFileSync(new URL('../static/platform/admin-settings.mjs', import.meta.url), 'utf8');
  assert.match(directCss, /\.cnt-hint\{[^}]*color:var\(--hint-ink,/);
  assert.match(directCss, /\.cnt-preview\{[^}]*color:var\(--hint-ink,/);
  assert.match(settings, /\.set-card \.set-lede\{[^}]*color:var\(--hint-ink,/);
  assert.match(horas, /\.hor-limit-copy p\{[^}]*color:var\(--hint-ink,/);
});

test('los links de las tablas del catálogo son verdes y sin subrayado', () => {
  assert.match(src, /\.cnt-table a\{color:var\(--accent\);text-decoration:none;\}/);
  assert.match(src, /\.cnt-table a:hover\{color:var\(--accent-hover\);text-decoration:none;\}/);
});

test('los formularios del catálogo usan controles grandes, campos proporcionados y etiquetas asociadas', () => {
  assert.match(directCss, /\.cnt-row\{[^}]*align-items:start/);
  assert.match(directCss, /\.cnt-row>label\{text-align:left;font-size:16px/);
  assert.match(directCss, /\.cnt-in,\.cnt-row \.adm-sel\{[^}]*font-size:20px[^}]*min-height:48px/);
  assert.match(directCss, /\.cnt-row \.cnt-in,\.cnt-row \.adm-sel\{max-width:100%;box-sizing:border-box;\}/);
  assert.match(directCss, /\.adm-search\{[^}]*min-height:40px[^}]*font-size:16px/);
  assert.match(directCss, /\.adm-sel\{[^}]*font-size:16px[^}]*min-height:40px/);
  assert.match(src, /@media\(max-width:1024px\)\{\.cnt-toolbar-filters\{display:grid;grid-template-columns:repeat\(2,minmax\(0,1fr\)\);width:100%;\}/);
  // Guardar is the panel's one button (2026-11-08, R4): the shared height,
  // 44px on touch, never the 48px of the fields above it, and never a 20px
  // type of its own (audit 2026-11-05).
  assert.match(directCss, /\.cnt-actions \.btn,\.cnt-actions-danger \.btn\{min-height:var\(--adm-btn-h,36px\);\}/);
  assert.doesNotMatch(directCss, /\.cnt-actions(-danger)? \.btn\{[^}]*font-size/);
  assert.match(directCss, /\.cnt-in,\.cnt-row \.adm-sel\{font-size:18px;\}/);
  assert.match(directCss, /\.cnt-actions\{align-items:center;justify-content:flex-start;flex-direction:row;flex-wrap:nowrap;\}/);
  assert.match(directCss, /\.adm-h1\{white-space:normal;overflow:visible;text-overflow:clip;overflow-wrap:anywhere;line-height:1\.25;\}/);
  assert.match(src, /\.cnt-h1-sub\{font-size:16px[^}]*overflow-wrap:anywhere/);
  assert.match(directCss, /\.cnt-field-email\{width:min\(36ch,100%\);\}/);
  assert.match(directCss, /\.cnt-field-specialty\{width:min\(30ch,100%\);max-width:100%;\}/);
  assert.match(src, /var inferred = control && !hidden && control\[0\]\.match/);
  assert.match(src, /var targetId = controlId \|\| \(inferred && inferred\[1\]\)/);
  assert.match(src, /forAttr \+ '>' \+ labelHtml/);
  assert.match(src, /id="med-email" type="email"[^']*class="cnt-in cnt-field-email"|class="cnt-in cnt-field-email" id="med-email"/);
  assert.match(src, /class="adm-sel cnt-field-specialty" id="med-especialidad"/);
  assert.match(src, /id="cen-email" type="email" autocomplete="email"/);
});


test('the approved hours controls use compact inputs and day chips while wrapping labelled fields', () => {
  assert.match(horas, /\.hor-in\{min-height:40px[^}]*font-size:16px/);
  assert.match(horas, /\.hor-sel\{min-height:40px[^}]*font-size:16px/);
  assert.match(horas, /\.cnt-franja-row \.cmb-in\{[^}]*min-height:40px[^}]*font-size:16px/);
  // .hor-add is the panel's secondary button (tests/admin-button-system.test.mjs).
  assert.doesNotMatch(horas, /\.hor-add\{/);
  assert.match(horas, /\.hor-x\{width:48px;min-height:48px/);
  assert.match(horas, /\.hor-day span\{[^}]*width:44px;height:40px[^}]*font-size:15px/);
  assert.match(horas, /@media\(max-width:1800px\)[\s\S]*?\.hor-cols,\.hor-exc-cols\{display:none;\}[\s\S]*?content:attr\(data-l\);font-size:16px/);
  assert.match(horas, /@media\(max-width:720px\)[\s\S]*?font-size:16px[\s\S]*?flex:1 1 100%/);
  assert.match(horas, /\.cnt-franja-row \.hor-c:first-child\{flex-direction:column;align-items:flex-start;gap:6px;\}/);
  assert.match(horas, /\.hor-range \.hor-in\{width:auto!important;\}/);
  assert.match(directCss, /\.cnt-centro-pair \.adm-sel\{width:100%;min-height:48px;font-size:20px;\}/);
  assert.match(directCss, /\.cnt-cupos label\{[^}]*min-height:44px;font-size:16px/);
});

test('shared account photo and user fields use the catalog control contract', () => {
  assert.doesNotMatch(account, /\.cnt-foto-actions\{display:flex/);
  // Cuenta's photo is a labelled button beside the portrait: no bare file
  // input, no small-print hint; the 5 MB guard still answers in place.
  assert.match(account, /<label class="btn btn-quiet btn-sm" for="mifoto-foto-input" id="mifoto-foto-pick">/);
  assert.doesNotMatch(account, /Se sube al elegirla/);
  assert.match(account, /file\.size > 5 \* 1024 \* 1024/);
  assert.match(users, /class="cnt-in cnt-field-email" id="usr-email"/);
  assert.match(users, /row\(T\("admin\.system\.users\.col\.email"[\s\S]*?"usr-email"\)/);
});

test('photo upload stays compact and cancelling a chooser leaves the existing image unchanged', () => {
  // Opus UI review 2026-11-03: the catalog photo copies «Mi cuenta» — one row
  // of buttons, the native «Choose File / No file chosen» hidden behind its
  // labelled button, «Quitar foto» a real button, no hint lines.
  assert.match(directCss, /\.cnt-foto-actions\{display:flex;flex-wrap:wrap;/);
  assert.match(directCss, /\.cnt-foto-file\{position:absolute;width:1px;height:1px;opacity:0;/);
  assert.match(src, /<label class="btn btn-quiet btn-sm" for="' \+ prefix \+ '-foto-input" id="' \+ prefix \+ '-foto-pick">/);
  assert.match(src, /id="' \+ prefix \+ '-foto-quitar" class="btn btn-quiet btn-sm"/);
  assert.doesNotMatch(src, /photo_hint_(new|existing)|foto-quitar-hint|cnt-foto-portrait/);
  const existingUpload = src.match(/listen\(input, "change", function \(\) \{[\s\S]*?if \(!file\) return;[\s\S]*?reader\.readAsDataURL\(file\);[\s\S]*?\n    \}\);\n\n    listen\(quitarA/);
  assert.ok(existingUpload, 'existing photo upload must keep state when no file is chosen');
});

test('active appointment limits say what they affect across service, doctor, and site settings', () => {
  assert.equal(contentEs['admin.content.catalog.common.field_appointments_per_patient'], 'Máximo de reservas web por paciente');
  assert.match(contentEs['admin.content.catalog.services.appointments_max_hint'], /solicitudes pendientes y turnos futuros del mismo servicio/);
  assert.match(contentEs['admin.content.catalog.services.appointments_max_hint'], /No es un límite por horario/);
  assert.match(contentEs['admin.content.catalog.doctors.appointments_max_hint'], /Vacío: usar el máximo del servicio/);
  assert.match(systemEs['admin.system.settings.turnos.lede'], /No es un límite por horario/);
  assert.equal(contentEn['admin.content.catalog.common.field_appointments_per_patient'], 'Maximum online bookings per patient');
  assert.match(contentEn['admin.content.catalog.doctors.appointments_max_hint'], /pending requests and future appointments for the same service/);
  assert.match(systemEn['admin.system.settings.turnos.lede'], /not a per-time-slot limit/);
  assert.doesNotMatch(src, /"Turnos por paciente":/);
});

test('los catálogos reservan lugar visible para Creado y Actualizado', () => {
  assert.match(src, /cnt-table--catalog cnt-table--" \+ tipo/);
  assert.match(src, /\.cnt-table--catalog\{table-layout:fixed;min-width:760px;\}/);
  // The doctors' rule names the LIST table: the Calendario day table in the
  // same section restacks on its own (pinamar-turnos 04.11).
  assert.match(src, /@media\(max-width:1024px\)\{[\s\S]*?#sec-servicios \.cnt-table,#sec-centros \.cnt-table,#sec-medicos \.cnt-table--medicos\{display:block;width:100%!important;min-width:0!important;table-layout:auto!important;\}/);
  assert.match(src, /data-col="' \+ esc\(c\.key\) \+ '"/);
  assert.match(src, /\.cnt-table--servicios \[data-col=created\],\.cnt-table--servicios \[data-col=updated\]\{width:10%;\}/);
  assert.match(src, /#sec-medicos \.cnt-table th\[data-col\]\{white-space:normal;overflow:visible;text-overflow:clip;overflow-wrap:anywhere;\}/);
  assert.match(src, /\.cnt-table--servicios td\[data-col=nombre\],\.cnt-table--servicios td\[data-col=centro\],\.cnt-table--servicios td\[data-col=modo\],\.cnt-table--servicios td\[data-col=horario\],\.cnt-table--servicios td\[data-col=medicos\],\.cnt-table--centros td\[data-col=nombre\],\.cnt-table--centros td\[data-col=address\],#sec-medicos \.cnt-table td\[data-col=nombre\],#sec-medicos \.cnt-table td\[data-col=especialidad\],#sec-medicos \.cnt-table td\[data-col=centro\]\{white-space:normal;overflow:visible;text-overflow:clip;overflow-wrap:anywhere;\}/);
  assert.match(src, /\.cnt-table--servicios td\[data-col=nombre\] a,\.cnt-table--servicios td\[data-col=centro\] a,\.cnt-table--servicios td\[data-col=medicos\] a,\.cnt-table--centros td\[data-col=nombre\] a,#sec-medicos \.cnt-table td\[data-col=especialidad\] \.cnt-inline-action\{white-space:normal;overflow:visible;text-overflow:clip;text-align:left;\}/);
  assert.match(src, /td\[data-col=created\]:before[^{]*\{content:attr\(data-label\) \\" \\";/);
  assert.match(src, /#sec-medicos \.cnt-th-check,#sec-medicos \.cnt-td-check\{width:auto;\}/);
  assert.match(src, /class="cnt-td-check" data-label=/);
  assert.match(src, /class="cnt-td-acts" data-label=/);
});

test('doctor occupancy is the number «52%», blank for zero and no data', async () => {
  // ui-expert 2026-11-15: the five-square bar is gone, the column is the
  // percentage through the panel's formatter, right-aligned (numeric).
  // Only a doctor with turnos gets a value: 0 and «no capacity» are blank
  // cells, so an all-zero column is empty and the table engine drops it.
  const I18n = await import('../static/platform/i18n.mjs');
  const cell = src.match(/function occupancyCell\(v\) \{[\s\S]*?\n  \}/);
  assert.ok(cell, 'occupancyCell no encontrada');
  const occupancyCell = new Function('T', 'esc', 'I18n', `${cell[0]}; return occupancyCell;`)((k, v, f) => f, String, I18n);
  assert.equal(occupancyCell(0), '');
  assert.equal(occupancyCell(null), '');
  assert.match(occupancyCell(52), />52%</);
  assert.match(occupancyCell(4.25), />4,3%</);
  assert.doesNotMatch(occupancyCell(52), /med-occupancy/);
  assert.match(src, /key: "ocupacion_14d"[^\n]*numeric: true/);
  assert.doesNotMatch(src, /function occupancySegments/);
  assert.doesNotMatch(src, /key: "turnos_14d"/);
});

test('catalog numeric metrics align by registry class, without treating digit-shaped data as numbers', () => {
  assert.match(src, /c\.numeric \? " cnt-numeric"/);
  assert.match(directCss, /\.cnt-table th\.cnt-numeric,\.cnt-table td\.cnt-numeric\{text-align:right;\}/);
  assert.doesNotMatch(directCss, /td\[data-col\*=.*number/);
});

test('blank trailing actions and resizers only appear where a row action or resizable column exists', () => {
  assert.match(src, /var hasRowActions = puedeAltaBaja\(tipo\) && visible\.some/);
  assert.match(src, /if \(hasRowActions\) theadHtml \+= '<th class="cnt-th cnt-th-acts"><\/th>';/);
  assert.match(src, /if \(hasRowActions\) rowsHtml \+= '<td class="cnt-td-acts"/);
  assert.match(src, /resizable: key !== "seleccion" && key !== "acciones"/);
});

test('doctor selection and form actions preserve their visual roles at narrow widths', () => {
  assert.match(directCss, /\.cnt-th-check::before,\.cnt-th-check::after,\.cnt-td-check::before,\.cnt-td-check::after\{content:none!important;display:none!important;\}/);
  assert.match(directCss, /\.cnt-actions-danger \.btn\{min-height:var\(--adm-btn-h,36px\);\}/);
  assert.doesNotMatch(directCss, /\.cnt-actions>\.btn:not\(\.btn-ok\)/);
  assert.match(directCss, /\.cnt-actions\{align-items:center;justify-content:flex-start;flex-direction:row;flex-wrap:nowrap;\}/);
  assert.match(horas, /\.hor-c--num\{text-align:left;\}/);
});

test('horarios idénticos se compactan sin mezclar cupos distintos', () => {
  const match = src.match(/function compactarFranjas\(franjas\) \{[\s\S]*?\n  \}\n\n  \/\/ resumen/);
  assert.ok(match, 'compactarFranjas no encontrada');
  const fnSource = match[0].replace(/\n\n  \/\/ resumen[\s\S]*$/, '');
  const compactarFranjas = new Function(`${fnSource}; return compactarFranjas;`)();

  const merged = compactarFranjas([
    {dias:[1], desde:'09:00', hasta:'12:00', cada_min:20, cupos:{web:1}},
    {dias:[3], desde:'09:00', hasta:'12:00', cada_min:20, cupos:{web:1}},
    {dias:[5], desde:'09:00', hasta:'12:00', cada_min:20, cupos:{web:2}},
  ]);
  assert.equal(merged.length, 2);
  assert.deepEqual(merged[0].dias, [1,3]);
  assert.deepEqual(merged[1].dias, [5]);
  assert.equal(compactarFranjas([
    {dias:[1], cupos:{overbooking_percent:20}},
    {dias:[2], cupos:{overbooking_percent:30}},
    {dias:[3], cupos:{overbooking_percent:0}},
    {dias:[4], cupos:{}},
  ]).length, 4, 'different percentage and legacy policies must stay separate');
  assert.match(src, /MED_STATE\.centros\.forEach\(function \(c\) \{ c\.franjas = compactarFranjas/);
});

test('consultorio común no se repite en resúmenes ni tablas del panel', () => {
  assert.match(src, /var mostrarServicio = servicios\.length > 1/);
  assert.match(src, /var conConsultorio = consultorios\.length > 1/);
  assert.doesNotMatch(src, /var conConsultorio = turnos\.some/);
});

test('la fecha seleccionada del calendario usa un estado sólido', () => {
  assert.match(calendarCss, /\.medcal-dia\.is-selected\{background:var\(--accent\);color:#fff;[^}]*0 0 0 4px var\(--accent\)/);
});

test('el cambio de admin-content invalida la caché sin tocar gráficos', () => {
  assert.match(contentScreen, /"sana\/calendar-grid": "\{\{ static\('platform\/calendar-grid\.mjs'\) \}\}"/);
  assert.doesNotMatch(contentScreen, /static\('calendar-grid\.js'\)/);
  assert.match(contentScreen, /static\('platform\/admin-content\.mjs'\)/);
  assert.match(reportesScreen, /import\("\{\{ static\('platform\/admin-reports\.mjs'\) \}\}"\)/);
  assert.doesNotMatch(reportesScreen, /static\('admin-reports\.js'\)/);
  assert.doesNotMatch(reportesScreen, /static\('admin-charts\.js'\)/);
});

test('una pestaña vacía conserva fondo transparente y el color de las demás pestañas', () => {
  // Sin color propio: inactiva va en --hint-ink como toda pestaña y, abierta,
  // en --text de .cnt-tab.on (antes el gris translúcido le ganaba también a .on).
  assert.match(src, /\.cnt-tab\.is-empty\{background:transparent;\}/);
  assert.doesNotMatch(src, /\.cnt-tab\.is-empty\{[^}]*background:var\(--bg-2\)/);
});

test('the doctor tabs stay on one line, scroll sideways and bring the chosen one into view', () => {
  const tabs = src.match(/\.cnt-tabs\{[^}]*\}/)[0];
  assert.match(tabs, /flex-wrap:nowrap/);
  assert.match(tabs, /overflow-x:auto/);
  const tab = src.match(/\.cnt-tab\{[^}]*\}/)[0];
  assert.match(tab, /flex:0 0 auto/);
  assert.match(tab, /white-space:nowrap/);
  assert.match(tab, /background:none/);
  assert.match(src, /tab\.scrollIntoView\(\{ block: "nearest", inline: "nearest" \}\)/);
});

test('three or more days in a row read as a span, anything else as a list', () => {
  const match = src.match(/function franjaResumen\(fr, mostrarServicio\) \{[\s\S]*?\n  \}\n/);
  assert.ok(match, 'franjaResumen not found');
  const DIAS_UI = [['Lu', 1], ['Ma', 2], ['Mi', 3], ['Ju', 4], ['Vi', 5], ['Sá', 6], ['Do', 0]].map(([l, v]) => ({ l, v }));
  const T = (key, vars, fallback) => fallback.replace(/\{(\w+)\}/g, (_, k) => vars[k]);
  const franjaResumen = new Function('DIAS_UI', 'T', 'servicioNombre', `${match[0]}; return franjaResumen;`)(DIAS_UI, T, (s) => s);
  assert.equal(franjaResumen({ dias: [1, 2, 3, 4, 5], desde: '08:00', hasta: '12:00' }), 'Lu a Vi 08:00–12:00');
  assert.equal(franjaResumen({ dias: [6, 0, 5], desde: '08:00', hasta: '12:00' }), 'Vi a Do 08:00–12:00');
  assert.equal(franjaResumen({ dias: [1, 3, 5], desde: '08:00', hasta: '12:00' }), 'Lu, Mi y Vi 08:00–12:00');
  assert.equal(franjaResumen({ dias: [1, 2], desde: '08:00', hasta: '12:00' }), 'Lu y Ma 08:00–12:00');
  // A repeated day (a legacy single agenda) is not a span over a day off.
  assert.equal(franjaResumen({ dias: [1, 1, 3], desde: '08:00', hasta: '12:00' }), 'Lu y Mi 08:00–12:00');
  assert.equal(franjaResumen({ dias: [4], desde: '08:00', hasta: '12:00' }), 'Ju 08:00–12:00');
  for (const lang of ['es', 'en']) {
    const doctor = JSON.parse(fs.readFileSync(new URL(`../locales/${lang}/admin.doctor.json`, import.meta.url), 'utf8'));
    assert.match(doctor['admin.doctor.days_span'], /\{first\}.+\{last\}/);
  }
});

test('a general specialty named like the specialty itself is not written under it', () => {
  const match = src.match(/function clusterChipHtml\(clusterId, especialidad\) \{[\s\S]*?\n  \}\n/);
  assert.ok(match, 'clusterChipHtml not found');
  const T = (key, vars, fallback) => fallback;
  const chip = new Function('T', 'CLUSTERS_NOMBRE', 'esc', `${match[0]}; return clusterChipHtml;`)(T, { ped: 'Pediatría', cli: 'Clínica médica' }, (s) => s);
  assert.equal(chip('ped', 'pediatría '), '');
  assert.equal(chip('cli', 'Cardiología'), 'Especialidad general: <b>Clínica médica</b>');
  assert.match(chip('', ''), /Elegí una especialidad/);
  assert.equal((src.match(/clusterChipHtml\(/g) || []).length, 5, 'every caller passes the specialty');
  assert.doesNotMatch(src, /clusterChipHtml\([^,()]*(\([^()]*\))?[^,()]*\)[ ;:+]/);
  assert.match(directCss, /\.cnt-hint:empty\{display:none;\}/);
});

test('an existing service keeps «Configuración interna» folded, a new one open, and an error opens it', () => {
  assert.match(src, /<details class="svc-field-group cnt-fold" id="svc-internal"' \+ \(it\.id \|\| it\.slug \? "" : " open"\) \+ '><summary><h2>/);
  const form = src.slice(src.indexOf('function formServicio('), src.indexOf('function collectServicio('));
  const folded = form.slice(form.indexOf('id="svc-internal"'));
  for (const id of ['svc-categoria', 'svc-cluster', 'svc-edad-min', 'svc-limits-wrap', 'svc-palabras', 'svc-featured', 'svc-top-label']) {
    assert.ok(folded.includes('id="' + id + '"'), id + ' stays in the folded group');
  }
  for (const id of ['svc-nombre', 'svc-centro', 'svc-horario', 'svc-cartel', 'svc-announcement-active']) {
    assert.ok(!folded.includes('id="' + id + '"'), id + ' stays in sight');
  }
  assert.match(folded, /return html \+ "<\/details>";/);
  assert.match(src, /\.cnt-fold>summary\{cursor:pointer;min-height:44px;/);
  const reveal = src.slice(src.indexOf('function revealFieldErrorControl('), src.indexOf('function revealFieldErrorControl(') + 900);
  assert.match(reveal, /var fold = control\.closest \? control\.closest\("details"\) : null;\s+if \(fold && !fold\.open\) fold\.open = true;\s+control\.scrollIntoView/);
});

// The centers' «Teléfono» shows only call/WhatsApp icons; a column judged
// empty by its words alone disappeared from the demo list (2026-11-15).
test('a cell with a named icon and no words is not empty, so its column stays', () => {
  const match = src.match(/function cellEmpty\(tipo, col, item\) \{[\s\S]*?\n  \}\n/);
  assert.ok(match, 'cellEmpty not found');
  const cellEmpty = new Function('renderCell', `${match[0]}; return cellEmpty;`)((t, c, it) => it.html);
  const empty = (html) => cellEmpty('centros', {}, { html });
  assert.equal(empty('<a class="cnt-ico-link" href="tel:+54" aria-label="(011) 4000-0005"><span class="cnt-ico" aria-hidden="true"></span></a>'), false);
  assert.equal(empty('<span class="med-occ" role="img" aria-label="40 %"><i></i></span>'), false);
  assert.equal(empty('<img src="a.png" alt="">'), false);
  assert.equal(empty('Lunes'), false);
  assert.equal(empty(''), true);
  assert.equal(empty('<span aria-label=""></span>'), true);
  assert.equal(empty('<span class="cnt-muted"> </span>'), true);
});

test('a center folds its rarely filled location and HSI fields, open only when one is set', () => {
  const form = src.slice(src.indexOf('function formCentro('), src.indexOf('function collectCentro('));
  assert.match(form, /var conDetalle = !!\(it\.location_note \|\| it\.location_until \|\| it\.locality \|\| \(it\.lat != null && it\.lng != null\) \|\| it\.hsi_institution\);/);
  assert.match(form, /<details class="cnt-fold" id="cen-more"' \+ \(conDetalle \? " open" : ""\) \+ '><summary><h2>/);
  const folded = form.slice(form.indexOf('id="cen-more"'));
  for (const id of ['cen-nota-ubi', 'cen-hasta', 'cen-zona', 'cen-coords', 'cen-hsi']) {
    assert.ok(folded.includes('id="' + id + '"'), id + ' is folded');
  }
  for (const id of ['cen-nombre', 'cen-direccion', 'cen-telefono', 'cen-jobs', 'cen-maps', 'cen-bookable']) {
    assert.ok(!folded.includes('id="' + id + '"'), id + ' stays in sight');
  }
  assert.match(folded, /return html \+ "<\/details>";/);
  assert.equal(contentEs['admin.content.catalog.centers.group_more'], 'Ubicación exacta e HSI');
  assert.ok(contentEn['admin.content.catalog.centers.group_more']);
  // no jargon in a label, and no empty grey circle where there is no photo
  assert.doesNotMatch(src, /JTBD/);
  assert.equal(contentEs['admin.content.catalog.centers.field_jtbd'], 'Para qué se usa');
  assert.doesNotMatch(contentEn['admin.content.catalog.centers.field_jtbd'], /JTBD/);
  assert.match(directCss, /\.cnt-foto-av:empty:not\(\[style\]\)\{display:none;\}/);
});

test('cada médico existente tiene una pestaña de captura de síntomas', () => {
  // El label puede venir directo o envuelto en T(key, vars, "Síntomas") (i18n).
  assert.match(src, /tab\("intake", (?:T\([^)]*?, )?"Síntomas"\)/);
  assert.match(src, /id="med-intake-panel"/);
  assert.match(src, /\/api\/admin\/intake\/settings/);
  assert.match(src, /data-med-intake-enabled/);
  assert.match(src, /Cartel imprimible con QR/);
  assert.doesNotMatch(src, /Columnas en Pacientes de hoy/);
  assert.doesNotMatch(src, /data-med-intake-column/);
});

test('la ficha del médico pide otro permiso sólo a quien lo tiene, nunca por nombre de rol (K8)', () => {
  const puede = /var puede = function \(perm\) \{[\s\S]*?\n  \};/.exec(src)?.[0];
  assert.ok(puede, 'helper puede(perm) sobre CURRENT_PERMISSIONS');
  const run = (perms, role) => new Function('window', 'esAdmin', `${puede}; return puede;`)(
    { CURRENT_PERMISSIONS: perms }, () => role === 'admin');
  const gestor = ['solicitudes', 'cola', 'crear_turnos', 'pacientes', 'medicos'];
  for (const perm of ['usuarios', 'asistente', 'servicios', 'configuracion']) assert.equal(run(gestor)(perm), false, perm);
  assert.equal(run(gestor)('medicos'), true);
  assert.equal(run(undefined, 'admin')('usuarios'), true, 'sin lista cae al criterio anterior');
  assert.match(src, /&& puede\("asistente"\)\) html \+= tab\("intake"/);
  assert.match(src, /&& puede\("usuarios"\)\) html \+= tab\("usuario"/);
  assert.match(src, /&& puede\("asistente"\)\) html \+= panel\("intake"/);
  assert.match(src, /&& puede\("usuarios"\)\) html \+= panel\("usuario"/);
  assert.match(src, /if \(!puede\("usuarios"\)\) return Promise\.resolve\(MED_ACCOUNTS = \{\}\);/);
  assert.doesNotMatch(src, /tipo === "medicos" && esAdmin\(\)/);
});

test('Síntomas es la penúltima pestaña del médico', () => {
  const agenda = src.indexOf('tab("agenda", ');
  const calendario = src.indexOf('tab("calendario", ');
  const info = src.indexOf('tab("info", ');
  const intake = src.indexOf('tab("intake", ');
  const usuario = src.indexOf('tab("usuario", ');
  assert.ok(agenda < calendario && calendario < info && info < intake && intake < usuario);
});

test('las acciones masivas de médicos agrupan la captura de síntomas, incluido apagarla', () => {
  assert.match(src, /data-med-bulk-select/);
  assert.match(src, /data-med-bulk-apply/);
  // El label puede venir directo o envuelto en T(key, vars, "Activar captura de síntomas") (i18n).
  assert.match(src, /value="enable-intake">[\s\S]{0,120}Activar captura de síntomas/);
  assert.match(src, /value="disable-intake">[\s\S]{0,120}Desactivar captura de síntomas/);
  assert.match(src, /action === "enable-intake" \|\| action === "disable-intake"/);
  assert.match(src, /medico_ids: ids, enabled: action === "enable-intake"/);
  assert.doesNotMatch(src, /<button[^>]+data-med-action="invite"/);
});

test('la selección de médicos admite rango con Shift y no confunde habilitar con restaurar el original', () => {
  assert.match(src, /var MED_LAST_SELECTED = null/);
  assert.match(src, /cb\._medShift = !!event\.shiftKey/);
  assert.match(src, /MED_SELECTION_VIEW !== viewSignature/);
  assert.match(src, /indexOf\(MED_LAST_SELECTED\)/);
  assert.match(src, /data-restaurar-original=/);
  assert.match(src, /it\._origen === "json\+db"/);
});

test('una búsqueda o filtro sin resultados ofrece limpiar la vista, sin crear registros fantasma', () => {
  assert.match(src, /data-clear-catalog/);
  assert.match(src, /SEARCH\[tipo\] = ""/);
  assert.match(src, /MED_FILTERS = \{ centro: "", especialidad: "", agenda: "" \}/);
  assert.match(src, /SERVICE_FILTERS = \{ centro: "", modo: "" \}/);
});

test('la captura de síntomas sólo muestra su PDF cuando está activa y disponible', () => {
  assert.match(src, /medico\.pdf_url \|\| \(medico\.cartel_url \? medico\.cartel_url \+ "\?format=pdf"/);
  assert.doesNotMatch(src, /data-med-intake-status/);
  assert.doesNotMatch(src, /intake_activate_qr_hint/);
});

test('el servicio permite editar su descripción y una aclaración del horario', () => {
  assert.match(src, /id="svc-descripcion"/);
  assert.match(src, /doc\.descripcion = descripcion/);
  assert.match(src, /id="svc-horario-nota"/);
  assert.match(src, /doc\.horario_nota = horarioNota/);
});

test('los campos opcionales del catálogo dicen «opcional», nunca un ejemplo que se lea como valor', () => {
  // A gray «PAP», «Más solicitado» or «rodillas, huesos, columna» read on a
  // screenshot as the entity's own setting (2026-11-10).
  const tagOf = (id) => {
    const at = src.indexOf('id="' + id + '"');
    assert.ok(at > 0, id);
    return src.slice(at, src.indexOf(">", at));
  };
  for (const id of ["svc-palabras", "svc-palabras-exc", "svc-top-label", "med-palabras", "cen-jobs"]) {
    assert.match(tagOf(id), /' \+ opcionalAttr\(\) \+ '$/, id);
  }
  assert.match(src, /function opcionalAttr\(\) \{\n    return ' placeholder="' \+ esc\(T\("admin\.content\.catalog\.common\.optional_placeholder", null, "opcional"\)\) \+ '"';/);
  // the email is required where staff log in through the portal: no gray text at all
  for (const id of ["med-email", "med-inv-email", "svc-horario"]) {
    assert.doesNotMatch(tagOf(id), /placeholder|opcional/, id);
  }
  for (const sample of ['placeholder="PAP"', "Más solicitado", "rodillas, huesos, columna", "análisis de sangre, laboratorio, extracción", "ej. Vacunarte"]) {
    assert.ok(!src.includes(sample), sample);
  }
  // the schedule's format lives in the guide under the field, not in a placeholder
  assert.match(src, /id="svc-horario" rows="2">/);
});

test('Horario guía primero y valida recién al salir del campo', () => {
  assert.match(src, /Escribí cuándo atiende\. Ejemplo: Lunes a viernes de 8 a 20 h\./);
  assert.match(src, /listen\(input, "blur"/);
  assert.match(src, /mostrarError = !!input\.value\.trim\(\)/);
  assert.doesNotMatch(src, /No puedo interpretar este horario/);
});

test('menú compacto usa el disclosure nativo y enlaces tocables de la shell directa', () => {
  // The retired hash-shell drawer/scrim is not a product surface.  Direct
  // pages use the native disclosure rendered by base.html and the shared
  // direct stylesheet keeps its summary and links usable on touch devices.
  assert.match(shell, /<details class="adm5-menu" open>/);
  assert.match(directCss, /\.adm5-menu>summary\{display:flex;[^}]*min-width:44px;min-height:44px/);
  assert.match(directCss, /\.adm5-menu:not\(\[open\]\)>.adm5-nav\{display:none;\}/);
  assert.match(directCss, /\.adm5-nav a\{min-height:44px/);
  assert.doesNotMatch(shell, /adm-menu-scrim|adm-drawer-logo/);
});

// Demo check 07.11: a doctor's five screens hid behind «Menú» on a phone. A
// doctor gets a row of tabs at compact width (the disclosure stays open), the
// person icon from the project's Icons8 pack stands for Cuenta; every other
// role keeps the disclosure. Geometry: tests/e2e/admin-doctor-tabs.spec.mjs.
test('a doctor gets a row of tabs instead of «Menú» at compact width', () => {
  assert.match(shell, /\{% set nav_tabs = screen_rol\(\) == 'medico' %\}/);
  assert.match(shell, /<aside class="adm5-side\{% if nav_tabs %\} adm5-side--tabs\{% endif %\}"/);
  assert.match(shell, /if \(compact\.matches && !tabs\) menu\.removeAttribute\("open"\);/);
  assert.match(directCss, /\.adm5-side--tabs \.adm5-menu>summary\{display:none;\}/);
  assert.match(directCss, /\.adm5-side--tabs \.adm5-menu\[open\]>\.adm5-nav\{[^}]*flex-wrap:nowrap;[^}]*overflow-x:auto;/);
  assert.match(directCss, /\.adm5-side--tabs \.adm5-nav a\.on\{[^}]*color:var\(--text\);font-weight:650;/);
  assert.match(directCss, /mask:url\(\/static\/icons\/account-sf-regular\.svg\)/);
  assert.ok(fs.existsSync(new URL('../static/icons/account-sf-regular.svg', import.meta.url)));
});

test('admin branding is text-only, including the sign-in gate', () => {
  assert.match(loginScreen, /<span class="adm-brand">\{\{ t\('admin\.shell\.gate\.brand_pre', 'Admin de '\) \}\}<b>\{\{ t\('admin\.shell\.gate\.brand_bold', 'Turnos'\) \}\}<\/b><\/span>/);
  assert.doesNotMatch(loginScreen, /logo-admin\.png/);
  assert.doesNotMatch(loginScreen, /class="stz-mark"/);
});

// ---------------------------------------------------------------------------
// Reunión hospital 2026-07-24: tres perillas independientes + servicio por
// franja. Guardas contra volver al menú de 4 modos o perder el combo.
// ---------------------------------------------------------------------------

test('el modo del servicio son TRES checkboxes independientes, no un menú de 4', () => {
  assert.match(src, /"svc-c-turno"/);
  assert.match(src, /"svc-c-orden"/);
  assert.match(src, /"svc-c-aprobacion"/);
  assert.match(src, /<input type="checkbox" id="' \+ p\[0\] \+ '"/);
  // los radios del menú viejo no vuelven
  assert.doesNotMatch(src, /name="svc-modo"/);
  assert.doesNotMatch(src, /id="svc-auto-confirmar"/);
});

test('modo y auto_confirmar se derivan de las perillas, en un solo lugar', () => {
  assert.match(src, /function modoActual\(sec\)/);
  assert.match(src, /conTurno \? \(conOrden \? "solicitud_con_orden" : "solicitud"\)\s*\n?\s*: \(conOrden \? "walkin_con_orden" : "walkin"\)/);
  assert.match(src, /doc\.modo = modoActual\(sec\)/);
  // el catálogo guarda la EXCEPCIÓN (auto_confirmar), o sea la perilla invertida
  assert.match(src, /aprueba && !aprueba\.checked/);
  assert.match(src, /doc\.auto_confirmar = true/);
});

test('unavailable service properties are hidden and excluded from submission', () => {
  assert.match(src, /apagar\(aprob, !conTurno\)/);
  assert.match(src, /cnt-modo-op--off/);
  assert.match(src, /lbl\.style\.display = off \? "none" : ""/);
  assert.match(directCss, /\.cnt-modo-op--off\{opacity/);
  // y al revés: con turno, "abre el mismo día" no significa nada
  assert.match(src, /apagar\(mismo, conTurno\)/);
  // la agenda cuelga de la perilla de turno o de la apertura el mismo día
  assert.match(src, /var reserva = conTurno \|\| \(mismo && mismo\.checked && !mismo\.disabled\);\s*wrap\.style\.display = reserva \? "" : "none";/);
  // the booking limits go with the schedule: a walk-in service has no booking to limit (2026-11-08 B3)
  assert.match(src, /if \(limites\) limites\.style\.display = reserva \? "" : "none";/);
  assert.match(src, /<div id="svc-limits-wrap" style="display:' \+ \(modo === "solicitud_con_orden" \|\| modo === "solicitud" \|\| it\.apertura_mismo_dia \? "" : "none"\)/);
});

test('un servicio por orden de llegada puede abrir su listado el mismo día', () => {
  // la cuarta perilla existe, se guarda como excepción y sólo en walk-in
  assert.match(src, /svc-c-mismodia/);
  assert.match(src, /doc\.apertura_mismo_dia = true; else delete doc\.apertura_mismo_dia/);
  assert.match(src, /doc\.modo === "walkin" \|\| doc\.modo === "walkin_con_orden"/);
});

test('cada franja del médico puede decir de qué servicio es', () => {
  assert.match(src, /var SVC_COMPAT = \[\]/);
  assert.match(src, /function franjaServicioHtml\(fr, centro, ci, fi\)/);
  // The editor resolves admin-combo.js through combo(), which fails visibly
  // when the dependency is absent rather than rendering a private copy.
  assert.match(src, /combo\(\)\.html\("fr-svc-sel"/);
  assert.doesNotMatch(src, /window\.comboHtml/);
  assert.match(src, /compatible-services\?especialidad=/);
  // se lee al guardar y viaja en la franja
  assert.match(src, /\$\("\.fr-svc-sel", wrap\)/);
  assert.match(src, /f\.servicio = sid; else delete f\.servicio/);
  // el servicio es UNA columna de la grilla, con su etiqueta escrita una sola
  // vez en el encabezado (antes: una etiqueta "Servicio" por franja). El
  // label puede venir directo o envuelto en T(key, vars, "Servicio") (i18n).
  assert.match(horas, /cell\(etqServicio, svc, "cnt-franja-svc"/);
  assert.match(horas, /"Servicio"/);
});

test('web booking is explicit and overbooking is an attached percentage opt-in', () => {
  assert.doesNotMatch(horas, /Públicos|Privados/);
  assert.doesNotMatch(src, /admin\.content\.catalog\.services\.slots_hint/);
  assert.doesNotMatch(horas, /admin\.content\.hours\.legend\.public_private/);
  assert.match(horas, /data-cupo-web aria-label=/);
  assert.match(horas, /type="hidden" data-cupo-reservado/);
  assert.match(horas, /type="hidden" data-cupo-sobreturnos/);
  // El control por franja se retiró: los cupos viajan como inputs ocultos y
  // la edición vive en el disclosure "Límites" del médico.
  assert.match(horas, /type="hidden" data-overbooking-enabled/);
  assert.match(horas, /type="hidden" data-overbooking-percent/);
  assert.match(horas, /data-limits-overbooking\b/);
  assert.match(horas, /data-limits-overbooking-percent/);
  assert.match(src, /data-preserved-overbooking-percent/);
  assert.deepEqual([
    contentEs['admin.content.hours.col.private'],
    contentEs['admin.content.hours.col.overbooking'],
  ], ['Solo personal', 'Pacientes extra por horario']);
  assert.deepEqual([
    contentEn['admin.content.hours.col.private'],
    contentEn['admin.content.hours.col.overbooking'],
  ], ['Staff only', 'Extra patients per time slot']);
  assert.equal(contentEs['admin.content.hours.col.public'], undefined);
  assert.equal(contentEn['admin.content.hours.col.public'], undefined);
  assert.equal(contentEs['admin.content.hours.legend.public_private'], undefined);
  assert.equal(contentEs['admin.content.catalog.services.slots_hint'], undefined);
});

test('web capacity serializes checkbox state and preserves legacy reserved counts', () => {
  const functionSource = src.match(/function collectCupos\(root\) \{[\s\S]*?\n  \}/)[0];
  const collect = new Function('$', functionSource + '; return collectCupos;')((selector, root) => root[selector]);
  const fields = {
    '[data-cupo-web]': {type: 'checkbox', checked: false, value: 'on'},
    '[data-cupo-reservado]': {type: 'hidden', value: '4'},
    '[data-cupo-sobreturnos]': {type: 'number', value: '2'},
  };
  assert.deepEqual(collect(fields), {web: 0, reservado: 4, sobreturnos: 2});
  fields['[data-cupo-web]'].checked = true;
  assert.deepEqual(collect(fields), {web: 1, reservado: 4, sobreturnos: 2});
  fields['[data-overbooking-enabled]'] = {type: 'hidden', value: '1'};
  fields['[data-overbooking-percent]'] = {type: 'hidden', value: '20'};
  assert.deepEqual(collect(fields), {web: 1, reservado: 4, sobreturnos: 2, overbooking_percent: 20});
  fields['[data-overbooking-percent]'].value = '35';
  assert.equal(collect(fields).overbooking_percent, 35);
  fields['[data-overbooking-enabled]'].value = '0';
  assert.equal(collect(fields).overbooking_percent, 0);
});

test('el servicio sólo ofrece servicios del centro elegido', () => {
  assert.match(src, /franjaServicioItems\(centro\)/);
  assert.match(src, /String\(s\.centro \|\| ""\) === String\(centro\)/);
  assert.match(src, /e\.target\.matches\("\[data-centro-select\]"\)/);
  assert.match(src, /if \(fr\.servicio && !permitidos\[String\(fr\.servicio\)\]\) delete fr\.servicio/);
  // el centro encabeza su sección; el servicio vive en cada fila de horario.
  // El label puede venir directo o envuelto en T(key, vars, "Centro") (i18n).
  assert.match(horas, /<span class="hor-lbl">(?:'\s*\+\s*esc\(T\([^)]*?, )?"?Centro/);
  assert.match(horas, /data-servicio-ci=/);
});

test('la lista de servicios se repinta sin re-renderizar el editor de centros', () => {
  // el fetch vuelve después del primer render: rehacer los centros perdería
  // lo tipeado en otra fila
  assert.match(src, /function pintarServiciosFranja\(sec\)/);
  // se reconstruye el control para que comboHtml lo dimensione con la lista ya
  // cargada (hook control-content-fits), sin pisar la fila enfocada
  // innerHTML: la celda es una columna de la grilla — reemplazarla entera le
  // sacaría la fila su lugar y su etiqueta en pantalla angosta
  assert.match(src, /fila\.innerHTML = franjaServicioHtml\(/);
  assert.match(src, /fila\.contains\(document\.activeElement\)\) return/);
  assert.doesNotMatch(src, /pintarServiciosFranja[\s\S]{0,200}reRenderCentros/);
});

test('Pacientes: sección directa cableada a su controlador, sin router global', () => {
  assert.match(pacientesScreen, /<div id="sec-pacientes"/);
  assert.match(pacientesScreen, /static\('platform\/admin-patients\.mjs'\)/);
  // The direct controller owns only its server-rendered screen.  The old
  // global router registration was deleted with admin.html.
  const patientsJs = fs.readFileSync(new URL('../static/platform/admin-patients.mjs', import.meta.url), 'utf8');
  assert.match(pacientesScreen, /document\.getElementById\("sec-pacientes"\)/);
  assert.match(pacientesScreen, /mount: \(Patients\) => Patients\.mount\(host\)/);
  assert.match(patientsJs, /export function mount\(host, options\)/);
  assert.doesNotMatch(patientsJs, /SanaAdminRouter|sanaRoutePacientes|location\.hash/);
});

test('empty catalogs call the existing creation form with its type and null id', () => {
  assert.match(src, /if \(!data\.items\.length && puedeAltaBaja\(tipo\)\) \{[\s\S]*?return renderForm\(tipo, null\);/);
  assert.doesNotMatch(src, /renderForm\(sec, tipo, null\)/);
});

test('intake bulk feedback counts the ids returned by the atomic endpoint', () => {
  assert.match(src, /var completed = Array\.isArray\(data\.medico_ids\) \? data\.medico_ids\.length : data\.completados;/);
  assert.match(src, /Tn\("admin\.content\.catalog\.doctors\.bulk_completed", completed,/);
});

// Reporte del hospital (video del 8 de octubre de 2026): al abrir un médico el
// servicio del horario salía «Sin especificar» y cualquier guardado lo
// borraba. La lista de servicios llega después del primer dibujo; hasta que
// llega, el combo conserva el servicio guardado.
test('el servicio guardado del horario sobrevive a la carga de la lista', () => {
  assert.match(src, /var SVC_COMPAT_READY = false;/);
  assert.match(src, /if \(!sel && SVC_COMPAT_READY\) actual = "";/);
  assert.doesNotMatch(src, /if \(!sel\) actual = "";/);
  assert.match(src, /SVC_COMPAT = \(j && j\.items\) \|\| \[\];\s*SVC_COMPAT_READY = true;/);
  // el repintado parte del servicio del estado, no de un control vaciado
  assert.match(src, /var actual = hid && hid\.value \? hid\.value : \(fr\.servicio \|\| ""\);/);
});

// ---------------------------------------------------------------------------
// Fichas de sólo lectura (agenda tomada del servicio): el botón Guardar no
// puede prometer un guardado que no existe, y el aviso de traducción de la IA
// no aplica a una sesión de médico, cuyos cambios nunca tocan contenido
// traducible del catálogo.
// ---------------------------------------------------------------------------

test('la ficha del médico con agenda del servicio no ofrece Guardar; el resto la conserva', () => {
  const match = src.match(/function medicoFichaReadOnly\(tipo, isNew, item\) \{[\s\S]*?\n  \}/);
  assert.ok(match, 'medicoFichaReadOnly no encontrada');
  const previous = globalThis.window;
  try {
    globalThis.window = { CURRENT_ROLE: 'medico' };
    const readOnly = new Function(`${match[0]}; return medicoFichaReadOnly;`)();
    assert.ok(readOnly('medicos', false, { _agendas_servicio: [{ id: 'general' }] }),
      'the medico read-only ficha drops the action row');
    assert.ok(!readOnly('medicos', false, { _agendas_servicio: [] }),
      'an editable medico schedule keeps Guardar');
    assert.ok(!readOnly('medicos', false, { centros: [] }),
      'a medico editing their own hours keeps Guardar');
    assert.ok(!readOnly('medicos', true, { _agendas_servicio: [{ id: 'general' }] }),
      'a new ficha keeps Guardar');
    globalThis.window = { CURRENT_ROLE: 'admin' };
    assert.ok(!new Function(`${match[0]}; return medicoFichaReadOnly;`)()('medicos', false, { _agendas_servicio: [{ id: 'general' }] }),
      'the admin keeps Guardar even on a service-owned schedule');
    assert.ok(!new Function(`${match[0]}; return medicoFichaReadOnly;`)()('servicios', false, { _agendas_servicio: [{ id: 'general' }] }),
      'other catalog types are unaffected');
  } finally {
    if (previous === undefined) delete globalThis.window; else globalThis.window = previous;
  }
  assert.match(src, /var readOnlyFicha = medicoFichaReadOnly\(tipo, isNew, item\);/);
  assert.match(src, /if \(!readOnlyFicha\) \{\s*html \+= translationNoticeHtml\(\);\s*html \+= '<div class="cnt-actions">/);
  // the row the editable form still gets, verbatim
  assert.match(src, /'<button class="btn btn-accent" type="submit" form="cnt-form">' \+ esc\(T\("admin\.content\.catalog\.common\.save", null, "Guardar"\)\)/);
  // a form without the row cannot crash its own submit listener
  assert.match(src, /if \(!btn \|\| btn\.disabled\) return;/);
});

test('el aviso de traducción de la IA no se muestra al médico', () => {
  assert.match(src, /function translationNoticeHtml\(\) \{\s*if \(window\.CURRENT_ROLE === "medico"\) return "";/);
  assert.match(src, /ai\.notice\.translation/, 'other roles keep the notice');
});

// ---------------------------------------------------------------------------
// Mis agendas en un celular (390px): el párrafo explicativo de la agenda
// tomada del servicio arrancaba en x0 mientras el título y las tarjetas
// tenían su margen de 16px.
// ---------------------------------------------------------------------------

test('Mis agendas alinea el texto suelto con el título y las tarjetas a 390px', () => {
  const workspaceCss = fs.readFileSync(new URL('../static/platform/doctor-workspace.css', import.meta.url), 'utf8');
  const workspaceTpl = fs.readFileSync(new URL('../templates/admin/doctor-workspace.html', import.meta.url), 'utf8');
  assert.match(workspaceCss,
    /@media\(max-width:720px\)\{\.dw-workspace>p,\.dw-workspace>button,\.dw-workspace>section\.dw-actions,#doctor-recovery,\.dw-workspace>form:not\(\.dw-card\)>p,\.dw-workspace>form:not\(\.dw-card\)>\.dw-edit-actions\{padding-left:var\(--adm-gutter,16px\);padding-right:var\(--adm-gutter,16px\)\}\}/);
  // the rule only works if the schedule note is a direct child of the bare
  // (non-card) schedule form in Mis agendas, and the onboarding variant stays
  // a card so its children are never padded twice.
  assert.match(workspaceTpl, /<form data-schedule class="dw-section\{% if onboarding %\} dw-card\{% endif %\}">/);
  // Mis agendas itself (the product owner, 2026-10-27): the note moved into the card of
  // the schedule it explains, and the screen keeps ONE 16px content edge at
  // every width (the panel's, as in «Pacientes de hoy») without doubling the
  // bare-child phone gutter.
  assert.match(workspaceTpl, /\{% if managed_rows and onboarding %\}<p>\{\{ managed_rows\[0\]\.message \}\}<\/p>\{% endif %\}/);
  assert.match(workspaceTpl, /row is sameas managed_rows\[0\] %\}<p>\{\{ row\.message \}\}<\/p>/);
  assert.match(workspaceCss, /\.dw-workspace--agendas\{padding:16px;/);
  // The reset carries the gutter rule's own `form:not(.dw-card)`: with less
  // specificity it lost and «Turnos de N min» sat 16px right of the cards.
  assert.match(workspaceCss, /\.dw-workspace--agendas>p,\.dw-workspace--agendas>form:not\(\.dw-card\)>p,\.dw-workspace--agendas>form:not\(\.dw-card\)>\.dw-edit-actions\{padding-left:0;padding-right:0\}/);
  assert.doesNotMatch(workspaceCss, /position:sticky/, 'no sticky action bar');
  assert.match(workspaceCss, /\.dw-card\{[^}]*padding:16px/);
});

// ---------------------------------------------------------------------------
// El rol de plantel se muestra como «Profesional» (las etiquetas de rol, no
// los sustantivos clínicos: el campo de vínculo clínico sigue siendo
// «Médico»).
// ---------------------------------------------------------------------------

test('el rol medico se muestra como Profesional sin tocar los sustantivos clínicos', () => {
  assert.equal(systemEs['admin.system.account.role.medico'], 'Profesional');
  assert.equal(systemEn['admin.system.account.role.medico'], 'Professional');
  assert.equal(systemEs['admin.system.users.role.medico.label'], 'Profesional');
  assert.equal(systemEn['admin.system.users.role.medico.label'], 'Professional');
  assert.equal(systemEs['admin.system.users.field.medico'], 'Médico', 'the clinical link field stays clinical');
  assert.equal(systemEn['admin.system.users.field.medico'], 'Doctor');
  assert.match(account, /admin\.system\.account\.role\.medico", null, "Profesional"\)/);
  assert.match(users, /admin\.system\.users\.role\.medico\.label", null, "Profesional"\)/);
  assert.doesNotMatch(users, /admin\.system\.users\.field\.medico", null, "Profesional"/);
  // the wire slug never moved
  assert.match(users, /\["medico", T\("admin\.system\.users\.role\.medico\.label"/);
});

test('Cuenta del médico: el perfil se ve y se edita en la propia Cuenta, con sus claves en es y en', () => {
  for (const [label, catalog] of [['es', systemEs], ['en', systemEn]]) {
    for (const key of ['profile.heading', 'profile.no_phone', 'profile.load_error', 'profile.retry', 'photo.change', 'photo.add',
      'access.title', 'security.title', 'password.set', 'password.change', 'tfa.off', 'edit', 'add'])
      assert.ok(catalog['admin.system.account.' + key], label + ' ' + key);
  }
  assert.match(account, /cardHtml\("acct-profile"/);
  assert.match(account, /\/api\/admin\/doctor-profile/);
  assert.doesNotMatch(account, /href="\/admin\/onboarding"/);
  assert.match(account, /if \(medico && !data\["2fa_vencido"\]\) html \+= profileSectionHtml\(\);/);
});

// ---------------------------------------------------------------------------
// Progressive disclosure (the product owner, 2026-10-26: «if there's no content, why to
// show all the ui? all over the admin panel»).
// ---------------------------------------------------------------------------

test('an empty catalog a role cannot fill shows one empty state, no toolbar, gear or header', () => {
  const shell = src.slice(src.indexOf('function renderListaShell'), src.indexOf('function renderListaRows'));
  const empty = shell.indexOf('if (!data.items.length) {');
  assert.ok(empty > 0 && empty < shell.indexOf('<div class="cnt-toolbar">'), 'returns before any toolbar markup');
  assert.match(shell, /Filters\.applyDisclosure\(sec, "empty"\);\s*sec\.innerHTML = Filters\.emptyStateHtml\(\{\s*title: T\("admin\.content\.catalog\.list\.empty_readonly"/);
  for (const [label, catalog] of [['es', contentEs], ['en', contentEn]]) {
    assert.ok(catalog['admin.content.catalog.list.empty_readonly'], label);
  }
});

test('a catalog filter is offered only with two values in the data, or when already set', () => {
  // The rule itself (and the ten-row threshold) is the shared one — see the
  // «diez filas» test above; here only the per-screen value counts.
  // Agenda only when doctors with AND without hours both exist
  assert.match(src, /var agendas = \(items\.some\(medTieneHorarios\) \? 1 : 0\) \+ \(items\.some\(function \(it\) \{ return !medTieneHorarios\(it\); \}\) \? 1 : 0\);/);
  assert.match(src, /var searchable = data\.items\.length > 15 \|\| !!SEARCH\[tipo\];/);
});

test('filters that hide every row swap the table and gear for the shared no-match state', () => {
  const rows = src.slice(src.indexOf('function renderListaRows'), src.indexOf('// FORMULARIOS'));
  assert.match(rows, /Filters\.disclosure\(\{ total: n, shown: visible\.length, filtered: filtered \}\)/);
  assert.match(rows, /if \(tableWrap\) tableWrap\.hidden = !visible\.length;/);
  assert.match(rows, /if \(gear\) gear\.hidden = !visible\.length;/);
  assert.match(rows, /Filters\.noMatchHtml\(\{[\s\S]{0,200}admin\.shell\.list\.clear_filters[\s\S]{0,80}clearAttrs: \{ "data-clear-catalog": "" \}/);
  assert.match(rows, /if \(!visible\.length\) \{\s*theadRow\.innerHTML = "";\s*tbody\.innerHTML = "";/);
  assert.doesNotMatch(rows, /colspan="' \+ \(cols\.length/);
  assert.match(src, /id="cnt-nomatch-' \+ tipo \+ '" hidden><\/div>/);
});

test('a column with nothing in any row of the list is not drawn', () => {
  const rows = src.slice(src.indexOf('function renderListaRows'), src.indexOf('// FORMULARIOS'));
  // Judged on the whole list (`items`), never on the filtered rows: the
  // columns must not jump while someone types in the search.
  assert.match(rows, /var cols = catalogPicker\(tipo\)\.activas\(\)\.filter\(function \(c\) \{\s*return c\.always \|\| c\.key === SORT\[tipo\]\.key \|\| items\.some\(function \(it\) \{ return !cellEmpty\(tipo, c, it\); \}\);\s*\}\);/);
  // A cell of pictures and no words (the occupancy bars, now without «3/5»)
  // is not empty: the column must not vanish when every doctor has bars.
  const start = src.indexOf('function cellEmpty');
  const cellEmpty = new Function('renderCell', src.slice(start, src.indexOf('function matchesSearch', start)) + '; return cellEmpty;');
  const withHtml = (html) => cellEmpty(() => html)('medicos', {}, {});
  assert.equal(withHtml('<span class="med-occupancy" role="img" aria-label="60%"><span aria-hidden="true"></span></span>'), false);
  assert.equal(withHtml('<img src="/a.png" alt="">'), false);
  assert.equal(withHtml('<span class="cnt-muted"> </span>'), true);
  assert.equal(withHtml(''), true);
  assert.equal(rows.split('catalogPicker(tipo).activas()').length, 2, 'the header and the rows use the same columns');
  // The doctors' account cell is empty, not a dash, where there is no account.
  assert.match(src, /return label \? esc\(label\) : "";/);
});

test('a folded field that the browser or the server rejects is put on screen', () => {
  // `invalid` does not bubble: each folded control opens its own group.
  assert.match(src, /\$\$\("details\.cnt-fold input, details\.cnt-fold select, details\.cnt-fold textarea", sec\)\.forEach\(function \(control\) \{\s*listen\(control, "invalid", function \(\) \{\s*var fold = control\.closest\("details"\);\s*if \(fold && !fold\.open\) fold\.open = true;/);
  // A server rejection with no control of its own opens every fold before the message.
  assert.match(src, /if \(showFieldError\(tipo, sec, String\(e2\.message\)\)\) return;[\s\S]{0,220}\$\$\("details\.cnt-fold", sec\)\.forEach\(function \(fold\) \{ fold\.open = true; \}\);\s*saveStatus\(/);
});

test('the doctor editor has a second specialty select that saves and clears the field', () => {
  assert.match(src, /class="adm-sel cnt-field-specialty" id="med-especialidad-2"/);
  assert.match(src, /id="med-cluster-chip-2"/);
  assert.match(src, /if \(esp2 && esp2\.value\) doc\.especialidad_2 = esp2\.value; else delete doc\.especialidad_2;/);
  assert.ok(contentEs['admin.content.catalog.doctors.field_specialty_2']);
  assert.ok(contentEn['admin.content.catalog.doctors.field_specialty_2']);
});

test('the AI translation notice is the caption of «Guardar», only with an editable item form', () => {
  // No band over the screen, a list or an empty catalog (2026-11-08 B5): the
  // line right above «Guardar» of an editable form, the action that hands the
  // text to the AI. A read-only ficha has no Guardar, so no caption.
  assert.doesNotMatch(contentScreen, /data-ai-notice="translation"/);
  assert.doesNotMatch(src, /setTranslationNotice/);
  assert.match(src, /'<p class="adm-ai-notice cnt-ai-notice" data-ai-notice-inline="translation">' \+ esc\(T\("ai\.notice\.translation", null,/);
  assert.match(directCss, /\.cnt-ai-notice\{margin:32px 0 0;\}/);
  assert.match(directCss, /\.cnt-ai-notice:not\(\[hidden\]\)\+\.cnt-actions\{margin-top:12px;\}/);
});

test('a doctor shows the AI translation caption on «Información básica» alone, under the form', () => {
  // Over the tabs it appeared and vanished with that tab and moved them
  // ~36px on every switch (Opus UI review 2026-11-03); under the form it moves nothing.
  assert.doesNotMatch(src, /avisoTraduccion/);
  assert.match(src, /var aviso = \$\(":scope > \.cnt-ai-notice", sec\);\s*if \(aviso\) aviso\.hidden = name !== "info";/);
});

test('a service announcement shows its title and text only while «Mostrar aviso» is on', () => {
  // 2026-11-08 B3: empty title/text fields under an unchecked box were UI for
  // something switched off. The fields keep their values while hidden.
  assert.match(src, /<div class="svc-announcement-dependent" id="svc-announcement-dependent"' \+ \(anuncioOn \? "" : " hidden"\) \+ '>/);
  assert.match(src, /var anuncioOn = anuncio\.active !== false && !!anuncio\.body;/);
  const start = src.indexOf("  function wireServiceAnnouncement(sec) {");
  assert.ok(start > 0, "wireServiceAnnouncement exists");
  const body = src.slice(start, src.indexOf("\n  }\n", start) + 4);
  const nodes = { "#svc-announcement-active": { checked: false, handlers: {} }, "#svc-announcement-dependent": { hidden: false } };
  const fn = new Function("$", "listen", body + "; return wireServiceAnnouncement;")(
    (sel) => nodes[sel] || null,
    (target, type, handler) => { target.handlers[type] = handler; });
  fn({});
  const check = nodes["#svc-announcement-active"], dependent = nodes["#svc-announcement-dependent"];
  assert.equal(dependent.hidden, true, "unchecked: no title/text fields");
  check.checked = true; check.handlers.change();
  assert.equal(dependent.hidden, false, "checked: the fields appear");
  check.checked = false; check.handlers.change();
  assert.equal(dependent.hidden, true, "unchecked again: hidden again");
  assert.match(src, /wireServiceAnnouncement\(sec\)/);
});
