import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
globalThis.SANA_COUNTRY_PHONE=JSON.parse(readFileSync(new URL('../packs/ar/country.json',import.meta.url),'utf8')).phone;

// Multi-center onboarding (2026-10-21): one explicit card per selected
// weekday, 24h typed controls, per-interval service/room, copy-to-days, and
// the save-side gates — overlaps are rejected within AND across centers.
const services = [
  {id: 'medicina-general-ostende', nombre: 'Medicina general', consultorio: 'Consultorio 1'},
  {id: 'clinica-hospital', nombre: 'Clínica médica', consultorio: 'Consultorio 2'},
];

test('slot editor: two 24h numeric inputs, room select, remove button, no day checkboxes', async () => {
  const {slotEditor} = await import('../static/platform/doctor-workspace.mjs');
  const html = slotEditor({desde: '08:00', hasta: '12:00', servicio: 'medicina-general-ostende', source: 2}, services);
  assert.doesNotMatch(html, /data-day=/);
  const times = html.match(/<input class="dw-time"[^>]*>/g) || [];
  assert.equal(times.length, 2);
  for (const input of times) {
    assert.match(input, /type="text"/);
    assert.match(input, /inputmode="numeric"/);
    assert.match(input, /maxlength="5"/);
    assert.match(input, /pattern="\(\[01\]\\d\|2\[0-3\]\):\[0-5\]\\d"/);
    assert.doesNotMatch(input, /type="time"/i);
  }
  assert.match(html, /<select data-service>/);
  assert.match(html, /value="medicina-general-ostende"[^>]*>Medicina general · Consultorio 1</);
  assert.match(html, /data-remove-slot/);
  assert.match(html, /Quitar horario/);
  // The stored band's index rides the row: hidden facts follow it on save.
  assert.match(html, /class="dw-slot" data-source="2"/);
  assert.doesNotMatch(slotEditor({}, services), /data-source/);
});

test('schedule editor: each selected weekday is its own card, separately editable', async () => {
  const {scheduleEditor} = await import('../static/platform/doctor-workspace.mjs');
  const html = scheduleEditor([
    {centro: 'ostende', center: 'Ostende', editable: true, servicios: services,
     franjas: [{dias: [4], desde: '08:00', hasta: '12:00'}, {dias: [5], desde: '14:00', hasta: '18:00'}]},
  ]);
  // Thursday 08:00–12:00 and Friday 14:00–18:00 are each immediately visible
  // and separately editable — never one grouped row with a shared range.
  assert.match(html, /data-day-row="4"[^]*Jueves/);
  assert.match(html, /data-day-row="5"[^]*Viernes/);
  const cards = html.match(/class="dw-day" data-day-row="\d+"/g) || [];
  assert.equal(cards.length, 2);
  assert.match(html, /value="08:00"/);
  assert.match(html, /value="14:00"/);
  // Every card carries its own add-interval and copy actions.
  assert.equal((html.match(/data-add-slot=/g) || []).length, 2);
  assert.equal((html.match(/data-copy-day=/g) || []).length, 2);
  // The weekday chooser shows all seven days, Thursday and Friday checked.
  assert.equal((html.match(/<input type="checkbox" data-day="\d+"/g) || []).length, 7);
  assert.match(html, /data-day="4" checked/);
  assert.match(html, /data-day="5" checked/);
  assert.match(html, /data-day="1"(?! checked)/);
});

test('schedule editor: a split shift is a second interval inside the same weekday card', async () => {
  const {scheduleEditor} = await import('../static/platform/doctor-workspace.mjs');
  const html = scheduleEditor([
    {centro: 'ostende', center: 'Ostende', editable: true,
     franjas: [{dias: [2], desde: '08:00', hasta: '12:00'}, {dias: [2], desde: '14:00', hasta: '18:00'}]},
  ]);
  assert.equal((html.match(/class="dw-day" data-day-row="\d+"/g) || []).length, 1);
  assert.equal((html.match(/class="dw-slot"/g) || []).length, 2);
});

