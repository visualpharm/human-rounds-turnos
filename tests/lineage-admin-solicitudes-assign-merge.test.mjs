import test from 'node:test';
import assert from 'node:assert/strict';
import {adminRuntime, plain, flush} from './lineage/runtime.mjs';
const before = {id:'r1',estado:'pendiente',turno:null,appointment_at:null,medico_asignado:'Old Doctor',medico_asignado_id:'old',paciente:'Old Name'};
const fresh = {...before,estado:'aprobada',turno:'2026-09-17T09:00:00-03:00',appointment_at:'2026-09-17T09:00:00-03:00',medico_asignado:'New Doctor',medico_asignado_id:'new',paciente:'Corrected Name'};
for (const action of ['assign','approve']) test(`lineage: ${action} merges the complete authoritative request`, async () => {
  const server = {row:fresh,slots:[]};
  const f = adminRuntime(before, server);
  if (action === 'approve') {
    assert.equal((await f.context.executePending(f.context.ALL[0], 'aprobar')).ok, true);
  } else {
    const card = {dataset:{id:'r1'},querySelector:()=>null};
    const select = {value:'New Doctor',closest: selector => selector === '.med-sel' ? select : selector === '.card' ? card : null};
    f.listeners.get('change')({target:select}); await flush(); await flush();
  }
  assert.ok(f.requests.includes(`/api/admin/requests/r1/${action}`));
  // Independent GET boundary returns the canonical row after the mutation.
  const get = async () => new Response(JSON.stringify({solicitudes:[server.row]}));
  const row = (await (await get()).json()).solicitudes.find(x => x.id === 'r1');
  assert.deepEqual(plain(f.context.ALL[0]), row, 'admin-app.js: mutation must reconcile all request fields');
});
