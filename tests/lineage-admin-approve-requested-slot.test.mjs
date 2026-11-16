import test from 'node:test';
import assert from 'node:assert/strict';
import {runtime, flush} from './lineage/runtime.mjs';

// Journey 2026-09-16: a patient who picked Thu 17 09:00 in the wizard got
// booked at 08:30 because the panel preselected the first free slot. Approving
// without touching the time dropdown must book the time the patient asked for;
// if that time is gone, nothing is preselected and Aprobar asks for a time.
const PEDIDO = {dia: '2026-09-17', hora: '09:00'};
// The patient's own hold keeps 09:00 out of the desk slots (/api/admin/appointment-slots).
const SLOTS = [{dia: '2026-09-17', hora: '07:00'}, {dia: '2026-09-17', hora: '08:30'}];

// `opts.doctor`: the card a doctor sees — no service picker, no doctor picker
// (critical review 2026-11-08, F1). `opts.fetch` / `opts.timers` replace the
// agenda endpoint and setTimeout.
function fixture(row, opts = {}){
  const approves = [], asked = [];
  const el = (props = {}) => ({value: '', hidden: false, innerHTML: '', dataset: {},
    setAttribute() {}, removeAttribute() {}, ...props});
  const nodes = {
    '.turno-sel': el(), '.svc-sel': opts.doctor ? null : el({value: row.servicio_id}),
    '.med-sel': opts.doctor ? null : el({value: 'Doc'}),
    '.turno': el(), '.turno-free': el({hidden: true}), '.sobreturno': null,
    '[data-act="aprobar"]': el({disabled: true})
  };
  const card = {nodeType: 1, isConnected: true, dataset: {id: 'r1'}, querySelector: sel => nodes[sel] ?? null};
  const agenda = opts.fetch || (async () => new Response(JSON.stringify({libres: SLOTS.map(t => ({...t, medico: 'Doc'}))})));
  const context = runtime('static/root/admin-app.js',
    ['turnoDefault', 'defaultsDe', 'medicoPorDefecto','executePending', 'poblarTurnoSel', 'slotsPara', 'turnoPreferido', 'agendaMarcar', 'agendaReintentar', 'svcDeTarjeta'], {
      ALL: [structuredClone(row)], EMR_SYNC_ACTIVE: false, CURRENT_ROLE: 'admin', recordarAprobada() {},
      toIsoWithOffset: x => x, esc: String, fmtTurno: String, toast() {},
      prefGeneral: () => '', nextWeekday9: () => '2026-09-18T09:00', esLab: () => false,
      medicosDe: () => [{nombre: 'Doc'}], defaultMotivo: () => '', approveError: () => 'err',
      fetch: async url => { asked.push(url); return agenda(url); },
      setTimeout: (fn, ms) => { (opts.timers || []).push({fn, ms}); },
      api: async (url, body) => {
        approves.push({url, body: JSON.parse(body.body)});
        return new Response(JSON.stringify({solicitud: {estado: 'aprobada'}}));
      }
    }, 'const SLOTS_CACHE = new Map(); let AGENDA_TOKEN = 0;');
  return {context, card, nodes, approves, asked};
}

test("a doctor's card (no service picker) asks the request's own service and can approve", async () => {
  const f = fixture({id: 'r1', servicio_id: 'svc', turno_pedido: {...PEDIDO, libre: true},
    medico_sugerido: {nombre: 'Doc', slot_iso: '2026-09-17T09:00:00-03:00', pedido: true}}, {doctor: true});
  await f.context.poblarTurnoSel(f.card); await flush();
  assert.equal(f.asked.length, 1);
  assert.equal(new URL(f.asked[0], 'http://x').searchParams.get('servicio_id'), 'svc',
    'the agenda is asked for the request service, never servicio_id=');
  assert.equal(f.nodes['[data-act="aprobar"]'].disabled, false, 'Aprobar is on once the agenda arrived');
  assert.equal(f.nodes['.turno'].value, '2026-09-17T09:00');
  const r = await f.context.executePending(f.card, 'aprobar');
  assert.equal(r.ok, true);
  assert.equal(f.approves[0].body.servicio_id, 'svc');
});

test('a failed agenda stays «Cargando horarios…» and asks again by itself, with growing pauses', async () => {
  const timers = [];
  let calls = 0;
  const f = fixture({id: 'r1', servicio_id: 'svc', turno_pedido: {...PEDIDO, libre: true},
    medico_sugerido: {nombre: 'Doc', slot_iso: '2026-09-17T09:00:00-03:00', pedido: true}}, {timers,
    fetch: async () => (++calls <= 2 ? new Response('{}', {status: 500})
      : new Response(JSON.stringify({libres: SLOTS.map(t => ({...t, medico: 'Doc'}))})))});
  await f.context.poblarTurnoSel(f.card); await flush();
  assert.match(f.nodes['.turno-sel'].innerHTML, /Cargando horarios/);
  assert.equal(f.nodes['.turno-sel'].dataset.agenda, 'cargando');
  assert.equal(f.nodes['[data-act="aprobar"]'].disabled, true);
  assert.equal(timers.length, 1);
  timers.shift().fn(); await flush(); await flush();
  assert.equal(timers.length, 1, 'the second failure schedules one more attempt');
  const second = timers.shift();
  assert.ok(second.ms >= 1000);
  second.fn(); await flush(); await flush();
  assert.equal(calls, 3);
  assert.equal(timers.length, 0);
  assert.equal(f.nodes['[data-act="aprobar"]'].disabled, false, 'the agenda arrived: Aprobar is on');
  assert.equal(f.nodes['.turno'].value, '2026-09-17T09:00');
});

test('approve without touching the dropdown books the requested slot', async () => {
  const f = fixture({id: 'r1', servicio_id: 'svc', turno_pedido: {...PEDIDO, libre: true},
    medico_sugerido: {nombre: 'Doc', slot_iso: '2026-09-17T09:00:00-03:00', pedido: true}});
  await f.context.poblarTurnoSel(f.card); await flush();
  assert.equal(f.nodes['.turno'].value, '2026-09-17T09:00', 'requested slot is preselected');
  assert.match(f.nodes['.turno-sel'].innerHTML, /value="2026-09-17T09:00" selected/);
  const r = await f.context.executePending(f.card, 'aprobar');
  assert.equal(r.ok, true);
  assert.equal(f.approves.length, 1);
  assert.equal(f.approves[0].body.turno_dt, '2026-09-17T09:00');
});

test('a requested slot that is already taken preselects nothing', async () => {
  const f = fixture({id: 'r1', servicio_id: 'svc', turno_pedido: {...PEDIDO, libre: false},
    medico_sugerido: {nombre: 'Doc', slot_iso: '2026-09-17T07:00:00-03:00'}});
  assert.equal(f.context.turnoDefault(f.context.ALL[0]), '');
  await f.context.poblarTurnoSel(f.card); await flush();
  assert.equal(f.nodes['.turno'].value, '');
  assert.doesNotMatch(f.nodes['.turno-sel'].innerHTML, /value="2026-09-17T0[78]:\d\d" selected/);
  const r = await f.context.executePending(f.card, 'aprobar');
  assert.equal(r.ok, false);
  assert.equal(f.approves.length, 0, 'nothing is booked without an explicit time');
});