test('schedule editor: separate rooms per center come from each center\'s services', async () => {
  const {scheduleEditor} = await import('../static/platform/doctor-workspace.mjs');
  const html = scheduleEditor([
    {centro: 'ostende', center: 'Ostende', franjas: [{dias: [2], desde: '08:00', hasta: '12:00'}], editable: true, servicios: [services[0]]},
    {centro: 'hospital', center: 'Hospital', franjas: [{dias: [3], desde: '08:00', hasta: '12:00'}], editable: true, servicios: [services[1]]},
  ]);
  assert.equal((html.match(/class="dw-center"/g) || []).length, 2);
  assert.match(html, /Medicina general · Consultorio 1/);
  assert.match(html, /Clínica médica · Consultorio 2/);
});

test('formatTime shapes committed digits into a 24h HH:MM', async () => {
  const {formatTime} = await import('../static/platform/doctor-workspace.mjs');
  assert.equal(formatTime(''), '');
  assert.equal(formatTime('8'), '8');
  assert.equal(formatTime('08'), '08');
  assert.equal(formatTime('083'), '08:3');
  assert.equal(formatTime('0830'), '08:30');
  assert.equal(formatTime('8:30'), '08:30');
  // A numeric keyboard has no colon: "830" is 8:30, not 83:0.
  assert.equal(formatTime('830'), '08:30');
  assert.equal(formatTime('08:30 '), '08:30');
  assert.equal(formatTime('12345'), '12:34');
});

// The card shape the collectors read: a minimal DOM stub per weekday card,
// the same contract mount() queries with real elements.
function slot({desde, hasta, cada = 20, servicio = '', source} = {}) {
  const dataset = source === undefined ? {} : {source: String(source)};
  const inputs = [
    {dataset: {time: 'desde'}, value: desde},
    {dataset: {time: 'hasta'}, value: hasta},
    {dataset: {time: 'cada_min'}, value: String(cada)},
  ];
  return {
    dataset,
    querySelectorAll: sel => sel === '[data-time]' ? inputs : [],
    querySelector: sel => sel === '[data-service]' ? {value: servicio} : null,
  };
}

function dayRow(day, slots) {
  return {dataset: {dayRow: String(day)}, querySelectorAll: sel => sel === '.dw-slot' ? slots : []};
}

function section(centro, days) {
  return {dataset: {center: centro}, querySelectorAll: sel => sel === '.dw-day' ? days : []};
}

test('collect: stored facts follow their own band through day splits and removals', async () => {
  const {collectSchedule} = await import('../static/platform/doctor-workspace.mjs');
  const stored = [
    {centro: 'hospital', franjas: [
      {dias: [4, 5], desde: '08:00', hasta: '12:00', cupos: {web: 1}},
      {dias: [3], desde: '14:00', hasta: '17:00', cupos: {reservado: 2}, servicio: 'clinica-hospital'},
    ], excepciones: [{fecha: '2026-11-02', tipo: 'cerrado'}]},
    {centro: 'ostende', franjas: []},
  ];
  // The doctor removed the stored Thursday/Friday band and typed a fresh
  // Thursday interval; the stored Wednesday band stays, second in the list.
  const edited = collectSchedule([
    section('hospital', [
      dayRow(4, [slot({desde: '09:00', hasta: '13:00'})]),
      dayRow(5, [slot({desde: '08:00', hasta: '12:00', source: 0})]),
      dayRow(3, [slot({desde: '14:00', hasta: '17:00', servicio: 'clinica-hospital', source: 1})]),
    ]),
    section('ostende', [dayRow(3, [slot({desde: '09:00', hasta: '13:00'})])]),
  ], structuredClone(stored));
  assert.deepEqual(edited.map(c => c.centro), ['hospital', 'ostende']);
  const [thursday, friday, wednesday] = edited[0].franjas;
  // A newly typed interval inherits nothing: no cupos of the band it replaced.
  assert.deepEqual(thursday, {dias: [4], desde: '09:00', hasta: '13:00', cada_min: 20});
  // A day split keeps each side anchored to its own stored band's facts.
  assert.deepEqual(friday.cupos, {web: 1});
  assert.deepEqual(friday.dias, [5]);
  assert.deepEqual(wednesday, {dias: [3], desde: '14:00', hasta: '17:00', cada_min: 20, cupos: {reservado: 2}, servicio: 'clinica-hospital'});
  assert.deepEqual(edited[1].franjas[0], {dias: [3], desde: '09:00', hasta: '13:00', cada_min: 20});
});

