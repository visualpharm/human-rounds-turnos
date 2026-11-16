// Guardas de fuente sobre my-requests.js: el botón "Cancelar solicitud" de la
// tarjeta pendiente tiene que ser un botón de PELIGRO chico/rojo — nunca un
// link de texto plano (el detalle aparte, /request/<id>, se retiró el
// 2026-08-01: todo lo que hacía vive ahora en la tarjeta) — y la tarjeta
// "Anteriores" (mutedCardHtml) nunca vuelve a unir médico+centro con " · "
// (ui-rules). Mismo patrón estático que entrevista-qr.test.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const js = readFileSync(join(ROOT, 'static/platform/account-requests.mjs'), 'utf8');

test('cancelar una solicitud pendiente: botón de peligro chico/rojo, nunca un link de texto plano', () => {
  const m = /function richCardHtml\(it, idx, opts\) \{[\s\S]*?\n  \}/.exec(js);
  assert.ok(m, 'richCardHtml debe existir');
  const body = m[0];
  // pendiente: "Cancelar solicitud"; aprobada activa: "Cancelar turno" —
  // mismo botón (req-btn-danger req-btn-cancel), nunca un link de texto plano.
  // La ventana es más ancha que el texto porque i18n envuelve el fallback en
  // T("clave", null, "..."); lo que importa sigue siendo la proximidad al tag.
  assert.match(body, /class="req-btn req-btn-danger req-btn-cancel" data-cancel>[\s\S]{0,120}Cancelar solicitud/);
  assert.match(body, /class="req-btn req-btn-danger req-btn-cancel" data-cancel>[\s\S]{0,120}Cancelar turno/);
  assert.doesNotMatch(body, /class="stz-link" data-cancel/);
});

test('mutedCardHtml (tarjetas "Anteriores"): médico y centro en líneas separadas, nunca unidos con " · "', () => {
  const m = /function mutedCardHtml\(it, idx\) \{[\s\S]*?\n  \}/.exec(js);
  assert.ok(m, 'mutedCardHtml debe existir');
  const body = m[0];
  assert.doesNotMatch(body, /sub\.join\(" · "\)/);
  assert.doesNotMatch(body, /var sub = \[\]/);
  const subLines = (body.match(/<div class="req-sub">/g) || []).length;
  assert.equal(subLines, 2, 'con quién y dónde deben ser DOS <div class="req-sub"> separados');
});

test('la entrevista se abre en un panel lateral sin navegar fuera de Mi cuenta', () => {
  assert.match(js, /function openInterviewSide\(url, actualizar\)/);
  assert.match(js, /embed=1/);
  assert.match(js, /data-entrevista=/);
  assert.match(js, /Actualizar síntomas/);
  assert.doesNotMatch(js, /data-reprogramar/);
});

