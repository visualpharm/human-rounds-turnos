import test from 'node:test';
import assert from 'node:assert/strict';
import {runtime} from './lineage/runtime.mjs';

// Mapa del hospital (desk.json d10, problems.md №2, 2026-10-23): una solicitud
// de Ecocardiograma sin cardiólogo cargado ofrecía como médico por defecto al
// traumatólogo — medicosDe() devolvía TODO el catálogo cuando la especialidad
// no encontraba a nadie, y el default tomaba el primero con turno libre. La
// mesa apretaba «Aprobar» y el paciente quedaba con el médico equivocado.
// La lista de médicos de un servicio es el POOL que manda el servidor
// (schedule_policy.belongs, la misma regla que usa el paciente al reservar).
const MED_LIST = [
  {nombre: 'Tomás Hueso', especialidad: 'Traumatólogo', href: '/doctor/tomas-hueso',
   proximo: {v: 'mañana 8:00', sort: '2026-10-24T08:00', hora: '08:00'}},
  {nombre: 'Olga Muela', especialidad: 'Odontóloga', href: '/doctor/olga-muela',
   proximo: {v: 'mañana 9:00', sort: '2026-10-24T09:00', hora: '09:00'}},
  {nombre: 'Clara Latido', especialidad: 'Cardióloga', href: '/doctor/clara-latido',
   proximo: {v: 'lunes 10:00', sort: '2026-10-26T10:00', hora: '10:00'}}
];
const SVC = {ecocardio: 'Ecocardiograma', cardio: 'Cardiología', trauma: 'Traumatología'};
const SVC_LIST = Object.entries(SVC).map(([id, nombre]) => ({id, nombre, centro: 'hospital'}));
// Lo que /api/admin/requests entrega: Ecocardiograma no tiene a nadie.
const MED_POOL = {ecocardio: [], cardio: ['clara-latido'], trauma: ['tomas-hueso']};

function ctx(pool = MED_POOL, extra = {}){
  return runtime('static/root/admin-app.js',
    ['medIdDe', 'medicosDe', 'medicoPorDefecto', 'medVacioTxt', 'defaultsDe', 'turnoDefault',
     'aplicarDerivacion'], {
      MED_LIST, SVC, SVC_LIST, MED_POOL: pool, ALL: [],
      esLab: () => false, defaultMotivo: () => '', nextWeekday9: () => '', fmtTurno: String,
      svcNombre: id => SVC[id] || id, cenNombre: String,
      T: (_k, vars, fallback) => String(fallback).replace(/\{(\w+)\}/g, (_, k) => (vars || {})[k] ?? ''),
      ...extra
    });
}

// Una tarjeta mínima: el combo de médico y la pista «no hay médicos de…».
function card(){
  const node = (props = {}) => ({value: '', hidden: false, textContent: '', dataset: {}, style: {}, ...props});
  const medBox = node({querySelector: () => null});
  const nodes = {
    '.med-sel': node({closest: s => s === '.cmb' ? medBox : null}),
    '.med-vacio': node({hidden: true})
  };
  return {nodes, medBox, el: {dataset: {id: 'r1'}, querySelector: s => nodes[s] ?? null}};
}

test('derivar a un servicio sin médicos vacía el combo y muestra la pista; con médicos, la esconde', () => {
  const c = ctx(), k = card();
  c.aplicarDerivacion(k.el, 'ecocardio');
  assert.equal(k.nodes['.med-vacio'].hidden, false);
  assert.match(k.nodes['.med-vacio'].textContent, /No hay médicos de Ecocardiograma/);
  assert.deepEqual(JSON.parse(k.medBox.dataset.items), []);
  c.aplicarDerivacion(k.el, 'cardio');
  assert.equal(k.nodes['.med-vacio'].hidden, true);
  assert.deepEqual(JSON.parse(k.medBox.dataset.items).map(x => x.v), ['Clara Latido']);
});

test('el sugerido por el servidor (del mismo pool) se respeta como default', () => {
  const c = ctx();
  const d = c.defaultsDe({id: 1, servicio_id: 'cardio',
    medico_sugerido: {nombre: 'Clara Latido', pedido: true, slot_iso: '2026-10-26T10:00:00-03:00'}});
  assert.equal(d.medico, 'Clara Latido');
});

test('un servicio sin médicos de su especialidad no ofrece médicos de otra', () => {
  const c = ctx();
  assert.deepEqual([...c.medicosDe('ecocardio')].map(m => m.nombre), []);
});

test('sin médicos del servicio no hay médico preseleccionado (queda «a asignar»)', () => {
  const c = ctx();
  const d = c.defaultsDe({id: 1, servicio_id: 'ecocardio'});
  assert.equal(d.medico, '');
});

test('un médico asignado de otra especialidad no se preselecciona', () => {
  const c = ctx();
  const d = c.defaultsDe({id: 1, servicio_id: 'ecocardio', medico_asignado: 'Tomás Hueso'});
  assert.equal(d.medico, '');
});

test('con médicos del servicio, la lista y el default salen del pool', () => {
  const c = ctx();
  assert.deepEqual([...c.medicosDe('cardio')].map(m => m.nombre), ['Clara Latido']);
  assert.equal(c.defaultsDe({id: 1, servicio_id: 'cardio'}).medico, 'Clara Latido');
});

// problems.md №3 (2026-10-23): el médico asignado se reconoce por id — una
// fila con el nombre viejo de un médico renombrado sigue siendo suya y el
// combo lo preselecciona con el nombre del catálogo.
test('el asignado se reconoce por id aunque el nombre guardado sea viejo', () => {
  const c = ctx({...MED_POOL, cardio: ['clara-latido', 'tomas-hueso']});
  const d = c.defaultsDe({id: 1, servicio_id: 'cardio',
    medico_asignado: 'Clara Vieja', medico_asignado_id: 'clara-latido'});
  assert.equal(d.medico, 'Clara Latido');
});

test('sin pool del servidor no se adivina: lista vacía, nunca el catálogo entero', () => {
  const c = ctx(null);
  assert.deepEqual([...c.medicosDe('cardio')], []);
});