test('collect: removing the first interval never moves its facts onto the second', async () => {
  const {collectSchedule} = await import('../static/platform/doctor-workspace.mjs');
  const stored = [
    {centro: 'hospital', franjas: [
      {dias: [1], desde: '08:00', hasta: '12:00', cupos: {web: 1}},
      {dias: [2], desde: '14:00', hasta: '17:00', cupos: {reservado: 2}},
    ]},
  ];
  // Monday's row is gone; Tuesday's row is still the SECOND stored band.
  const [hospital] = collectSchedule([
    section('hospital', [dayRow(2, [slot({desde: '14:00', hasta: '17:00', source: 1})])]),
  ], structuredClone(stored));
  assert.deepEqual(hospital.franjas, [{dias: [2], desde: '14:00', hasta: '17:00', cada_min: 20, cupos: {reservado: 2}}]);
});

test('collect: an explicitly cleared service does not linger from the stored band', async () => {
  const {collectSchedule} = await import('../static/platform/doctor-workspace.mjs');
  const stored = [{centro: 'ostende', franjas: [{dias: [3], desde: '09:00', hasta: '13:00', servicio: 'clinica-hospital'}]}];
  const [ostende] = collectSchedule([
    section('ostende', [dayRow(3, [slot({desde: '09:00', hasta: '13:00', servicio: '', source: 0})])]),
  ], structuredClone(stored));
  assert.equal(ostende.franjas[0].servicio, undefined);
});

test('scheduleProblems stops same-center and cross-center overlaps with remedies', async () => {
  const {scheduleProblems} = await import('../static/platform/doctor-workspace.mjs');
  const center = (franja1, franja2) => [{centro: 'hospital', center: 'Hospital', franjas: [franja1, franja2].filter(Boolean)}];
  const ok = {dias: [1, 3], desde: '08:00', hasta: '12:00'};
  assert.deepEqual(scheduleProblems(center(ok, {dias: [2], desde: '14:00', hasta: '17:00'})), []);
  // Same hours on a shared day overlap within one center.
  assert.match(scheduleProblems(center(ok, {dias: [3], desde: '10:00', hasta: '13:00'}))[0], /superponen/);
  // The same doctor cannot be at two centers at once on the same weekday:
  // the cross-center clash is named with both centers and rejected.
  const crossClash = scheduleProblems([
    {centro: 'hospital', center: 'Hospital', franjas: [ok]},
    {centro: 'ostende', center: 'Ostende', franjas: [{dias: [1], desde: '10:00', hasta: '13:00'}]},
  ]);
  assert.equal(crossClash.length, 1);
  assert.match(crossClash[0], /Hospital/);
  assert.match(crossClash[0], /Ostende/);
  // Disjoint cross-center windows on the same weekday are a valid split day.
  assert.deepEqual(scheduleProblems([
    {centro: 'hospital', center: 'Hospital', franjas: [{dias: [1], desde: '08:00', hasta: '12:00'}]},
    {centro: 'ostende', center: 'Ostende', franjas: [{dias: [1], desde: '14:00', hasta: '18:00'}]},
  ]), []);
  assert.match(scheduleProblems(center({dias: [1], desde: '12:00', hasta: '08:00'}))[0], /termina antes de empezar/);
  assert.match(scheduleProblems(center({dias: [], desde: '08:00', hasta: '12:00'}))[0], /al menos un día/);
});

