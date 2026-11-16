import test from 'node:test';
import assert from 'node:assert/strict';
import {adminRuntime} from './lineage/runtime.mjs';
test('lineage: approval invalidates slots before the assignment UI reopens', async () => {
  const server = {row:{id:'r1',estado:'aprobada'},slots:[{dia:'2026-09-17',hora:'09:00'},{dia:'2026-09-17',hora:'10:00'}]};
  const f = adminRuntime({id:'r1',estado:'pendiente'}, server);
  function card() {
    const controls = {'.turno-sel':{innerHTML:'',dataset:{},setAttribute(){},removeAttribute(){}},'.svc-sel':{value:'svc'},'.med-sel':{value:'Doctor One'},'.turno':{value:''},'.turno-free':{value:''}};
    return {dataset:{id:'r1'},querySelector: key => controls[key],controls};
  }
  const first = card(); await f.context.poblarTurnoSel(first);
  assert.match(first.controls['.turno-sel'].innerHTML, /2026-09-17T09:00/);
  assert.equal((await f.context.executePending(f.context.ALL[0], 'aprobar')).ok,true);
  assert.equal(server.slots.length,1);
  const reopened = card(); await f.context.poblarTurnoSel(reopened);
  assert.match(reopened.controls['.turno-sel'].innerHTML, /2026-09-17T10:00/);
  assert.doesNotMatch(reopened.controls['.turno-sel'].innerHTML, /2026-09-17T09:00/, 'admin-app.js:2379 offers a server-consumed slot');
});
