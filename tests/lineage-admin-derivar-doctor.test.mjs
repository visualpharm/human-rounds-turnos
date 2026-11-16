import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runtime} from './lineage/runtime.mjs';

// Journey 2026-09-16: Medicina general → Derivar → Odontología. The header kept
// "Medicina general", the doctor combo read "a asignar", and Aprobar still sent
// the old service's doctor (Jorge García).
const ROW = {id: 'r1', servicio_id: 'medicina-general', medico_asignado: 'Jorge García',
  medico_sugerido: {nombre: 'Jorge García', slot_iso: '2026-09-17T09:00:00-03:00'}};
const MEDICOS = {'medicina-general': [{nombre: 'Jorge García'}], 'odontologia': [{nombre: 'Ana Muela'}]};

function fixture(){
  const approves = [];
  const node = (props = {}) => ({value: '', hidden: false, textContent: '', dataset: {}, style: {}, ...props});
  const cmbIn = node({value: 'Jorge García'});
  const medBox = node({dataset: {disp: 'Jorge García'}, querySelector: s => s === '.cmb-in' ? cmbIn : null});
  const nodes = {
    '.svc-sel': node({value: 'odontologia'}),
    '.med-sel': node({value: 'Jorge García', closest: s => s === '.cmb' ? medBox : null}),
    '.turno': node({value: '2026-09-17T10:00'}),
    '.sol-pide-line1 a': node({textContent: 'Medicina general'}),
    '.sobreturno': null
  };
  const card = {nodeType: 1, dataset: {id: 'r1'}, querySelector: s => nodes[s] ?? null};
  const context = runtime('static/root/admin-app.js', ['defaultsDe', 'medicoPorDefecto', 'turnoDefault','executePending', 'aplicarDerivacion'], {
    ALL: [structuredClone(ROW)], EMR_SYNC_ACTIVE: false, CURRENT_ROLE: 'admin', recordarAprobada() {},
    toIsoWithOffset: x => x, esc: String, fmtTurno: String, toast() {}, nextWeekday9: () => '',
    esLab: () => false, medicosDe: svc => MEDICOS[svc] || [], defaultMotivo: () => '',
    approveError: () => 'err', svcNombre: id => ({odontologia: 'Odontología general'})[id] || id,
    cenNombre: String, SVC_LIST: [{id: 'odontologia', centro: 'caps'}],
    api: async (url, opts) => {
      approves.push(JSON.parse(opts.body));
      return new Response(JSON.stringify({solicitud: {estado: 'aprobada'}}));
    }
  }, 'const SLOTS_CACHE = new Map();');
  return {context, card, nodes, approves};
}

test('Derivar updates the header and drops the old service doctor', () => {
  const f = fixture();
  f.context.aplicarDerivacion(f.card, 'odontologia');
  assert.equal(f.nodes['.sol-pide-line1 a'].textContent, 'Odontología general');
  assert.equal(f.nodes['.med-sel'].value, '');
});

// The line under the service names the center the request goes to. The panel has
// no `.svc-current` any more; Derivar wrote the new center there and the desk kept
// reading the old one (design review 2026-11-04, C10). Only the center changes:
// the slot and the doctor the patient asked for stay on the line.
test('Derivar shows the center of the new service, not the request\'s old one', () => {
  const f = fixture();
  f.nodes['.sol-pide-line2 .sol-pide-centro'] = {textContent: 'Hospital de Pinamar'};
  f.context.aplicarDerivacion(f.card, 'odontologia');
  assert.equal(f.nodes['.sol-pide-line2 .sol-pide-centro'].textContent, 'caps');
});

test('Derivar with no line under the service does not fail', () => {
  const f = fixture();
  assert.doesNotThrow(() => f.context.aplicarDerivacion(f.card, 'odontologia'));
  assert.equal(f.nodes['.sol-pide-line1 a'].textContent, 'Odontología general');
});

test('the panel no longer looks for the retired .svc-current', () => {
  const APP = readFileSync(new URL('../static/root/admin-app.js', import.meta.url), 'utf8');
  assert.equal(APP.includes('svc-current'), false, 'admin-app.js still reads .svc-current');
});

test('approval after Derivar never sends the old service doctor', async () => {
  const f = fixture();
  f.context.aplicarDerivacion(f.card, 'odontologia');
  const r = await f.context.executePending(f.card, 'aprobar');
  assert.equal(r.ok, true);
  assert.equal(f.approves[0].servicio_id, 'odontologia');
  assert.equal(f.approves[0].medico, undefined, 'no doctor picked → none sent');
});
