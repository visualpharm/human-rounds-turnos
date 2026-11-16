import test from 'node:test';
import assert from 'node:assert/strict';
import {mounted, click, flush, patient} from './lineage/queue-fixture.mjs';
test('lineage: attendance renders the server timestamp despite a skewed browser clock', async t => {
  const RealDate = Date;
  t.mock.method(globalThis, 'Date', class extends RealDate {
    constructor(...args) { super(...(args.length ? args : ['2026-09-16T22:44:00Z'])); }
    static now() { return RealDate.parse('2026-09-16T22:44:00Z'); }
  });
  const f = await mounted(t);
  const cell = {innerHTML:'',querySelector:()=>null};
  // Attendance is marked in the appointment card (the product owner, 2026-11-09): the
  // card's attendance box is what repaints, the row only changes weight.
  const row = {classList:{toggle(){}},querySelector:()=>null};
  const query = f.doc.querySelector;
  f.doc.querySelector = selector => selector === '[data-cola-row="r1"]' ? row : selector === '[data-cola-asis-box="r1"]' ? cell : query(selector);
  click(f,'[data-asis]',{'data-asis':'r1','data-valor':'presente'});
  const request = f.requests.find(r=>r.url === '/api/admin/requests/r1/attendance');
  assert.ok(request); assert.deepEqual(JSON.parse(request.options.body),{asistencia:'presente'});
  const stamp = '2026-09-16T08:17:00-03:00';
  // DB attendance_updated_at is exposed as asistencia_actualizada_en in
  // the mutation response and asistencia_en in the day-queue projection.
  request.resolve(new Response(JSON.stringify({ok:true,solicitud:{...patient,asistencia:'presente',asistencia_actualizada_en:stamp}})));
  await flush(); await flush();
  assert.match(cell.innerHTML,/Atendió/, 'successful mutation repaints attendance');
  assert.match(cell.innerHTML,/08:17/, 'the card attendance box must display the server timestamp');
  assert.doesNotMatch(cell.innerHTML,/19:44/, 'browser time is not appointment truth');
});
