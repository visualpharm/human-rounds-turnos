import assert from 'node:assert/strict';
import test from 'node:test';
import { mount } from '../static/platform/admin-content.mjs';
import * as Hours from '../static/platform/admin-hours-editor.mjs';

const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function target() {
  const listeners = new Map();
  return {
    listeners,
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    count() { return [...listeners.values()].reduce((n, set) => n + set.size, 0); },
    emit(type, event = {}) { for (const fn of [...listeners.get(type) || []]) fn(event); },
  };
}
function fixture(t, { screen = 'servicios', imports = null } = {}) {
  const requests = [], h1 = { style: {}, textContent: '' }, section = {
    id: 'sec-servicios', style: {}, innerHTML: 'departed owner', isConnected: true,
    setAttribute() {}, removeAttribute() {},
  };
  const doc = Object.assign(target(), {
    readyState: 'complete', documentElement: {}, head: { appendChild() {} }, body: {},
    getElementById(id) { return id === 'adm-h1' ? h1 : id === 'sec-servicios' ? section : null; },
    querySelector(selector) { return selector === '#adm-h1' ? h1 : selector === '#sec-servicios' ? section : null; },
    querySelectorAll() { return []; }, createElement() { return { style: {}, appendChild() {}, setAttribute() {} }; },
  });
  const win = Object.assign(target(), {
    document: doc, SANA_ADMIN_SCREEN: screen, SANA_STATIC: {}, CURRENT_ROLE: 'admin',
    location: { pathname: '/admin/services', search: '' }, history: { pushState() {}, replaceState() {} },
    setTimeout, clearTimeout,
  });
  doc.defaultView = win;
  const loader = imports || (() => Promise.resolve({ load: async () => {}, get: (_key, fallback) => fallback, set() {}, confirmar: async () => false }));
  const run = () => mount(doc, {
    window: win,
    importStatic: loader,
    fetch(url, options) { const gate = deferred(); requests.push({ url, options, ...gate }); return gate.promise; },
  });
  t.after(() => { section.isConnected = false; });
  return { doc, win, section, requests, run };
}

