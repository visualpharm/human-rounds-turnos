import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, click, flush } from './helpers/queue-runtime-fixture.mjs';


test('real mount removes document listeners on disposal, not merely their effects', async t => {
  const f = fixture(t), owner = f.mount();
  assert.ok(f.doc.count() >= 4, 'positive: live controller registers its delegates');
  owner();
  assert.equal(f.doc.count(), 0);
  assert.equal(f.win.count(), 0);
  f.readyTable(); await owner.ready;
});

function typeSearch(f, value) {
  const input = { value, closest(selector) { return selector === '[data-cola-buscar]' ? input : null; } };
  f.doc.activeElement = input;
  f.doc.emit('input', { target: input });
}
function readableQueue(f) {
  const section = { style: {}, innerHTML: '<p>Server-rendered queue</p>' };
  f.nodes.set('sec-cola', section);
  return section;
}
test('typed date invalidates an old response before debounce fires and keeps SSR readable', async t => {
  const f = fixture(t), confirmations = [], section = readableQueue(f);
  f.modules.set('platform/admin-session.mjs', Promise.resolve({ confirmar() { confirmations.push(true); return true; } }));
  const owner = f.mount(); f.readyTable(); await flush();
  assert.equal(f.requests.length, 1);
  typeSearch(f, '2026-09-10');
  assert.equal(f.requests[0].options.signal.aborted, true, 'intent, not timer expiry, invalidates old day');
  f.requests[0].resolve(new Response('{}', { status: 401 })); await flush(); await flush();
  assert.deepEqual(confirmations, []);
  assert.equal(section.innerHTML, '<p>Server-rendered queue</p>');
  assert.equal(f.requests.length, 1);
  f.tick(260);
  assert.equal(f.requests.length, 2);
  assert.equal(f.requests[1].url, '/api/admin/day-queue?dia=2026-09-10');
});
test('initial readiness follows the current date after an aborting transport is superseded', async t => {
  const f = fixture(t), section = readableQueue(f), outcomes = [];
  const owner = f.mount();
  owner.ready.then(() => outcomes.push('ready'), error => outcomes.push(error.message));
  f.readyTable(); await flush();
  typeSearch(f, '2026-09-10');
  f.requests[0].reject(new DOMException('Aborted', 'AbortError'));
  await flush(); assert.deepEqual(outcomes, [], 'old abort neither disposes nor announces readiness for the current load');
  f.tick(260); assert.equal(f.requests.length, 2);
  f.requests[1].resolve(new Response(JSON.stringify({ dia: '2026-09-10', grupos: [] })));
  await owner.ready; await flush();
  assert.deepEqual(outcomes, ['ready']);
  assert.match(section.innerHTML, /No hay turnos aprobados/);
  assert.ok(f.doc.count() > 0, 'current controller remains live after takeover');
});
// Contraparte técnica, 2026-10-27: «nunca entendí cómo… llegábamos a esa pantalla». The
// day's answer says where the desk installs the HSI extension; the queue draws
// that one line above the day, and nothing when the answer carries none.
test('«Pacientes de hoy» draws where to install the HSI extension from the day answer', async t => {
  const f = fixture(t), section = readableQueue(f);
  const owner = f.mount(); f.readyTable(); await flush();
  f.requests[0].resolve(new Response(JSON.stringify({ dia: '2026-09-09', grupos: [],
    hsi_instalar: { tienda: null, zip: '/api/admin/hsi-extension/extension.zip', guia: '/api/admin/hsi-extension/guide.pdf' } })));
  await owner.ready; await flush();
  assert.match(section.innerHTML, /<p class="hsi-instalar" id="queue-hsi-instalar">\/api\/admin\/hsi-extension\/guide\.pdf<\/p>/);
  f.streams[0].emit('ready'); assert.equal(f.requests.length, 2);
  f.requests[1].resolve(new Response(JSON.stringify({ dia: '2026-09-09', grupos: [], hsi_instalar: null })));
  await flush(); await flush();
  assert.doesNotMatch(section.innerHTML, /hsi-instalar/);
});
test('clearing a date cancels its scheduled navigation and disposal clears all timers', async t => {
  const f = fixture(t); readableQueue(f);
  const owner = f.mount(); f.readyTable(); await flush();
  typeSearch(f, '2026-09-10'); assert.equal(f.timers.size, 1);
  typeSearch(f, '');
  assert.equal(f.timers.size, 0);
  const count = f.requests.length; f.tick(260); assert.equal(f.requests.length, count);
  assert.ok(f.requests.every(request => !request.url.includes('2026-09-10')));
  typeSearch(f, '2026-09-11'); assert.equal(f.timers.size, 1);
  owner(); assert.equal(f.timers.size, 0);
});
test('obsolete live announcements are cancelled but a fresh owner still announces current errors', async t => {
  const f = fixture(t), live = { textContent: 'previous announcement' };
  f.nodes.set('cola-live', live);
  const first = f.mount(); f.readyTable(); await first.ready;
  click(f, '[data-asis]', { 'data-asis': 'synthetic-row', 'data-valor': 'presente' });
  f.requests[0].resolve(new Response('{}', { status: 409 })); await flush();
  assert.equal(f.timers.size, 1); assert.equal(live.textContent, '');
  first(); assert.equal(f.timers.size, 0); f.tick(30); assert.equal(live.textContent, '');
  const second = f.mount(); await second.ready;
  click(f, '[data-asis]', { 'data-asis': 'synthetic-row', 'data-valor': 'presente' });
  f.requests[1].resolve(new Response('{}', { status: 409 })); await flush(); f.tick(30);
  assert.match(live.textContent, /futuro/);
});
test('session import failure rejects native readiness and releases all delegates', async t => {
  const f = fixture(t);
  let rejectSession;
  f.modules.set('platform/admin-session.mjs', new Promise((_resolve, reject) => { rejectSession = reject; }));
  const owner = f.mount();
  const rejected = assert.rejects(owner.ready, /session unavailable/);
  f.readyTable(); rejectSession(new Error('session unavailable'));
  await rejected;
  assert.equal(f.doc.count(), 0); assert.equal(f.win.count(), 0);
});
test('SSE cannot supersede a pending date and coalesces refresh without starving a load', async t => {
  const f = fixture(t); readableQueue(f);
  const owner = f.mount(); f.readyTable(); await flush();
  f.requests[0].resolve(new Response(JSON.stringify({ dia: '2026-09-09', grupos: [] })));
  await owner.ready;
  typeSearch(f, '2026-09-10');
  f.streams[0].emit('ready'); assert.equal(f.requests.length, 1, 'SSE must not restart old day during debounce');
  f.tick(260); assert.equal(f.requests.length, 2);
  for (let i = 0; i < 5; i++) f.streams[0].emit('ready');
  assert.equal(f.requests.length, 2, 'events coalesce while current transport is pending');
  assert.equal(f.requests[1].options.signal.aborted, false);
  f.requests[1].resolve(new Response('{}', { status: 503 })); await flush(); await flush();
  assert.equal(f.requests.length, 3, 'exactly one queued refresh follows completion');
  assert.equal(f.requests[2].url, '/api/admin/day-queue?dia=2026-09-10', 'never refresh previous rendered day');
});