function harness() {
  const oldFetch = globalThis.fetch, oldFormData = globalThis.FormData;
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({url, body: options?.body ? JSON.parse(options.body) : null});
    return {ok: true, status: 200, json: async () => url.endsWith('/complete') ? {ok: true, next: '/admin/cola'} : {ok: true, item: {id: 'doctor'}}}; };
  globalThis.FormData = class { constructor(form) { return form.fields.entries(); } };
  function form() {
    const handlers = new Map(), status = {textContent: '', classList: {toggle() {}}};
    return {dataset: {}, fields: new Map(), reportValidity: () => true, querySelector: () => status, querySelectorAll: () => [],
      addEventListener: (name, fn) => handlers.set(name, fn), removeEventListener: name => handlers.delete(name),
      dispatch(name) { handlers.get(name)?.({preventDefault() {}}); }, status};
  }
  const profile = form(), schedule = form(), sectionWrap = form();
  profile.fields.set('nombre_pila', 'Ana'); profile.fields.set('apellido', 'Example');
  const handlers = new Map();
  const finish = {click: () => handlers.get('click')(), addEventListener: (name, fn) => handlers.set(name, fn), removeEventListener: name => handlers.delete(name), closest: () => sectionWrap};
  const root = {querySelector: sel => ({'[data-profile]': profile, '[data-schedule]': schedule, '[data-finish]': finish}[sel] || null)};
  return {calls, profile, schedule, sectionWrap, finish, root,
    restore() { globalThis.fetch = oldFetch; globalThis.FormData = oldFormData; }};
}

test('retry after content saved and completion failed preserves each original band metadata', async t => {
  const {mount} = await import('../static/platform/doctor-workspace.mjs');
  const h = harness(); t.after(h.restore);
  const doctor = {id: 'doctor', centros: [{centro: 'hospital', franjas: [
    {dias: [4, 5], desde: '08:00', hasta: '12:00', cupos: {web: 1}},
    {dias: [1], desde: '14:00', hasta: '17:00', cupos: {reservado: 2}},
  ]}]};
  h.schedule.querySelectorAll = sel => sel === '[data-center][data-editable]' ? [
    section('hospital', [
      dayRow(1, [slot({desde: '14:00', hasta: '17:00', source: 1})]),
      dayRow(4, [slot({desde: '08:00', hasta: '12:00', source: 0})]),
      dayRow(5, [slot({desde: '08:00', hasta: '12:00', source: 0})]),
    ]),
  ] : [];
  let completions = 0;
  const saves = [], destinations = [];
  globalThis.fetch = async (url, options) => {
    if (url.endsWith('/complete')) {
      const ok = ++completions > 1;
      return {ok, status: ok ? 200 : 503, json: async () => ok
        ? {ok: true, next: '/admin/cola'} : {error: 'No se pudo completar. Reintentá.'}};
    }
    const body = JSON.parse(options.body); saves.push(body);
    return {ok: true, status: 200, json: async () => ({ok: true, item: {id: 'doctor', ...body}})};
  };
  const dispose = mount(h.root, {editable: true, doctors: [doctor], schedules: [{centro: 'hospital', center: 'Hospital'}]}, url => destinations.push(url));
  t.after(dispose);
  h.finish.click(); await new Promise(r => setImmediate(r));
  assert.equal(saves.length, 1);
  assert.match(h.sectionWrap.status.textContent, /Reintentá/);
  assert.deepEqual(destinations, []);
  h.finish.click(); await new Promise(r => setImmediate(r));
  assert.equal(saves.length, 2);
  assert.deepEqual(saves[1].centros, saves[0].centros);
  assert.deepEqual(saves[1].centros[0].franjas.map(b => b.cupos), [{reservado: 2}, {web: 1}, {web: 1}]);
  assert.deepEqual(destinations, ['/admin/cola']);
});

test('finish blocks the save on a same-center overlap instead of sending it', async t => {
  const {mount} = await import('../static/platform/doctor-workspace.mjs');
  const h = harness(); t.after(h.restore);
  h.schedule.querySelectorAll = sel => sel === '[data-center][data-editable]' ? [
    section('hospital', [dayRow(1, [slot({desde: '08:00', hasta: '12:00'}), slot({desde: '10:00', hasta: '13:00'})])]),
  ] : [];
  const data = {editable: true, schedules: [{centro: 'hospital', center: 'Hospital'}],
    doctors: [{id: 'doctor', centros: [{centro: 'hospital', franjas: []}]}]};
  const dispose = mount(h.root, data, () => {}); t.after(dispose);
  h.finish.click();
  await new Promise(r => setImmediate(r));
  assert.deepEqual(h.calls.map(call => call.url), [], 'no request leaves the page while two intervals overlap');
  assert.match(h.sectionWrap.status.textContent, /superponen/);
});

