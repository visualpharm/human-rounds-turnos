// Guardas de fuente para «Mis pacientes» (/admin/my-patients,
// static/platform/admin-doctor-patients.mjs, 2026-08-01): roster
// longitudinal por médico. Mismo patrón que admin-entrevistas.test.mjs —
// node --test sobre el texto de los archivos, sin navegador — alcanza para
// clavar el cableado más el contrato de triage/orden/links. El controlador
// nativo no bootea solo: su template lo importa y lo monta
// (mountDoctorPatients({root, role}) -> disposer con .ready).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const jsPath = join(ROOT, 'static', 'platform', 'admin-doctor-patients.mjs');
const directTemplatePath = join(ROOT, 'templates', 'admin', 'my-patients.html');

test('admin-doctor-patients.mjs exists and is imported by its direct template', () => {
  assert.ok(existsSync(jsPath), 'static/platform/admin-doctor-patients.mjs debe existir');
  const template = readFileSync(directTemplatePath, 'utf8');
  assert.match(template, /static\('platform\/admin-doctor-patients\.mjs'\)/);
});

// Product owner 05.09, 15.09 C3/R2: «filters without frames, label + chevron; one module
// of filters». The doctor picker is an ordinary .adm-sel in a .queue-controls
// row, so the shared admin-direct.css rule draws it; no own size or frame.
test('the doctor picker looks like every other .adm-sel: no own CSS for it', () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.match(js, /<div class="adm-tools queue-controls">[^]*<select class="adm-sel adm-picker" id="dp-medico">/);
  assert.doesNotMatch(js, /#sec-mis-pacientes \.adm-sel|#dp-medico\{/);
  const css = readFileSync(join(ROOT, 'static', 'root', 'admin-direct.css'), 'utf8');
  assert.match(css, /:is\(\.adm-filter-fields,\.cnt-toolbar-filters,\.queue-controls\) select\.adm-sel\{[^}]*border:1px solid transparent/);
  // It chooses what the screen shows, it is not a filter: on the phone it is
  // never folded behind a «Filtros» toggle, and none is planted for it.
  assert.match(css, /:not\(\.filters-open\) select\.adm-sel:not\(\.adm-picker\)\{display:none;\}/);
  const fit = readFileSync(join(ROOT, 'static', 'platform', 'admin-control-fit.mjs'), 'utf8');
  assert.match(fit, /indexOf\(" adm-picker "\) < 0/);
  assert.match(fit, /!plegables\(row\)\.length\) return;/);
});

test('direct template owns the patient roster container', () => {
  const template = readFileSync(directTemplatePath, 'utf8');
  assert.match(template, /<section id="sec-mis-pacientes" aria-labelledby="adm-h1"><\/section>/);
});

