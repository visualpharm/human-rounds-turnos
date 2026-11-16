import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fixture, flush } from './helpers/queue-runtime-fixture.mjs';

const CREATE = readFileSync(new URL('../static/platform/admin-create-appointment.mjs', import.meta.url), 'utf8');
const QUEUE_CSS = readFileSync(new URL('../static/platform/admin-queue.css', import.meta.url), 'utf8');
const QUEUE_RUNTIME = readFileSync(new URL('../static/platform/admin-queue.mjs', import.meta.url), 'utf8');

function group(id, name, specialty, center, service = 'Consulta') {
  return {
    medico_id: id, medico: name, especialidad: specialty, centro: center,
    turnos: [{ id: `${id}-1`, servicio: service }], libres: []
  };
}

function options(html, className) {
  const select = new RegExp(`<select[^>]*${className}[^>]*>([\\s\\S]*?)<\\/select>`).exec(html)?.[1] || '';
  return [...select.matchAll(/<option value="([^"]*)"[^>]*>([^<]*)<\/option>/g)].map(([, value, label]) => ({ value, label }));
}
async function mounted(t, data) {
  const f = fixture(t, { queueDom: true, initialData: data });
  const owner = f.mount(); f.readyTable(); await owner.ready;
  return f;
}

test('queue filters keep a selected doctor by identity when the typed next day changes group order', async t => {
  const first = { dia: '2026-09-04', grupos: [
    group('med-1', 'Dra. Ana Pérez', 'Cardiología', 'Centro A'),
    group('med-2', 'Dr. Bruno López', 'Pediatría', 'Centro B')
  ] };
  const f = await mounted(t, first);
  const doctor = { value: 'med-2', closest: query => query === '[data-cola-medfiltro]' ? doctor : null };
  f.doc.emit('change', { target: doctor });
  const next = { dia: '2026-09-05', grupos: [
    group('med-2', 'Dr. Bruno López', 'Pediatría', 'Centro B'),
    group('med-1', 'Dra. Ana Pérez', 'Cardiología', 'Centro A')
  ] };
  const typed = { value: '2026-09-05', closest: query => query === '[data-cola-buscar]' ? typed : null };
  f.doc.emit('input', { target: typed }); f.tick(260);
  assert.equal(f.requests.length, 1);
  f.requests[0].resolve(new Response(JSON.stringify(next), { status: 200 })); await flush(); await flush();
  assert.match(f.nodes.get('cola-results').innerHTML, /Dr\. Bruno López/);
  assert.doesNotMatch(f.nodes.get('cola-results').innerHTML, /Dra\. Ana Pérez/);
  assert.deepEqual(options(f.nodes.get('sec-cola').innerHTML, 'queue-doctor-filter').find(option => option.value === 'med-2'),
    { value: 'med-2', label: 'Dr. Bruno López' });
});

test('queue keeps every active rendered filter visible and clearable when typed next day has no match', async t => {
  const f = await mounted(t, { dia: '2026-09-04', grupos: [
    group('med-1', 'Dra. Ana Pérez', 'Cardiología', 'Centro A', 'Cardiología'),
    group('med-2', 'Dr. Bruno López', 'Pediatría', 'Centro B', 'Pediatría')
  ] });
  for (const [selector, value] of [['[data-cola-medfiltro]', 'med-2'], ['[data-cola-servfiltro]', 'Pediatría'], ['[data-cola-especialidadfiltro]', 'Pediatría'], ['[data-cola-centrofiltro]', 'Centro B']]) {
    const control = { value, closest: query => query === selector ? control : null };
    f.doc.emit('change', { target: control });
  }
  const next = { dia: '2026-09-05', grupos: [
    group('med-1', 'Dra. Ana Pérez', 'Cardiología', 'Centro A', 'Cardiología')
  ] };
  const typed = { value: '2026-09-05', closest: query => query === '[data-cola-buscar]' ? typed : null };
  f.doc.emit('input', { target: typed }); f.tick(260);
  f.requests[0].resolve(new Response(JSON.stringify(next), { status: 200 })); await flush(); await flush();
  assert.match(f.nodes.get('cola-results').innerHTML, /No se encontraron turnos/);
  const html = f.nodes.get('sec-cola').innerHTML;
  for (const [className, value] of [['queue-doctor-filter', 'med-2'], ['queue-service-filter', 'Pediatría'], ['queue-specialty-filter', 'Pediatría'], ['queue-center-filter', 'Centro B']]) {
    const select = options(html, className);
    const selected = select.find(option => option.value === value);
    assert.ok(selected, `${value} remains in the select so it can be cleared`);
    assert.match(selected.label, /sin resultados ese día/);
    assert.ok(select.some(option => option.value === ''), 'all option clears the active filter');
  }
  assert.equal(options(html, 'queue-doctor-filter').find(option => option.value === 'med-2').label,
    'Dr. Bruno López (sin resultados ese día)');
});

