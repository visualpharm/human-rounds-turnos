import test from 'node:test';
import assert from 'node:assert/strict';
import { nextAppointmentDay, gridHtml } from '../static/platform/calendar-grid.mjs';
import { fixture, click, flush } from './helpers/queue-runtime-fixture.mjs';

test('calendar navigation skips empty days, deduplicates and crosses month/year boundaries', () => {
  const days = ['2027-01-04', '2026-12-30', '2026-12-30', '2027-01-02'];
  assert.equal(nextAppointmentDay(days, '2026-12-30', 1), '2027-01-02');
  assert.equal(nextAppointmentDay(days, '2027-01-02', -1), '2026-12-30');
  assert.equal(nextAppointmentDay(days, '2026-12-31', 1), '2027-01-02');
  assert.equal(nextAppointmentDay(days, '2026-12-29', -1), null);
  assert.equal(nextAppointmentDay([], '2027-01-01', 1), null);
});
test('the actual shared month grid highlights occupied days but keeps empty days selectable', () => {
  const html = gridHtml([{ dia: '2026-10-14', className: 'queue-calendar-occupied' }],
    { from: '2026-10-01', to: '2026-10-31', selected: '2026-10-13', dateAttr: 'data-cola-calendar-date' });
  assert.match(html, /class="medcal-dia queue-calendar-occupied(?: medcal-hoy)?" data-cola-calendar-date="2026-10-14"/);
  assert.match(html, /data-cola-calendar-date="2026-10-15"/);
  assert.doesNotMatch(html, / disabled/);
});
async function mounted(t) {
  const f = fixture(t, { queueDom: true, holdCalendar: true, initialData: {
    dia: '2026-10-13', hoy: true, grupos: [{ medico_id: 'doctor', medico: 'Doctor Example', turnos: [], libres: [] }]
  } });
  const owner = f.mount(); f.readyTable(); await owner.ready;
  assert.equal(f.calendarRequests.length, 1);
  return { f, owner };
}
function calendar(request, values = {}) {
  request.resolve(new Response(JSON.stringify({ dia: '2026-10-13', dias: ['2026-10-14'], anterior: '2026-09-01', siguiente: '2026-10-14', ...values }), { status: 200 }));
}
test('real queue arrows use occupied-day neighbors from the server, never current day plus one', async t => {
  const { f } = await mounted(t);
  calendar(f.calendarRequests[0], { siguiente: '2026-10-19' }); await flush(); await flush();
  click(f, '[data-cola-nav]', { 'data-cola-nav': '1' });
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].url, '/api/admin/day-queue?dia=2026-10-19');
});
test('a late calendar response cannot navigate after a center-filter change', async t => {
  const { f } = await mounted(t);
  const center = { value: 'Hospital', closest: selector => selector === '[data-cola-centrofiltro]' ? center : null };
  f.doc.emit('change', { target: center });
  assert.equal(f.calendarRequests[0].options.signal.aborted, true);
  assert.equal(new URL(f.calendarRequests[1].url, 'https://test.invalid').searchParams.get('centro'), 'Hospital');
  calendar(f.calendarRequests[1], { siguiente: '2026-10-21' }); await flush(); await flush();
  calendar(f.calendarRequests[0], { siguiente: '2026-10-14' }); await flush(); await flush();
  click(f, '[data-cola-nav]', { 'data-cola-nav': '1' });
  assert.equal(f.requests[0].url, '/api/admin/day-queue?dia=2026-10-21');
});
test('calendar read is aborted on disposal and its late result cannot revive navigation', async t => {
  const { f, owner } = await mounted(t);
  owner();
  assert.equal(f.calendarRequests[0].options.signal.aborted, true);
  calendar(f.calendarRequests[0]); await flush(); await flush();
  click(f, '[data-cola-nav]', { 'data-cola-nav': '1' });
  assert.equal(f.requests.length, 0);
});


test('the shared grid renders actual counts with accessible labels and escapes day metadata',()=>{
  const html=gridHtml([{dia:'2026-10-14',className:'queue-calendar-occupied',count:8,title:'<unsafe>'}],{from:'2026-10-01',to:'2026-10-31',today:'2026-10-15'});
  assert.match(html,/title="&lt;unsafe&gt;" aria-label="Día 14: 8 turnos"/);
  assert.match(html,/>14<span class="medcal-count">8<\/span><\/button>/);
  assert.doesNotMatch(gridHtml([{dia:'2026-10-14'}]),/medcal-count/);
});