test('patient roster is direct-only and keeps query/history state', () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.match(js, /URLSearchParams\(window\.location\.search\)\.get\(["']doctor["']\)/);
  assert.match(js, /window\.addEventListener\("popstate", onPopstate\)/);
  assert.doesNotMatch(js, /SanaAdminRouter|SanaAdminSections|location\.hash|hashchange/);
});

test('server endpoint is doctor-patients with permitir_medico opt-in', () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.match(js, /\/api\/admin\/doctor-patients/);
  const py = readFileSync(join(ROOT, 'app', 'admin_panel', 'doctor_patients_router.py'), 'utf8');
  // Its `openapi_extra` (the answer's model, api_body.docs, and the marker of a handler that gates by
  // hand, core/deps.py STAFF_GATE_DOCS) is documentation only.
  assert.match(py, /@router\.get\("\/api\/admin\/doctor-patients"[,)]/);
  assert.match(py, /gate\(request, permitir_medico=True\)/);
  assert.doesNotMatch(py, /handler|LegacyHandlerShim/);
});

test('the turnos manifest registers doctor_patients as a GET route', () => {
  // routing.py ya no lista rutas de módulos a mano: el loader las construye
  // desde modules/<id>/module.json (contrato Tier 2).
  const manifest = JSON.parse(
    readFileSync(join(ROOT, 'app', 'modules', 'turnos', 'module.json'), 'utf8'));
  const entry = manifest.routes.find(
    (r) => (r.module || r) === 'app.admin_panel.doctor_patients');
  assert.ok(entry, 'doctor_patients debe estar en las rutas del manifiesto');
  assert.ok(entry.methods.includes('GET'));
});

test('reuses the exact queue-triage chip classes (TRIAGE_LABEL / .queue-triage--<color>)', () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.match(js, /ROJO: T\("admin\.queue\.triage\.rojo", null, "Rojo"\)/);
  assert.match(js, /VERDE: T\("admin\.queue\.triage\.verde", null, "Verde"\)/);
  assert.match(js, /"queue-triage queue-triage--' \+ key\.toLowerCase\(\)/);
  const css = readFileSync(join(ROOT, 'static', 'root', 'admin-direct.css'), 'utf8');
  assert.match(css, /\.queue-triage--rojo\{/);
});

test('awaits the canonical table ESM before creating column widths', () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.match(js, /importStatic\("platform\/admin-table\.mjs"\)/);
  assert.match(js, /return m;[\s\S]*?Table\.crear\(/);
  assert.doesNotMatch(js, /window\.SanaTable/);
});

test('uses canonical Format for contact — never a second phone formatter', () => {
  const js = readFileSync(jsPath, 'utf8');
  // the phone says whose it is, the same way «Mis turnos» does (problems.md №4)
  assert.match(js, /fmt\(\)\.contactoQuienHtml\(p\)/);
  assert.doesNotMatch(js, /function\s+telNacional/, 'no debe redefinir el formateador de teléfono');
});

test('uses canonical Format.nombre for patient names', () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.match(js, /return Format\.nombre\(raw\)/);
  assert.match(js, /importStatic\("platform\/format\.mjs"\)/);
  assert.doesNotMatch(js, /SanaFormat/);
  assert.match(js, /var nombrePaciente = nombre\(p\.nombre/);
  assert.match(js, /esc\(nombre\(m\.nombre\)\)/);
});

test('Última entrevista links to the direct consultation view', () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.match(js, /"\/admin\/consult\?interview=" \+ encodeURIComponent\(iv\.id\)/);
});

test('direct screen translates the doctor query to the API query', () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.match(js, /URLSearchParams\(window\.location\.search\)\.get\(["']doctor["']\)/);
  assert.match(js, /"\?medico=" \+ encodeURIComponent\(medicoId\)/);
  assert.match(js, /setDirectDoctorUrl\(id\)/);
  assert.match(js, /STATE\.rol === "medico"/);
  assert.match(js, /history\.replaceState/);
});

test('direct screen waits for DOM and preferences, keeps a labelled fitted picker, and keeps consultation links in the direct shell', () => {
  const js = readFileSync(jsPath, 'utf8');
  const template = readFileSync(directTemplatePath, 'utf8');
  assert.match(js, /importStatic\("platform\/admin-preferences\.mjs"\)/);
  assert.match(js, /Promise\.all\(\[i18nReady, prefsReady, datesReady, formatReady, tableReady, sessionReady, filtersReady\]\)/);
  assert.doesNotMatch(js, /SanaPreferences/);
  // Sin boot implícito: el template importa el módulo nativo y lo monta;
  // bootDirectScreen corre a través del .ready del disposer.
  assert.match(js, /export function mountDoctorPatients/);
  assert.match(js, /dispose\.ready = bootDirectScreen\(\)/);
  assert.match(template, /mod\.mountDoctorPatients\(\{ root: host, role: "\{\{ rol \}\}" \}\)/);
  assert.match(js, /<label class="sr-only" for="dp-medico">/);
  // The picker carries no own size: the shared .adm-sel rule (C3/R2 test).
  assert.match(template, /static\('platform\/admin-control-fit\.mjs'\)[\s\S]*static\('platform\/admin-doctor-patients\.mjs'\)/);
  assert.match(js, /"\/admin\/consult\?interview=" \+ encodeURIComponent\(iv\.id\)/);
});

test('the direct screen reroutes Back and Forward state through popstate', () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.match(js, /window\.addEventListener\("popstate", onPopstate\)/);
  assert.match(js, /function onPopstate\(\) \{ if \(!disposed\) bootDirectScreen\(\)/);
  assert.doesNotMatch(js, /hashchange|location\.hash/);
});

test('mobile roster reflows each semantic row without a horizontal table scroller', () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.match(js, /@media\(max-width:720px\)/);
  assert.match(js, /\.cnt-table-wrap\{overflow:visible/);
  assert.match(js, /\.cnt-table td\{display:block;width:100%!important;min-width:0!important/);
  assert.match(js, /\.cnt-table\{max-width:100%!important;\}/);
  assert.match(js, /\.cnt-table colgroup\{display:none;\}/);
  assert.match(js, /\.cnt-table thead\{display:none/);
  assert.match(js, /\.cnt-table tr\{padding:12px var\(--adm-gutter\);border-bottom:1px solid var\(--border-soft\)/);
  assert.match(js, /\.cnt-table td::before\{content:none;\}/);
  assert.match(js, /overflow-wrap:anywhere/);
  assert.match(js, /td\[data-col='resumen'\]\{display:none/);
});

test('patient name and DNI remain separate table data cells', () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.match(js, /\{ key: "paciente"[\s\S]*\{ key: "dni"/);
  assert.match(js, /paciente: function \(p\)[\s\S]*?dni: function \(p\)/);
});

// Usability study 2026-10-15 (D5): two doctors looked for a place to type a
// DNI in «Mis pacientes» and found none.
test('the roster has the same DNI/name search as Pacientes, matching DNI digits and name words', async () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.match(js, /id="dp-q" type="search"/);
  assert.match(js, /admin\.queue\.patients\.search_placeholder/);
  const { filtrarPacientes } = await import('../static/platform/admin-doctor-patients.mjs');
  const items = [{ dni: '30002329', nombre: 'Facundo Acosta' }, { dni: '30003014', nombre: 'Zoe Aguirre' },
    { dni: '99000001', nombre: 'Sofía Herrera' }];
  assert.deepEqual(filtrarPacientes(items, '30.002.329').map(p => p.nombre), ['Facundo Acosta']);
  assert.deepEqual(filtrarPacientes(items, '3014').map(p => p.nombre), ['Zoe Aguirre']);
  assert.deepEqual(filtrarPacientes(items, 'sofia').map(p => p.nombre), ['Sofía Herrera']);
  assert.deepEqual(filtrarPacientes(items, 'aguirre zoe').map(p => p.nombre), ['Zoe Aguirre']);
  assert.equal(filtrarPacientes(items, '').length, 3);
  assert.equal(filtrarPacientes(items, 'nadie').length, 0);
});
