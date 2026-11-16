import test from 'node:test';
import assert from 'node:assert/strict';
import { mountMyAppointments } from '../static/platform/admin-my-appointments.mjs';

// Execute the complete shipped native controller, doubling only external
// boundaries (deps, fetch, DOM). The module is imported whole and unchanged;
// every mount is disposed and every global restored per test.
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(setImmediate); };

const POPULATED = {
  entrevista_activa: false,
  turnos: [{ turno_dt: '2030-01-01T10:00:00', dia_iso: '2030-01-01', hora: '10:00', paciente: 'Ana', dni: '1' }],
};

function run(t, turnos = []) {
  const feedPending = [];
  let feedCalls = 0;
  let columnChange;
  function makeSection() {
    return {
      style: {}, children: [], text: '',
      set innerHTML(value) { this.text = value; this.children = []; },
      get innerHTML() { return this.text; },
      appendChild(node) { this.children.push(node); },
      querySelector() { return null; },
    };
  }
  let section = makeSection();
  let cal = makeSection();
  const calOpenBtn = {
    clickHandler: null,
    addEventListener(_, fn) { this.clickHandler = fn; },
    setAttribute() {},
  };
  const document = {
    readyState: 'loading', addEventListener() {}, removeEventListener() {},
    querySelector(s) { return s === '#sec-mis-turnos' ? section : null; },
    querySelectorAll() { return []; },
    getElementById(s) {
      if (s === 'sec-mis-turnos') return section;
      if (s === 'mt-cal') return cal;
      if (s === 'mt-cal-open') return calOpenBtn;
      return null;
    },
    createElement() { return { set innerHTML(value) { this.firstChild = { html: value }; } }; },
  };
  const window = {
    document, SANA_STATIC: {},
    sanaImportStatic(name) {
      if (name === 'platform/format.mjs') return Promise.resolve({ nombre: v => v, dni: v => v, dniHtml: v => String(v), edadTexto: () => '', contactoHtml: () => '' });
      if (name === 'platform/i18n.mjs') return Promise.resolve({ t: (key, vars, fallback) => fallback });
      if (name === 'platform/admin-session.mjs') return Promise.resolve({ confirmar: async () => false });
      if (name === 'platform/admin-table.mjs') return Promise.resolve({
        crear: () => ({ programarFit() {}, dispose() {} }),
        columnas: config => {
          columnChange = config.onChange;
          return { activas: () => config.cols, gearHtml: () => '', dispose() {} };
        },
      });
      throw new Error('Unexpected dependency ' + name);
    },
  };
  const fetch = url => {
    // The address is asked for only when the panel opens (the product owner, 2026-10-27):
    // the screen itself renders with the list request alone.
    if (url === '/api/admin/my-appointments/feed') {
      feedCalls += 1;
      return new Promise(resolve => feedPending.push(resolve));
    }
    assert.equal(url, '/api/admin/my-appointments?days=14');
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ turnos }) });
  };
  const saved = Object.fromEntries(['window', 'document', 'fetch']
    .map(k => [k, Object.getOwnPropertyDescriptor(globalThis, k)]));
  globalThis.window = window;
  globalThis.document = document;
  globalThis.fetch = fetch;
  const dispose = mountMyAppointments({ root: document });
  t.after(() => {
    dispose();
    for (const [k, v] of Object.entries(saved)) v ? Object.defineProperty(globalThis, k, v) : delete globalThis[k];
  });
  return {
    get section() { return section; },
    get cal() { return cal; },
    get feedCalls() { return feedCalls; },
    openCal() { calOpenBtn.clickHandler(); },
    changeColumns() { columnChange(); },
    replaceCalHost() { const previous = cal; cal = makeSection(); return previous; },
    finishFeed(index = 0) {
      feedPending[index]({ ok: true, json: async () => ({ url: 'https://example.test/synthetic.ics' }) });
    },
  };
}

test('the screen renders without asking for the calendar address', async t => {
  const page = run(t); await settle();
  assert.match(page.section.text, /No tenés turnos/);
  assert.equal(page.feedCalls, 0);
});

test('a populated current render also leaves the address unasked', async t => {
  const page = run(t, POPULATED.turnos); await settle();
  assert.match(page.section.text, /Ana/);
  assert.equal(page.feedCalls, 0);
});

test('opening asks once, then paints the address', async t => {
  const page = run(t, POPULATED.turnos); await settle();
  page.openCal(); await settle();
  assert.equal(page.feedCalls, 1);
  assert.match(page.cal.text, /Preparando tu dirección/);
  page.finishFeed(); await settle();
  assert.match(page.cal.text, /https:\/\/example\.test\/synthetic\.ics/);
  assert.equal(page.feedCalls, 1);
});

test('closing and reopening reuses the address without a second request', async t => {
  const page = run(t); await settle();
  page.openCal(); await settle();
  page.finishFeed(); await settle();
  page.openCal(); await settle(); // closes: hidden, cleared
  assert.equal(page.cal.hidden, true);
  assert.equal(page.cal.text, '');
  page.openCal(); await settle(); // reopens: the cached address, no request
  assert.equal(page.feedCalls, 1);
  assert.match(page.cal.text, /https:\/\/example\.test\/synthetic\.ics/);
});

test('a column repaint keeps the painted address and asks nothing', async t => {
  const page = run(t, POPULATED.turnos); await settle();
  page.openCal(); await settle();
  page.finishFeed(); await settle();
  page.changeColumns(); await settle();
  assert.equal(page.feedCalls, 1);
  assert.match(page.cal.text, /https:\/\/example\.test\/synthetic\.ics/);
  assert.match(page.section.text, /Ana/);
});

test('a pending address paints into the current host, never a replaced one', async t => {
  const page = run(t); await settle();
  page.openCal(); await settle(); // the request is out, the wait message painted
  const oldHost = page.replaceCalHost();
  page.finishFeed(); await settle();
  assert.doesNotMatch(oldHost.text, /synthetic\.ics/);
  assert.match(page.cal.text, /https:\/\/example\.test\/synthetic\.ics/);
});