function classList() {
  const values = new Set();
  return { add: value => values.add(value), remove: value => values.delete(value),
    contains: value => values.has(value), toggle: (value, force) => {
      const on = force == null ? !values.has(value) : !!force;
      if (on) values.add(value); else values.delete(value);
      return on;
    } };
}
function runtimeElement(doc, id = '') {
  const events = target();
  const attributes = new Map(id ? [['id', id]] : []);
  return Object.assign(events, {
    id, style: {}, classList: classList(), className: '', isConnected: true,
    value: '', checked: false, disabled: false, hidden: false, textContent: '',
    scrollHeight: 0, attributes,
    setAttribute(name, value) { attributes.set(name, String(value)); if (name === 'id') this.id = String(value); },
    getAttribute(name) { return attributes.has(name) ? attributes.get(name) : null; },
    hasAttribute(name) { return attributes.has(name); },
    removeAttribute(name) { attributes.delete(name); },
    appendChild(node) { this.children ||= []; this.children.push(node); return node; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    closest() { return this.panel || null; }, matches() { return false; },
    scrollIntoView() {}, focus() { doc.activeElement = this; },
    click() { this.emit('click', { preventDefault() {}, target: this }); },
    remove() { this.isConnected = false; this.owner?.forget(this); },
    insertAdjacentElement(_where, node) { this.owner.add(node); },
  });
}
function formRuntimeFixture(t, { screen, item = null, saveError, fetchOverride }) {
  const requests = [], toasts = [];
  const h1 = runtimeElement(null, 'adm-h1');
  const doc = Object.assign(target(), { readyState: 'complete', documentElement: {}, head: { appendChild() {} }, body: {}, activeElement: null });
  h1.focus = () => { doc.activeElement = h1; };
  const section = runtimeElement(doc, `sec-${screen}`);
  section.owner = section;
  section.items = new Map(); section.tabs = []; section.panels = []; section.centerSelects = [];
  section.add = node => { node.owner = section; node.isConnected = true; if (node.id) section.items.set(node.id, node); return node; };
  section.forget = node => { if (section.items.get(node.id) === node) section.items.delete(node.id); };
  section.querySelector = selector => {
    const id = selector.match(/^#([\w-]+)$/)?.[1];
    if (id) return section.items.get(id) || null;
    if (selector === 'button[type="submit"]') return section.submit;
    const tab = selector.match(/^\.cnt-tab\[data-tab="(.+)"\]$/)?.[1];
    if (tab) return section.tabs.find(node => node.getAttribute('data-tab') === tab) || null;
    return null;
  };
  section.querySelectorAll = selector => {
    if (selector === '.cnt-exc-row') return section.closureRows || [];
    if (selector === '[data-centro-select]') return section.centerSelects;
    if (selector === '.cnt-tab') return section.tabs;
    if (selector === '.cnt-tabpanel') return section.panels;
    if (selector === 'textarea.cnt-textarea') return [...section.items.values()].filter(node => node.tagName === 'textarea');
    if (selector === '#svc-modo-list input[type=checkbox]') return ['svc-c-turno', 'svc-c-orden', 'svc-c-aprobacion', 'svc-c-mismodia'].map(id => section.items.get(id)).filter(Boolean);
    return [];
  };
  Object.defineProperty(section, 'innerHTML', { get() { return section._html || ''; }, set(html) {
    section._html = html; section.items.clear(); section.tabs = []; section.panels = []; section.centerSelects = [];
    for (const match of html.matchAll(/<(input|textarea|select|div|form|p|button)[^>]*\bid="([^"]+)"[^>]*>/g)) {
      const node = runtimeElement(doc, match[2]); node.tagName = match[1]; node.value = (match[0].match(/\bvalue="([^"]*)"/) || [])[1] || '';
      node.checked = /\bchecked(?:\s|>|=)/.test(match[0]);
      if (match[1] === 'select') {
        const rest = html.slice(match.index + match[0].length).split('</select>')[0];
        node.value = (rest.match(/<option value="([^"]*)" selected/) || rest.match(/<option value="([^"]*)"/) || [])[1] || '';
      }
      section.add(node);
    }
    section.submit = runtimeElement(doc); section.submit.disabled = false; section.add(section.submit);
    if (screen === 'medicos') {
      const hiddenInfo = runtimeElement(doc); hiddenInfo.setAttribute('data-tabpanel', 'info'); hiddenInfo.style.display = 'none'; section.panels.push(hiddenInfo);
      const tab = runtimeElement(doc); tab.setAttribute('data-tab', 'info'); tab.classList.add('cnt-tab');
      tab.addEventListener('click', () => { hiddenInfo.style.display = ''; }); section.tabs.push(tab);
      for (const value of ['center-one', 'center-two']) {
        const select = runtimeElement(doc); select.owner = section; select.value = value; select.setAttribute('data-centro-select', ''); select.panel = hiddenInfo; section.centerSelects.push(select);
      }
    }
  } });
  doc.getElementById = id => id === 'adm-h1' ? h1 : id === section.id ? section : section.items.get(id) || null;
  doc.querySelector = selector => selector === '#adm-h1' ? h1 : selector === `#${section.id}` ? section : doc.getElementById(selector.slice(1));
  doc.querySelectorAll = selector => selector === '.adm-content-wrap > [id^="sec-"]' ? [section] : [];
  doc.createElement = () => runtimeElement(doc); doc.createTextNode = text => ({ textContent: text });
  const win = Object.assign(target(), {
    document: doc, SANA_ADMIN_SCREEN: screen, SANA_STATIC: {}, CURRENT_ROLE: 'admin',
    location: { pathname: `/admin/${{servicios: 'services', centros: 'centers', medicos: 'doctors'}[screen]}`, search: item ? `?item=${item.id || item.slug}` : '?item=new' },
    history: { pushState() {}, replaceState() {} }, setTimeout, clearTimeout, toast: msg => toasts.push(msg),
  });
  doc.defaultView = win;
  const response = (status, body) => ({ status, ok: status >= 200 && status < 300, json: async () => body, clone() { return this; } });
  const importStatic = async name => {
    if (name === 'platform/admin-hours-editor.mjs') return {
      ...Hours,
      editorHtml: () => '<div></div>', capacityDescriptionHtml: () => '', webChoiceHtml: () => '',
      closureHeadingHtml: () => '', excColsHtml: () => '', excRowHtml: () => '',
    };
    if (name === 'platform/admin-preferences.mjs') return { load: async () => {} };
    if (name === 'platform/admin-session.mjs') return { confirmar: async () => false };
    if (name === 'platform/admin-users.mjs') return { mountDoctorUser: () => ({ dispose() {}, ready: Promise.resolve() }) };
    if (name === 'platform/islands.mjs') return { mount: async () => {}, unmount() {}, update() {} };
    return { get: (_key, fallback) => fallback, set() {}, load: async () => {} };
  };
  const run = () => mount(doc, { window: win, importStatic, fetch: async (url, options = {}) => {
    requests.push({ url, options });
    const overridden = fetchOverride && await fetchOverride(url, options, response);
    if (overridden) return overridden;
    if (options.method === 'POST' || options.method === 'PUT') return response(400, { ok: false, error: saveError });
    if (url === '/api/admin/content/services') return response(200, { items: item ? [item] : [] });
    if (url === '/api/admin/content/centers') return response(200, { items: screen === 'centros' ? (item ? [item] : []) : [{ slug: 'center-one', name: 'Center one' }] });
    if (url === '/api/admin/content/doctors') return response(200, { items: item ? [item] : [] });
    if (url === '/data/especialidades.json') return response(200, { specialties: [{ nombre: 'General', cluster: 'general' }] });
    if (url === '/data/clusters.json') return response(200, { clusters: [{ id: 'general', nombre: 'General' }] });
    if (url.includes('/compatible-services?')) return response(200, { items: [] });
    if (url.includes('/appointments?')) return response(200, { total: 0, items: [] });
    if (url === '/api/admin/intake/settings') return response(200, { medicos: [] });
    if (/^\/api\/admin\/centers\/[^/]+\/photo$/.test(url)) return response(200, {});
    throw new Error(`unexpected request ${url}`);
  } });
  t.after(() => { section.isConnected = false; });
  return { doc, section, run, win, requests, toasts };
}

test('canonical module import is inert without browser globals', () => {
  assert.equal(typeof mount, 'function');
  assert.equal(typeof globalThis.document, 'undefined');
});

test('mount disposal removes owned document/window listeners and permits a fresh owner', async t => {
  const f = fixture(t), first = f.run();
  await flush();
  assert.equal(f.requests.length, 1, 'real catalog route issued its content GET');
  assert.ok(f.doc.count() > 0); assert.ok(f.win.count() > 0);
  const second = f.run();
  assert.equal(f.requests[0].options.signal.aborted, true);
  assert.equal(f.doc.count(), 1, 'replacement did not retain old document listeners');
  second();
  assert.equal(f.doc.count(), 0); assert.equal(f.win.count(), 0);
  first();
});

test('held catalog GET success and failure cannot repaint a disposed owner', async t => {
  const f = fixture(t), old = f.run();
  await flush();
  const replacement = f.run();
  await flush();
  assert.equal(f.requests.length, 2);
  f.requests[0].resolve({ ok: false, status: 503, json: async () => ({}) });
  await flush();
  assert.equal(f.section.innerHTML, 'departed owner');
  f.requests[1].reject(new Error('current transport failure'));
  await flush();
  assert.match(f.section.innerHTML, /No se pudo cargar/);
  replacement(); old();
});

test('a catalog GET 401 the session check does not confirm shows the load error, never a stuck screen', async t => {
  const f = fixture(t), owner = f.run();
  owner.ready.catch(() => {});
  await flush();
  f.requests[0].resolve({ ok: false, status: 401, json: async () => ({}) });
  await flush(); await flush();
  assert.match(f.section.innerHTML, /No se pudo cargar/);
  assert.match(f.section.innerHTML, /data-catalog-route-retry/);
  owner();
});

test('a catalog GET 401 the session check confirms leaves the screen to the redirect', async t => {
  const imports = () => Promise.resolve({ load: async () => {}, get: (_key, fallback) => fallback, set() {}, confirmar: async () => true });
  const f = fixture(t, { imports }), owner = f.run();
  owner.ready.catch(() => {});
  await flush();
  f.requests[0].resolve({ ok: false, status: 401, json: async () => ({}) });
  await flush(); await flush();
  assert.equal(f.section.innerHTML, 'departed owner');
  owner();
});

for (const outcome of ['success', 'failure']) {
  test('disposed catalog response body ' + outcome + ' cannot repaint', async t => {
    const f = fixture(t), body = deferred(), owner = f.run();
    owner.ready.catch(() => {});
    await flush();
    f.requests[0].resolve({ ok: true, status: 200, json: () => body.promise });
    await flush();
    owner();
    f.section.innerHTML = 'fresh replacement view';
    assert.equal(f.requests[0].options.signal.aborted, true,
      'disposal aborts the response stream after headers, not only pending fetch');
    if (outcome === 'success') body.resolve(null); else body.reject(new Error('late body failure'));
    await flush(); await flush();
    assert.equal(f.section.innerHTML, 'fresh replacement view');
  });
}

test('navigation aborts a pending response body before replacing the route', async t => {
  const f = fixture(t), body = deferred(), owner = f.run();
  await flush();
  f.requests[0].resolve({ ok: true, status: 200, json: () => body.promise });
  await flush();
  f.win.emit('popstate');
  assert.equal(f.requests[0].options.signal.aborted, true);
  body.reject(new Error('old stream stopped'));
  await flush(); await flush();
  assert.equal(f.requests.length, 2);
  assert.equal(f.section.innerHTML, 'departed owner');
  owner();
});

test('a held native import resolving after disposal cannot start routing', async t => {
  const held = deferred();
  const f = fixture(t, { screen: '', imports: () => held.promise });
  const owner = f.run();
  owner();
  held.resolve({ load: async () => {}, get: (_key, fallback) => fallback, set() {} });
  await flush(); await flush();
  assert.equal(f.requests.length, 0);
  assert.equal(f.doc.count(), 0); assert.equal(f.win.count(), 0);
});

test('runtime field errors retain an existing helper and restore its ARIA state across retries', async t => {
  const f = formRuntimeFixture(t, { screen: 'servicios', saveError: 'falta nombre' });
  const owner = f.run(); await owner.ready;
  assert.match(f.section.innerHTML, /id="cnt-form"/);
  const form = f.section.querySelector('#cnt-form');
  const control = f.section.querySelector('#svc-nombre');
  const helper = runtimeElement(f.doc, 'svc-nombre-msg'); helper.textContent = 'Server-owned helper'; f.section.add(helper);
  control.setAttribute('aria-invalid', 'grammar');
  control.setAttribute('aria-describedby', 'svc-nombre-msg persisted-description');
  form.emit('submit', { preventDefault() {} }); await flush(); await flush();
  const error = f.section.querySelector('#svc-nombre-validation-msg');
  assert.ok(error, 'the module creates a distinct message rather than replacing an existing helper');
  assert.equal(helper.textContent, 'Server-owned helper');
  assert.equal(control.getAttribute('aria-invalid'), 'true');
  assert.equal(control.getAttribute('aria-describedby'), 'svc-nombre-msg persisted-description svc-nombre-validation-msg');
  form.emit('submit', { preventDefault() {} }); await flush(); await flush();
  assert.equal(f.section.querySelector('#svc-nombre-validation-msg'), error, 'repeated invalid saves reuse the owned message');
  control.value = 'Recovered service'; control.emit('input');
  assert.equal(error.isConnected, false);
  assert.equal(helper.isConnected, true);
  assert.equal(helper.textContent, 'Server-owned helper');
  assert.equal(control.getAttribute('aria-invalid'), 'grammar');
  assert.equal(control.getAttribute('aria-describedby'), 'svc-nombre-msg persisted-description');
  control.value = ''; form.emit('submit', { preventDefault() {} }); await flush(); await flush();
  const retry = f.section.querySelector('#svc-nombre-validation-msg');
  assert.ok(retry && retry !== error, 'a corrected field can receive one new owned error');
  control.value = 'Recovered again'; control.emit('change');
  assert.equal(retry.isConnected, false);
  owner();
});

test('runtime doctor center-row validation targets the submitted row named by the server', async t => {
  const f = formRuntimeFixture(t, {
    screen: 'medicos', saveError: 'centro 2: falta centro',
    item: { id: 'doctor-one', nombre_pila: 'Ana', apellido: 'Médica', especialidad: 'General', centros: [{ centro: 'center-one' }, { centro: 'center-two' }] },
  });
  const owner = f.run(); await owner.ready;
  assert.match(f.section.innerHTML, /id="cnt-form"/);
  const [first, second] = f.section.centerSelects;
  f.section.querySelector('#cnt-form').emit('submit', { preventDefault() {} }); await flush(); await flush();
  assert.equal(first.id, '', 'the first submitted center is not selected for a row-two rejection');
  assert.equal(second.id, 'med-centro-sel-2');
  const error = f.section.querySelector('#med-centro-sel-2-msg');
  assert.ok(error);
  assert.equal(second.getAttribute('aria-invalid'), 'true');
  assert.equal(f.doc.activeElement, second);
  second.value = 'center-two-corrected'; second.emit('change');
  assert.equal(error.isConnected, false);
  assert.equal(second.getAttribute('aria-invalid'), null);
  owner();
});

const doctor = { id: 'doctor-one', nombre_pila: 'Ana', apellido: 'Médica', especialidad: 'General', centros: [{ centro: 'center-one', franjas: [], excepciones: [] }] };
const submit = async f => { f.section.querySelector('#cnt-form').emit('submit', { preventDefault() {} }); await flush(); await flush(); };

for (const status of [401, 403]) test(`rejected save ${status} gives a remedy and retains the draft`, async t => {
  const f = formRuntimeFixture(t, { screen: 'medicos', item: doctor,
    fetchOverride: (url, opts, response) => opts.method === 'PUT' && response(status, { error: status === 401 ? 'no_autenticado' : 'demo_solo_lectura' }) });
  const owner = f.run(); t.after(owner); await owner.ready;
  const name = f.section.querySelector('#med-nombre-pila'); name.value = 'Draft';
  await submit(f);
  assert.equal(name.value, 'Draft'); assert.equal(f.section.submit.disabled, false);
  assert.ok(f.toasts.length, 'a rejected save is visible');
  assert.doesNotMatch(f.toasts.join(' '), /demo_solo_lectura|^401$/);
  const statusNode = f.section.querySelector('#cnt-save-status');
  assert.ok(statusNode && !statusNode.hidden);
  if (status === 401) assert.match(statusNode.innerHTML, /\/admin\/login\?next=%2Fadmin%2Fdoctors%3Fitem%3Ddoctor-one/);
});

for (const failure of ['http', 'network']) test(`specialty ${failure} failure retains the current option and retries next open`, async t => {
  let calls = 0;
  const f = formRuntimeFixture(t, { screen: 'medicos', item: doctor,
    fetchOverride: (url, opts, response) => {
      if (url !== '/data/especialidades.json') return;
      if (++calls > 1) return response(200, { specialties: [{ nombre: 'General', cluster: 'general' }] });
      if (failure === 'network') throw new Error('offline');
      return response(503, {});
    } });
  const owner = f.run(); t.after(owner); await owner.ready;
  assert.equal(f.section.querySelector('#med-especialidad').value, 'General');
  assert.match(f.section.innerHTML, /No se pudo cargar la lista de especialidades/);
  await submit(f);
  assert.equal(JSON.parse(f.requests.find(r => r.options.method === 'PUT').options.body).especialidad, 'General');
  f.win.emit('popstate'); await flush(); await flush();
  assert.equal(calls, 2);
  assert.doesNotMatch(f.section.innerHTML, /No se pudo cargar la lista de especialidades/);
});

test('successful save renders canonical server values and confirms without a follow-up GET', async t => {
  const f = formRuntimeFixture(t, { screen: 'medicos', item: doctor,
    fetchOverride: (url, opts, response) => opts.method === 'PUT' && response(200, { ok: true, item: { ...doctor, nombre_pila: 'Canonical' } }) });
  const owner = f.run(); t.after(owner); await owner.ready;
  f.section.querySelector('#med-nombre-pila').value = 'Draft';
  await submit(f);
  assert.equal(f.section.querySelector('#med-nombre-pila').value, 'Canonical');
  assert.ok(f.toasts.includes('Guardado.'));
  assert.equal(f.requests.filter(r => r.url === '/api/admin/content/doctors' && !r.options.method).length, 1);
});

for (const screen of ['servicios', 'centros', 'medicos']) for (const creating of [false, true]) test(`catalog ${screen} ${creating ? 'create' : 'edit'} preserves newer typing and updates the saved identity on retry`, async t => {
  const config = {
    servicios: { item: { id: 'service-one', nombre: 'Clinic', centro: 'center-one', modo: 'walkin' }, field: '#svc-nombre', key: 'nombre', url: 'services', id: 'service-one' },
    centros: { item: { slug: 'center-one', name: 'Center one' }, field: '#cen-nombre', key: 'name', url: 'centers', id: 'center-one' },
    medicos: { item: doctor, field: '#med-nombre-pila', key: 'nombre_pila', url: 'doctors', id: 'doctor-one' }
  }[screen];
  const pending = deferred();
  let writes = 0;
  const f = formRuntimeFixture(t, { screen, item: creating ? null : config.item,
    fetchOverride: (url, opts, response) => {
      if (!['POST', 'PUT'].includes(opts.method)) return;
      if (++writes === 1) return pending.promise;
      return response(200, { ok: true, item: { ...config.item, [config.key]: 'Newer unsaved edit' } });
    } });
  const owner = f.run(); t.after(owner); await owner.ready;
  f.section.querySelector(config.field).value = 'Submitted';
  await submit(f);
  f.section.querySelector(config.field).value = 'Newer unsaved edit';
  pending.resolve({ status: 200, ok: true, json: async () => ({ ok: true, item: { ...config.item, [config.key]: 'Submitted' } }) });
  for (let i = 0; i < 10; i++) await flush();
  assert.equal(f.section.querySelector(config.field).value, 'Newer unsaved edit');
  assert.equal(f.section.submit.disabled, false);
  assert.match(f.section.querySelector('#cnt-save-status').innerHTML, /Cambios sin guardar/);
  await submit(f);
  const writesSent = f.requests.filter(r => ['POST', 'PUT'].includes(r.options.method));
  assert.equal(writesSent.length, 2);
  assert.equal(writesSent[1].options.method, 'PUT');
  assert.equal(writesSent[1].url, '/api/admin/content/' + config.url + '/' + config.id);
  assert.equal(JSON.parse(writesSent[1].options.body)[config.key], 'Newer unsaved edit');
});

test('save acknowledged after navigation still reports its outcome', async t => {
  const pending = deferred();
  const f = formRuntimeFixture(t, { screen: 'medicos', item: doctor,
    fetchOverride: (url, opts) => opts.method === 'PUT' && pending.promise });
  const owner = f.run(); t.after(owner); await owner.ready;
  await submit(f); f.win.emit('popstate');
  pending.resolve({ status: 200, ok: true, json: async () => ({ ok: true, item: doctor }) });
  await flush(); await flush();
  assert.ok(f.toasts.includes('Guardado.'), 'late acknowledgements must not disappear');
});

for (const modo of ['solicitud', 'walkin']) test(`${modo} service preserves closures with no selected weekday`, async t => {
  const closure = { fecha: '2026-11-01', tipo: 'cerrado', motivo: 'Licencia' };
  const item = { id: 'svc-one', nombre: 'Clinic', centro: 'center-one', modo, agenda: { franjas: [], excepciones: [closure] } };
  const f = formRuntimeFixture(t, { screen: 'servicios', item });
  const owner = f.run(); t.after(owner); await owner.ready; await submit(f);
  const doc = JSON.parse(f.requests.find(r => r.options.method === 'PUT').options.body);
  assert.deepEqual(doc.agenda?.excepciones || doc.excepciones, [closure]);
  if (modo === 'walkin') assert.equal(doc.agenda, undefined, 'no appointment agenda for walk-in');
});

for (const screen of ['medicos', 'servicios']) {
  for (const [from, to] of [['', ''], ['2026-11-01', ''], ['2026-11-03', '2026-11-01']]) {
    test(`${screen} blocks incomplete or reversed closure ${from}/${to} beside its date field`, async t => {
      const f = formRuntimeFixture(t, { screen, item: screen === 'medicos' ? doctor : null });
      const owner = f.run(); t.after(owner); await owner.ready;
      const row = runtimeElement(f.doc), start = runtimeElement(f.doc), end = runtimeElement(f.doc);
      start.value = from; end.value = to; start.owner = f.section; end.owner = f.section;
      row.querySelector = sel => ({ '[data-exc-desde-fecha]': start, '[data-exc-hasta-fecha]': end })[sel] || null;
      f.section.closureRows = [row];
      await submit(f);
      assert.equal(f.requests.filter(r => r.options.method === 'PUT' || r.options.method === 'POST').length, 0);
      const field = !from ? start : end;
      assert.equal(field.getAttribute('aria-invalid'), 'true');
      assert.ok(field.getAttribute('aria-describedby'));
      assert.equal(f.doc.activeElement, field);
      assert.equal(start.value, from); assert.equal(end.value, to);
    });
  }
}

for (const status of [400, 401, 403]) test(`late rejected save ${status} is not swallowed after navigation`, async t => {
  const pending = deferred();
  const f = formRuntimeFixture(t, { screen: 'medicos', item: doctor,
    fetchOverride: (url, opts) => opts.method === 'PUT' && pending.promise });
  const owner = f.run(); t.after(owner); await owner.ready;
  await submit(f); f.win.emit('popstate');
  pending.resolve({ status, ok: false, json: async () => ({ error: status === 401 ? 'no_autenticado' : status === 403 ? 'demo_solo_lectura' : 'falta nombre' }) });
  await flush(); await flush();
  assert.ok(f.toasts.length);
  assert.doesNotMatch(f.toasts.join(' '), /Guardado|demo_solo_lectura|no_autenticado/);
});

for (const body of [{ ok: true }, { ok: 'true', item: doctor }, null]) test(`malformed acknowledgement ${JSON.stringify(body)} never confirms a save`, async t => {
  const f = formRuntimeFixture(t, { screen: 'medicos', item: doctor,
    fetchOverride: (url, opts, response) => opts.method === 'PUT' && response(200, body) });
  const owner = f.run(); t.after(owner); await owner.ready;
  f.section.querySelector('#med-nombre-pila').value = 'Keep me'; await submit(f);
  assert.equal(f.section.querySelector('#med-nombre-pila').value, 'Keep me');
  assert.ok(f.toasts.length); assert.ok(!f.toasts.includes('Guardado.'));
});

test('pending save guards document departure; disposing reports an unconfirmed result', async t => {
  const pending = deferred();
  const f = formRuntimeFixture(t, { screen: 'medicos', item: doctor,
    fetchOverride: (url, opts) => opts.method === 'PUT' && pending.promise });
  const owner = f.run(); t.after(owner); await owner.ready;
  await submit(f);
  let blocked = false; f.win.emit('beforeunload', { preventDefault() { blocked = true; } });
  assert.equal(blocked, true);
  owner(); await flush(); await flush();
  assert.match(f.toasts.join(' '), /No se pudo confirmar el guardado/);
});

test('doctor date-range closures are sent intact', async t => {
  const closure = { tipo: 'cerrado', desde_fecha: '2026-11-01', hasta_fecha: '2026-11-09', motivo: 'Licencia' };
  const f = formRuntimeFixture(t, { screen: 'medicos', item: { ...doctor, centros: [{ centro: 'center-one', franjas: [], excepciones: [closure] }] } });
  const owner = f.run(); t.after(owner); await owner.ready; await submit(f);
  const doc = JSON.parse(f.requests.find(r => r.options.method === 'PUT').options.body);
  assert.deepEqual(doc.centros[0].excepciones, [closure]);
});

test('walk-in reopens its stored closures and carries them into appointment mode', async t => {
  const closure = { tipo: 'cerrado', fecha: '2026-11-01' };
  const f = formRuntimeFixture(t, { screen: 'servicios', item: { id: 'svc-one', nombre: 'Clinic', centro: 'center-one', modo: 'walkin', excepciones: [closure] } });
  const owner = f.run(); t.after(owner); await owner.ready;
  f.section.querySelector('#svc-c-turno').checked = true; await submit(f);
  const doc = JSON.parse(f.requests.find(r => r.options.method === 'PUT').options.body);
  assert.deepEqual(doc.agenda.excepciones, [closure]);
  assert.equal(doc.excepciones, undefined);
});