test('disposed attendance response cannot confirm a session or touch the new screen', async t => {
  const f = fixture(t), confirmations = [];
  f.modules.set('platform/admin-session.mjs', Promise.resolve({ confirmar(...args) { confirmations.push(args); return false; } }));
  const owner = f.mount(); f.readyTable(); await owner.ready;
  const button = click(f, '[data-asis]', { 'data-asis': 'synthetic-row', 'data-valor': 'presente' });
  assert.equal(button.disabled, true);
  assert.equal(f.requests.length, 1);
  owner();
  f.requests[0].resolve(new Response('{"error":"unauthorized"}', { status: 401 }));
  await flush(); await flush();
  assert.equal(confirmations.length, 0);
  assert.deepEqual(f.toasts, []);
  assert.equal(f.requests[0].options.signal.aborted, true);
});
test('late attendance body after disposal cannot show a stale failure', async t => {
  const f = fixture(t), owner = f.mount(); f.readyTable(); await owner.ready;
  click(f, '[data-asis]', { 'data-asis': 'synthetic-row', 'data-valor': 'presente' });
  let releaseBody;
  f.requests[0].resolve({ ok: false, status: 409, json: () => new Promise(resolve => { releaseBody = resolve; }) });
  await flush(); owner(); releaseBody({ error: 'old error' });
  await flush(); await flush();
  assert.deepEqual(f.toasts, []);
});
test('date intent cancels old attendance ownership before debounce and releases old controls', async t => {
  const f = fixture(t), confirmations = [];
  f.modules.set('platform/admin-session.mjs', Promise.resolve({ confirmar() { confirmations.push(true); return true; } }));
  const owner = f.mount(); f.readyTable(); await owner.ready;
  const button = click(f, '[data-asis]', { 'data-asis': 'synthetic-row', 'data-valor': 'presente' });
  assert.equal(button.disabled, true);
  typeSearch(f, '2026-09-10');
  assert.equal(f.requests[0].options.signal.aborted, true);
  assert.equal(button.disabled, false, 'retracting date navigation must not leave old controls disabled');
  f.requests[0].resolve(new Response('{}', { status: 401 })); await flush(); await flush();
  assert.deepEqual(confirmations, []);
  assert.deepEqual(f.toasts, []);
});
test('attendance response parsing from a previous date cannot announce an obsolete failure', async t => {
  const f = fixture(t), owner = f.mount(); f.readyTable(); await owner.ready;
  click(f, '[data-asis]', { 'data-asis': 'synthetic-row', 'data-valor': 'presente' });
  let releaseBody;
  f.requests[0].resolve({ ok: false, status: 409, json: () => new Promise(resolve => { releaseBody = resolve; }) });
  await flush(); typeSearch(f, '2026-09-10'); releaseBody({ error: 'previous date failure' });
  await flush(); await flush();
  assert.deepEqual(f.toasts, []);
});
test('changing date leaves installation-wide intake settings owned by the current screen', async t => {
  const f = fixture(t), owner = f.mount(); f.readyTable(); await owner.ready;
  click(f, '[data-entrevista-toggle]', { 'data-entrevista-toggle': '1' });
  typeSearch(f, '2026-09-10');
  assert.equal(f.requests[0].options.signal.aborted, false);
  f.requests[0].reject(new Error('current settings failure')); await flush();
  assert.equal(f.toasts.length, 1);
});
test('new day data invalidates attendance started while its load was pending', async t => {
  const f = fixture(t), confirmations = [];
  f.modules.set('platform/admin-session.mjs', Promise.resolve({ confirmar() { confirmations.push(true); return true; } }));
  const owner = f.mount(); f.readyTable(); await owner.ready;
  assert.equal(f.requests.length, 0, 'no section in this resource fixture: route does not start a load');
  typeSearch(f, '2026-09-10'); f.tick(260);
  click(f, '[data-asis]', { 'data-asis': 'synthetic-row', 'data-valor': 'presente' });
  assert.equal(f.requests.length, 2);
  assert.equal(f.requests[0].url, '/api/admin/day-queue?dia=2026-09-10');
  assert.equal(f.requests[1].url, '/api/admin/requests/synthetic-row/attendance');
  f.requests[0].resolve(new Response(JSON.stringify({ dia: '2026-09-10', grupos: [] })));
  await flush(); await flush();
  assert.equal(f.requests[1].options.signal.aborted, true);
  f.requests[1].resolve(new Response('{}', { status: 401 })); await flush(); await flush();
  assert.deepEqual(confirmations, []);
  assert.deepEqual(f.toasts, []);
});
test('held session dependency cannot redirect after attendance owner is replaced', async t => {
  const f = fixture(t), confirmations = [];
  let releaseSession;
  f.modules.set('platform/admin-session.mjs', new Promise(resolve => { releaseSession = resolve; }));
  const first = f.mount(); f.readyTable();
  click(f, '[data-asis]', { 'data-asis': 'synthetic-row', 'data-valor': 'presente' });
  f.requests[0].resolve(new Response('{"error":"2fa_requerido"}', { status: 403 }));
  await flush(); first();
  const second = f.mount();
  releaseSession({ confirmar(...args) { confirmations.push(args); return true; } });
  await Promise.all([first.ready, second.ready]);
  await flush(); await flush();
  assert.deepEqual(confirmations, []);
  assert.deepEqual(f.toasts, []);
});
test('current attendance failure still announces recovery and unlocks its controls', async t => {
  const f = fixture(t), owner = f.mount(); f.readyTable(); await owner.ready;
  const button = click(f, '[data-asis]', { 'data-asis': 'synthetic-row', 'data-valor': 'presente' });
  f.requests[0].resolve(new Response('{}', { status: 409 })); await flush(); await flush();
  assert.equal(button.disabled, false);
  assert.equal(f.toasts.length, 1);
  assert.match(f.toasts[0], /futuro/);
});
test('disposed intake mutation rejection is silent while a fresh mutation still reports failure', async t => {
  const f = fixture(t), first = f.mount(); f.readyTable(); await first.ready;
  click(f, '[data-entrevista-toggle]', { 'data-entrevista-toggle': '1' });
  first(); const second = f.mount(); await second.ready;
  f.requests[0].reject(new Error('old network failure')); await flush();
  assert.deepEqual(f.toasts, []);
  click(f, '[data-entrevista-toggle]', { 'data-entrevista-toggle': '0' });
  f.requests[1].reject(new Error('current network failure')); await flush();
  assert.equal(f.toasts.length, 1);
});
test('latest intake mutation supersedes an older response even if transport ignores abort', async t => {
  const f = fixture(t), owner = f.mount(); f.readyTable(); await owner.ready;
  click(f, '[data-entrevista-toggle]', { 'data-entrevista-toggle': '1' });
  click(f, '[data-entrevista-toggle]', { 'data-entrevista-toggle': '0' });
  assert.equal(f.requests.length, 2);
  assert.equal(f.requests[0].options.signal.aborted, true);
  f.requests[1].resolve(new Response('{"error":"current validation"}', { status: 400 }));
  await flush(); await flush();
  assert.deepEqual(f.toasts, ['No se pudo guardar.']);
  f.requests[0].resolve(new Response('{"error":"old validation"}', { status: 400 }));
  await flush(); await flush();
  assert.deepEqual(f.toasts, ['No se pudo guardar.']);
});
test('pending create module cannot open after its queue was disposed; fresh owner can open', async t => {
  const f = fixture(t), opened = [];
  let releaseCreate;
  f.modules.set('platform/admin-create-appointment.mjs', new Promise(resolve => { releaseCreate = resolve; }));
  const first = f.mount(); f.readyTable(); await first.ready;
  click(f, '#adm-crear-turno'); first();
  releaseCreate({ abrir(pre) { opened.push(pre); } }); await flush();
  assert.equal(opened.length, 0);
  const second = f.mount(); await second.ready; click(f, '#adm-crear-turno'); await flush();
  assert.deepEqual(opened, [{}]);
});
test('failed pending create import cannot attach a recovery message to a disposed queue', async t => {
  const f = fixture(t);
  let rejectCreate;
  f.modules.set('platform/admin-create-appointment.mjs', new Promise((_resolve, reject) => { rejectCreate = reject; }));
  const owner = f.mount(); f.readyTable(); await owner.ready;
  click(f, '#adm-crear-turno'); owner();
  rejectCreate(new Error('late import failure')); await flush();
  assert.deepEqual(f.toasts, []);
});
test('late table dependency cannot create resources or inject CSS for a disposed owner', async t => {
  const f = fixture(t), owner = f.mount();
  owner(); f.readyTable(); await owner.ready;
  assert.equal(f.tables.length, 0);
  assert.equal(f.styles.length, 0);
});
test('controllers leave presentation ownership to the document across mount and disposal', async t => {
  const f = fixture(t), first = f.mount({}), second = f.mount({});
  f.readyTable(); await Promise.all([first.ready, second.ready]);
  assert.equal(f.styles.length, 0, 'the template loads the fingerprinted CSS independently of enhancement');
  first(); second();
  assert.equal(f.styles.length, 0, 'controller teardown must not restyle the still-readable document');
});
test('disposal never deletes an existing stylesheet supplied by another owner', async t => {
  const f = fixture(t), foreign = { remove() { assert.fail('foreign stylesheet removed'); } };
  f.nodes.set('cola-css', foreign);
  const owner = f.mount(); f.readyTable(); await owner.ready;
  owner(); assert.equal(f.nodes.get('cola-css'), foreign);
});
test('same-root remount releases the prior owner and retains one fresh table', async t => {
  const f = fixture(t), first = f.mount(), count = f.doc.count(), second = f.mount();
  assert.equal(f.doc.count(), count, 'delegates must not accumulate');
  f.readyTable(); await Promise.all([first.ready, second.ready]);
  assert.equal(f.tables.length, 1, 'only the still-current owner creates its table');
  first(); assert.equal(f.tables[0].disposals, 0, 'old disposer cannot tear down new owner');
  second(); second();
  assert.equal(f.tables[0].disposals, 1);
  assert.equal(f.doc.count(), 0);
});
test('real pagehide releases current document owner', async t => {
  const f = fixture(t), owner = f.mount(); f.readyTable(); await owner.ready;
  assert.equal(f.tables.length, 1);
  f.win.emit('pagehide');
  assert.equal(f.tables[0].disposals, 1);
  assert.equal(f.doc.count(), 0);
  assert.equal(f.win.count(), 0);
});
test('failed table dependency rejects readiness and cleans up delegates', async t => {
  const f = fixture(t), owner = f.mount();
  const rejected = assert.rejects(owner.ready, /table unavailable/);
  f.rejectTable(new Error('table unavailable')); await rejected; await flush();
  assert.equal(f.doc.count(), 0);
  assert.equal(f.win.count(), 0);
});
test('current queue starts SSE and remount closes and unbinds its old stream', async t => {
  const f = fixture(t);
  f.nodes.set('sec-cola', { style: {}, innerHTML: '<p>Server-rendered queue remains readable</p>' });
  const first = f.mount(); f.readyTable(); await flush();
  assert.equal(f.streams.length, 1);
  assert.equal(f.streams[0].url, '/api/admin/day-queue/events');
  f.requests[0].resolve(new Response(JSON.stringify({ dia: '2026-09-09', grupos: [] })));
  await first.ready;
  const before = f.requests.length;
  f.streams[0].emit('ready');
  assert.equal(f.requests.length, before + 1, 'positive: current SSE starts a scoped refresh');
  const second = f.mount(); await flush();
  assert.equal(f.streams[0].closes, 1);
  assert.equal(f.streams[0].count(), 0, 'closed streams must release their listeners too');
  assert.equal(f.streams.length, 2);
  const count = f.requests.length;
  f.streams[0].emit('ready'); assert.equal(f.requests.length, count);
  f.streams[1].emit('ready'); assert.equal(f.requests.length, count, 'fresh stream coalesces while its initial load is pending');
  second(); assert.equal(f.streams[1].closes, 1); assert.equal(f.streams[1].count(), 0);
  assert.equal(f.requests.at(-1).options.signal.aborted, true);
});
test('a day-queue 401 the session check does not confirm shows the load error, never an empty screen', async t => {
  const f = fixture(t), section = { style: {}, innerHTML: '' };
  f.nodes.set('sec-cola', section);
  const owner = f.mount(); f.readyTable(); await flush();
  owner.ready.catch(() => {});
  f.requests[0].resolve(new Response('{"error":"no_autenticado"}', { status: 401 })); await flush(); await flush();
  assert.match(section.innerHTML, /No se pudo cargar/);
});
test('an unconfirmed 401 on attendance frees the buttons and says it was not saved', async t => {
  const f = fixture(t);
  const owner = f.mount(); f.readyTable(); await owner.ready;
  const button = click(f, '[data-asis]', { 'data-asis': 'synthetic-row', 'data-valor': 'presente' });
  f.requests[0].resolve(new Response('{"error":"no_autenticado"}', { status: 401 }));
  await flush(); await flush();
  assert.equal(button.disabled, false);
  assert.deepEqual(f.toasts, ['No se pudo guardar.']);
});