test('finish blocks the save on a cross-center overlap instead of sending it', async t => {
  const {mount} = await import('../static/platform/doctor-workspace.mjs');
  const h = harness(); t.after(h.restore);
  h.schedule.querySelectorAll = sel => sel === '[data-center][data-editable]' ? [
    section('hospital', [dayRow(1, [slot({desde: '08:00', hasta: '12:00'})])]),
    section('ostende', [dayRow(1, [slot({desde: '10:00', hasta: '13:00'})])]),
  ] : [];
  const data = {editable: true, schedules: [{centro: 'hospital', center: 'Hospital'}, {centro: 'ostende', center: 'Ostende'}],
    doctors: [{id: 'doctor', centros: [{centro: 'hospital', franjas: []}, {centro: 'ostende', franjas: []}]}]};
  const dispose = mount(h.root, data, () => {}); t.after(dispose);
  h.finish.click();
  await new Promise(r => setImmediate(r));
  assert.deepEqual(h.calls.map(call => call.url), [], 'no request leaves the page while two centers overlap on one weekday');
  assert.match(h.sectionWrap.status.textContent, /Hospital/);
  assert.match(h.sectionWrap.status.textContent, /Ostende/);
});

test('finish blocks an agenda with no hours at all', async t => {
  const {mount} = await import('../static/platform/doctor-workspace.mjs');
  const h = harness(); t.after(h.restore);
  h.schedule.querySelectorAll = sel => sel === '[data-center][data-editable]' ? [
    section('hospital', [dayRow(1, [])]),
  ] : [];
  const data = {editable: true, schedules: [{centro: 'hospital', center: 'Hospital'}],
    doctors: [{id: 'doctor', centros: [{centro: 'hospital', franjas: []}]}]};
  const dispose = mount(h.root, data, () => {}); t.after(dispose);
  h.finish.click();
  await new Promise(r => setImmediate(r));
  assert.deepEqual(h.calls.map(call => call.url), []);
  assert.match(h.sectionWrap.status.textContent, /al menos un día/);
});

test('finish sends the collected per-day bands, splitting stored days with their facts', async t => {
  const {mount} = await import('../static/platform/doctor-workspace.mjs');
  const h = harness(); t.after(h.restore);
  h.schedule.querySelectorAll = sel => sel === '[data-center][data-editable]' ? [
    section('hospital', [
      dayRow(1, [slot({desde: '08:00', hasta: '12:00', servicio: 'clinica-hospital', source: 0})]),
      dayRow(4, [slot({desde: '08:00', hasta: '12:00', source: 0})]),
    ]),
    section('ostende', [dayRow(3, [slot({desde: '09:00', hasta: '13:00', cada: 30})])]),
  ] : [];
  const data = {editable: true, schedules: [{centro: 'hospital', center: 'Hospital'}, {centro: 'ostende', center: 'Ostende'}],
    doctors: [{id: 'doctor', centros: [
      {centro: 'hospital', franjas: [{dias: [1, 4], desde: '08:00', hasta: '12:00', cupos: {web: 1}}], excepciones: [{fecha: '2026-11-02', tipo: 'cerrado'}]},
      {centro: 'ostende', franjas: []}]}]};
  const dispose = mount(h.root, data, url => { h.calls.push({redirect: url}); }); t.after(dispose);
  h.finish.click();
  await new Promise(r => setImmediate(r));
  const save = h.calls.find(call => call.url === '/api/admin/content/doctors/doctor');
  assert.ok(save, 'catalog save happened');
  // The stored Monday+Thursday band splits into one band per weekday, each
  // keeping its own cupos; the center's closures survive untouched.
  assert.deepEqual(save.body.centros[0].franjas, [
    {dias: [1], desde: '08:00', hasta: '12:00', cada_min: 20, cupos: {web: 1}, servicio: 'clinica-hospital'},
    {dias: [4], desde: '08:00', hasta: '12:00', cada_min: 20, cupos: {web: 1}},
  ]);
  assert.deepEqual(save.body.centros[0].excepciones, [{fecha: '2026-11-02', tipo: 'cerrado'}]);
  assert.deepEqual(save.body.centros[1].franjas[0], {dias: [3], desde: '09:00', hasta: '13:00', cada_min: 30});
  assert.ok(h.calls.some(call => call.redirect === '/admin/cola'));
});