// Contraparte técnica, 2026-10-24 («Todos los centros» sólo ofrecía CAPS Ostende y el
// Hospital): el filtro listaba los centros de las agendas del día, y un médico
// con dos centros (centro: null en el encabezado) no figuraba en ninguno.
test('the center filter offers every center the server sends, not only the day\'s agendas', async t => {
  const f = await mounted(t, { dia: '2026-10-26',
    centros: ['CAPS Ostende', 'CAPS Pinamar', 'Hospital de Pinamar'],
    grupos: [
      group('med-1', 'Dra. Ana Pérez', 'Cardiología', 'Hospital de Pinamar'),
      group('med-2', 'Dr. Bruno López', 'Pediatría', 'CAPS Ostende')
    ] });
  assert.deepEqual(options(f.nodes.get('sec-cola').innerHTML, 'queue-center-filter').map(o => o.label),
    ['Todos los centros', 'CAPS Ostende', 'CAPS Pinamar', 'Hospital de Pinamar']);
});

test('a doctor with turnos in two centers is found under each, with only that center\'s turnos', async t => {
  const dos = { medico_id: 'med-3', medico: 'Dr. Carlos Gómez', especialidad: 'Cirugía', centro: null,
    centros: ['CAPS Ostende', 'Hospital de Pinamar'], libres: [],
    turnos: [{ id: 't-a', servicio: 'Cirugía', centro: 'CAPS Ostende' },
             { id: 't-b', servicio: 'Cirugía', centro: 'Hospital de Pinamar' }] };
  const f = await mounted(t, { dia: '2026-10-26', centros: ['CAPS Ostende', 'Hospital de Pinamar'],
    grupos: [dos, group('med-1', 'Dra. Ana Pérez', 'Cardiología', 'Hospital de Pinamar')] });
  const pick = { value: 'CAPS Ostende', closest: q => q === '[data-cola-centrofiltro]' ? pick : null };
  f.doc.emit('change', { target: pick });
  const html = f.nodes.get('cola-results').innerHTML;
  assert.match(html, /Dr\. Carlos Gómez/);
  assert.match(html, /t-a/);
  assert.doesNotMatch(html, /t-b/);
  assert.doesNotMatch(html, /Dra\. Ana Pérez/);
});

test('catalog callback only applies to the still-open appointment modal state', () => {
  const helper = CREATE.match(/function modalSigueActivo\(estado\) \{[^\n]+\}/)?.[0];
  assert.ok(helper, 'missing modal state guard');
  const context = { S: { id: 'first' }, $: () => ({}) };
  vm.createContext(context);
  vm.runInContext(`${helper}; globalThis.active = modalSigueActivo;`, context);
  const first = context.S;
  assert.equal(context.active(first), true);
  context.S = { id: 'second' };
  assert.equal(context.active(first), false, 'reopened modal rejects the prior callback');
  context.S = first;
  context.$ = () => null;
  assert.equal(context.active(first), false, 'closed modal rejects the callback');
  assert.match(CREATE, /cargarCatalogo\(\)\.then\(function \(\) \{\s*if \(!modalSigueActivo\(estado\)\) return;/);
});


// 2026-11-08: the doctor table is gone (one flat list, the doctor as a
// column); on the phone the opened filters each take their own full line.
test('queue keeps phone filters within their row; the doctor is a column of the one list', () => {
  const phone = QUEUE_CSS.slice(QUEUE_CSS.indexOf('@media(max-width:640px){\n/* The phone: the search'));
  assert.match(phone, /\.queue-filters \.adm-sel\{box-sizing:border-box;flex:1 1 100%;width:100%;min-width:0;max-width:100%;\}/);
  assert.match(phone, /\.adm5-main #sec-cola \.queue-controls \.adm-search\{flex:1 1 0;width:auto;\}/);
  assert.doesNotMatch(QUEUE_CSS, /queue-doctors-table|container:queue-doctors/);
  assert.doesNotMatch(QUEUE_RUNTIME, /queue-doctors-table/);
  assert.match(QUEUE_RUNTIME, /<td data-col="medico" data-label="' \+ esc\(lbl\)/);
});