test('lista de espera y controles secundarios no compiten con el único primario', () => {
  // Review 2026-11-15: the waitlist sits on its own row, left-aligned, in dark ink;
  // secondary buttons are dark too (grey read as disabled).
  assert.match(js, /\.pt-wl\{display:flex;justify-content:flex-start/);
  assert.match(js, /\.req-btn-primary\{background:var\(--btn-bg,var\(--accent\)/);
  assert.match(js, /\.req-btn\{[^}]*color:var\(--btn2-fg,var\(--text\)\)/);
  assert.match(js, /\.ms-feed-link\{[^}]*color:var\(--text\)/);
  assert.match(js, /font-size:15px;line-height:21px;color:var\(--text\);cursor:pointer;/);
});

test('en el teléfono los botones conservan su ancho: cancelar nunca iguala al primario', () => {
  assert.doesNotMatch(js, /\.pt-acts\{flex-direction:column;align-items:stretch/);
  assert.doesNotMatch(js, /\.req-acts\{flex-direction:column;align-items:stretch/);
  assert.doesNotMatch(js, /\.mt-confirm\{align-items:stretch/);
});

test('las tarjetas no traen íconos dibujados a mano: archivos de Icons8 por máscara', () => {
  assert.doesNotMatch(js, /<svg/);
  for (const f of ['clock', 'error', 'refresh', 'plus', 'marker', 'cancel', 'expand-arrow', 'calendar', 'calendar-plus']) {
    assert.ok(js.includes(`"${f}-sf-regular.svg"`), f);
  }
});

test('el turno prioriza servicio y mantiene fecha + calendario en una sola línea', () => {
  const body = extractFn('richCardHtml');
  assert.match(body, /class="pt-title">' \+ esc\(nombre\)/);
  assert.doesNotMatch(body, /next_appointment_title/);
  assert.match(body, /class="pt-when-row"/);
  assert.match(body, /class="pt-calendar-link"/);
  assert.doesNotMatch(body, /class="req-btn req-btn-xs" href="\/api\/appointment/);
});

test('la solicitud pendiente agrupa fecha solicitada con calendario, sin aviso repetido', () => {
  const body = extractFn('richCardHtml');
  // The state and the note are said once, under the section heading (renderList).
  assert.doesNotMatch(body, /pt-pending-status|class="pt-note"|pending_note/);
  // The doctor is named as soon as it is known, confirmed or not.
  assert.match(body, /if \(it\.medico_asignado\) \{/);
  assert.match(body, /class="pt-pref-row"/);
  assert.match(body, /class="req-pref"/);
  assert.match(body, /class="req-btn req-btn-xs req-pref-cal"/);
  assert.ok(
    body.indexOf('class="pt-pref-row"') < body.indexOf('class="req-pref"') &&
      body.indexOf('class="req-pref"') < body.indexOf('class="req-btn req-btn-xs req-pref-cal"'),
    'the requested date must precede its calendar action in the same group',
  );
  assert.doesNotMatch(body, /<div style="margin-top:11px;">/);
});

test('la tarjeta activa conserva jerarquía en el rail estrecho del chat', () => {
  assert.match(js, /\.pt-top\{display:grid;grid-template-columns:minmax\(0,1fr\) auto/);
  // Actions on one row, the waitlist always on the next (review 2026-11-15:
  // one card had it under the buttons, another to the right).
  assert.match(js, /\.pt-footer\{display:flex;flex-direction:column;align-items:flex-start/);
  // Stacks before the chat panel squeezes the title to nothing
  // («CardiologíaMiércoles…» glued together at a ~643px container with the
  // chat open): the date sits over «Al calendario», the header stacks at 640
  // and a long title wraps inside its own column.
  assert.match(js, /@container mt \(max-width:640px\)\{" \+\s*"\.pt-top\{grid-template-columns:1fr/);
  assert.match(js, /\.pt-when-row\{display:flex;flex-direction:column;align-items:flex-end/);
  assert.match(js, /\.pt-title\{[^}]*" \+\s*"line-height:1\.12;hyphens:auto;overflow-wrap:break-word;\}/);
});

test('Mi cuenta pinta los turnos embebidos SIN repetir la consulta a /api/requests, y en UN solo pintado', () => {
  // preloaded evita el fetch de red a /api/requests (embebido por el
  // servidor) -- pero el pintado en sí espera a que loadServicios()/
  // loadCentros() resuelvan para tener nombre de servicio y dirección del
  // centro desde el vamos. Antes se pintaba dos veces (eager + de nuevo al
  // resolver el catálogo) y el segundo pintado, un host.innerHTML de
  // reemplazo total, le borraba a la persona cualquier interacción abierta
  // en la tarjeta -- el "¿Cancelar el turno? Sí/No" desaparecía a mitad de
  // click (bug real, 2026-08-05, ver tests/e2e/cancel-waitlist.spec.mjs).
  assert.match(js, /var preloaded = options\.requests/);
  const mountBlock = /if \(host\) \{[\s\S]*?\n  \}\n  function dispose/.exec(js);
  assert.ok(mountBlock, 'bloque de montaje (if (host) { ... })() no encontrado');
  const body = mountBlock[0];
  // preloaded: exactamente UNA llamada a renderList, disparada sólo después
  // de que el catálogo resuelve -- nunca una llamada eager seguida de otra.
  const renderCalls = (body.match(/renderList\(preloaded\)/g) || []).length;
  assert.equal(renderCalls, 1, 'renderList(preloaded) debe aparecer una sola vez -- nunca eager + de nuevo al resolver el catálogo');
  assert.match(body, /if \(Array\.isArray\(preloaded\)\) \{\s*Promise\.all\(\[loadServicios\(\), loadCentros\(\), datesReady, prepReady\]\)\.then\(function \(\) \{ renderList\(preloaded\); \}\)/);
  // Mientras se espera el catálogo, un "Cargando…" -- mismo patrón que
  // lab-results.js/admin-*.js -- nunca una tarjeta a medio llenar.
  assert.match(body, /host\.innerHTML = '<p[^>]*>' \+\s*T\("account\.mine\.requests\.loading", null, "Cargando…"\) \+ "<\/p>";/);
});

// nombreServicio: el pintado EAGER de arriba (renderList(preloaded) antes de
// que loadServicios() resuelva) corre con svcName todavía vacío — antes de
// esto, las dos tarjetas (richCardHtml/mutedCardHtml) caían
// directo a `it.servicio_id` crudo en ese momento, y si el fetch a
// /api/services era lento (o fallaba, tragado por el .catch de abajo) se
// quedaban mostrando el slug para siempre, sin ningún error en consola (bug
// real, demo, 2026-08-05: "medicina-general-riverside-clinic" en vez de "Medicina
// general" en Mis solicitudes). Un solo helper, un solo lugar donde puede
// volver a romperse.
function extractFn(name) {
  const re = new RegExp(`function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n  \\}`);
  const m = js.match(re);
  assert.ok(m, `función ${name} no encontrada`);
  return m[0];
}

function loadNombreServicio(svcName, svcNamesReady) {
  const code =
    `var svcName = ${JSON.stringify(svcName)};\n` +
    `var svcNamesReady = ${svcNamesReady};\n` +
    `function T(key, vars, fallback) { return fallback || key; }\n` +
    extractFn('humanizeId') + '\n' +
    extractFn('nombreServicio') + '\n' +
    'return {nombreServicio};';
  return new Function(code)().nombreServicio;
}

test('nombreServicio: catálogo aún cargando -> placeholder, NUNCA el slug', () => {
  const nombreServicio = loadNombreServicio({}, false);
  const it = { servicio_id: 'medicina-general-riverside-clinic' };
  const nombre = nombreServicio(it, 'Solicitud');
  assert.notEqual(nombre, 'medicina-general-riverside-clinic');
  assert.equal(nombre, '…');
});

test('nombreServicio: catálogo cargado y el id aparece -> el nombre real', () => {
  const nombreServicio = loadNombreServicio({ 'medicina-general-riverside-clinic': 'Medicina General' }, true);
  const it = { servicio_id: 'medicina-general-riverside-clinic' };
  assert.equal(nombreServicio(it, 'Solicitud'), 'Medicina General');
});

test('nombreServicio: catálogo cargado y el id NO aparece (borrado/otro pack) -> humanizado, no el slug', () => {
  const nombreServicio = loadNombreServicio({}, true);
  const it = { servicio_id: 'cardiologia-sampleton-hospital' };
  const nombre = nombreServicio(it, 'Solicitud');
  assert.notEqual(nombre, 'cardiologia-sampleton-hospital');
  assert.equal(nombre, 'Cardiologia sampleton hospital');
});

test('nombreServicio: fila importada de HSI (sin id, con servicio_nombre) no se toca', () => {
  const nombreServicio = loadNombreServicio({}, true);
  const it = { servicio_id: 'hsi-importado', servicio_nombre: 'Turno registrado en HSI' };
  assert.equal(nombreServicio(it, 'Solicitud'), 'Turno registrado en HSI');
});

test('loadServicios() marca svcNamesReady incluso si /api/services falla (nunca se queda "cargando" para siempre)', () => {
  assert.match(js, /\}\)\.catch\(function \(\) \{\}\)\.then\(function \(\) \{ svcNamesReady = true; \}\);/);
});

test('las dos tarjetas usan nombreServicio(), ninguna repite el fallback directo al id crudo', () => {
  // el patrón viejo (bug): resolvía a it.servicio_id sin ningún gate.
  assert.doesNotMatch(js, /svcName\[it\.servicio_id\]\s*\|\|\s*it\.servicio_nombre\s*\|\|\s*it\.servicio_id/);
  const usos = (js.match(/var nombre = nombreServicio\(it,/g) || []).length;
  assert.equal(usos, 2, 'richCardHtml + mutedCardHtml');
});

test('el título de la sección es el nombre de su pestaña: una sola clave, «Mis turnos»', () => {
  const m = /function titleRowHtml\(sinSacar\) \{[\s\S]*?\n  \}/.exec(js);
  assert.ok(m, 'titleRowHtml debe existir');
  assert.match(m[0], /<h1 class="mt-h1">' \+ T\("account\.dash\.nav_turnos", null, "Mis turnos"\)/);
  assert.doesNotMatch(m[0], /account\.mine\.requests\.title/);
});
