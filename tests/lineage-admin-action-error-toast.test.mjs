import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {runtime, source, flush} from './lineage/runtime.mjs';

// Journey 2026-09-16: a 409 on Aprobar showed the same message twice — once in
// the toast at the top and once inline next to the button. The inline error is
// the one that stays in view; a success still announces itself with a toast.
function fixture(server){
  const toasts = [], loads = [], listeners = new Map(), repobladas = [];
  const node = (props = {}) => ({value: '', hidden: false, textContent: '', checked: false, ...props});
  const nodes = {'.turno': node({value: '2026-09-17T09:00'}), '.svc-sel': node({value: 'svc'}),
    '.med-sel': node({value: 'Doc'}), '.motivo-sel': node({value: 'Orden ilegible'}), '.sobreturno': null,
    '.overturn-ctl': node({hidden: true})};
  const appended = [];
  const fila = {querySelector: s => s === '.sol-act-err' ? (appended[0] || null) : null,
    appendChild: el => appended.push(el)};
  const card = {nodeType: 1, dataset: {id: 'r1'}, querySelector: s => nodes[s] ?? null};
  const context = runtime('static/root/admin-app.js', ['executePending'], {
    ALL: [{id: 'r1', servicio_id: 'svc'}], EMR_SYNC_ACTIVE: false, CURRENT_ROLE: 'admin', recordarAprobada() {},
    defaultsDe: () => ({turno: '', servicio_id: 'svc', medico: 'Doc', motivo: ''}),
    toIsoWithOffset: x => x, approveError: j => j.mensaje, toast: m => toasts.push(m),
    load: async () => { loads.push(1); },
    poblarTurnoSel: (c, o) => repobladas.push(o),
    api: async (url) => new Response(JSON.stringify(server(url)), {status: server(url).status || 200}),
    document: {
      addEventListener: (name, fn) => { if(name === 'click') listeners.set(name, fn); },
      createElement: () => ({className: '', textContent: '', setAttribute() {}, remove() {}})
    }
  }, 'const SLOTS_CACHE = new Map();\n' + source('static/root/admin-app.js').match(/^const TURNO_TOMADO = .*;$/m)[0]);
  const text = source('static/root/admin-app.js');
  const start = text.indexOf("document.addEventListener('click', async (e)=>{");
  const end = text.indexOf('\n});\n', start);
  assert.ok(start >= 0 && end > start, 'action click listener boundary exists');
  vm.runInContext(text.slice(start, end + 4), context, {filename: 'admin-app.js'});
  const click = async act => {
    const b = {dataset: {act}, disabled: false};
    b.closest = s => s === '[data-act]' ? b : s === '.card' ? card : s === '.sol-res-row' ? fila : null;
    await listeners.get('click')({target: {closest: s => s === '[data-act]' ? b : null}});
    await flush();
    return b;
  };
  return {click, toasts, loads, appended, nodes, repobladas};
}

test('an approve error shows inline once, never also as a toast', async () => {
  const f = fixture(() => ({status: 409, error: 'turno_medico_tomado', mensaje: 'Ese horario ya está tomado.'}));
  const b = await f.click('aprobar');
  assert.deepEqual(f.toasts, [], 'the error is not repeated in a toast');
  assert.equal(f.appended.length, 1);
  assert.match(f.appended[0].textContent, /^Ese horario ya está tomado\. /, 'the server message stays, with the next step after it');
  assert.equal(JSON.stringify(f.repobladas), '[{"tras409":"turno_medico_tomado"}]','the agenda is asked again after the 409');
  assert.equal(f.nodes['.overturn-ctl'].hidden, false, 'the overturn control appears next to the error');
  assert.equal(b.disabled, false);
  assert.equal(f.loads.length, 0);
});

test('a successful approve and a successful reject still announce themselves in a toast', async () => {
  const ok = url => ({solicitud: {estado: url.endsWith('/approve') ? 'aprobada' : 'rechazada'}});
  const a = fixture(ok);
  await a.click('aprobar');
  assert.equal(a.toasts.length, 1);
  assert.match(a.toasts[0], /^Aprobada/);
  assert.equal(a.appended.length, 0);
  assert.equal(a.loads.length, 1);
  const r = fixture(ok);
  await r.click('rechazar');
  assert.deepEqual(r.toasts, ['Solicitud rechazada.']);
  assert.equal(r.loads.length, 1);
});
