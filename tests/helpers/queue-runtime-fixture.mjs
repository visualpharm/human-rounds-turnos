import { mount } from '../../static/platform/admin-queue.mjs';
import * as FilterControls from '../../static/platform/admin-filter-controls.mjs';
import * as Forecast from '../../static/platform/attendance-forecast.mjs';

export const flush = () => new Promise(resolve => setImmediate(resolve));
function target() {
  const listeners = new Map();
  return {
    listeners,
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    emit(type, event = {}) { for (const fn of [...listeners.get(type) || []]) fn(event); },
    count() { return [...listeners.values()].reduce((n, set) => n + set.size, 0); }
  };
}
export function fixture(t, { queueDom = false, initialData = null, holdCalendar = false } = {}) {
  let resolveTable, rejectTable;
  const tablePromise = new Promise((resolve, reject) => { resolveTable = resolve; rejectTable = reject; });
  const tables = [], styles = [], owners = [], nodes = new Map(), streams = [], requests = [], toasts = [], modules = new Map(), timers = new Map();
  const calendarRequests = [];
  let nextTimer = 0;
  function host(id) {
    let html = '';
    return {
      id, style: {}, attributes: new Map(), parentElement: null,
      setAttribute(name, value) { this.attributes.set(name, String(value)); },
      removeAttribute(name) { this.attributes.delete(name); },
      getAttribute(name) { return this.attributes.get(name) || null; },
      get innerHTML() { return html; },
      set innerHTML(value) {
        html = String(value);
        // The controller's real render creates this one follow-up host; a
        // counted fixture node is sufficient to observe the rendered output.
        if (html.includes('id="cola-results"')) nodes.set('cola-results', host('cola-results'));
      }
    };
  }
  const doc = Object.assign(target(), {
    documentElement: { lang: 'es' },
    getElementById(id) { return nodes.get(id) || null; }, querySelector(selector) { return nodes.get(selector.replace(/^#/, '')) || null; }, querySelectorAll() { return []; },
    body: { classList: { add() {}, remove() {} } },
    head: { appendChild(el) { styles.push(el); if (el.id) nodes.set(el.id, el); } },
    createElement() { return { style: {}, setAttribute() {}, appendChild() {}, remove() { if (nodes.get(this.id) === this) nodes.delete(this.id); } }; }
  });
  if (queueDom) {
    const sec = host('sec-cola');
    nodes.set('sec-cola', sec);
    nodes.set('adm-h1', host('adm-h1'));
    nodes.set('queue-initial-data', { textContent: JSON.stringify(initialData || {}) });
  }
  const win = Object.assign(target(), {
    document: doc, SANA_STATIC: {},
    setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    matchMedia: () => ({ matches: false }),
    toast(message) { toasts.push(message); },
    sanaImportStatic(name) {
      if (modules.has(name)) return modules.get(name);
      if (name === 'platform/admin-session.mjs') return Promise.resolve({ confirmar: async () => false });
      if (name === 'platform/admin-table.mjs') return tablePromise;
      if (name === 'platform/admin-filter-controls.mjs') return Promise.resolve(FilterControls);
      if (name === 'platform/attendance-forecast.mjs') return Promise.resolve(Forecast);
      if (name === 'platform/format.mjs') {
        const digits = value => String(value == null ? '' : value).replace(/\D/g, '');
        const dni = value => digits(value).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
        // Same shape as the real Format.dniHtml (the click lives there, tested in formato.test.mjs).
        const dniHtml = value => dni(value) ? `<span class="dni-cp" role="button" tabindex="0" data-copy-dni="${digits(value)}">${dni(value)}</span>` : '';
        return Promise.resolve({ nombre: value => String(value || ''), edad: () => '', dni, digits, dniHtml });
      }
      if (name === 'platform/admin-preferences.mjs') return Promise.resolve({ load: async () => {}, get: (_key, fallback) => fallback, set() {} });
      // instalarHtml's own markup is pinned in hsi-sync.test.mjs; here it only
      // shows what the queue hands it and where the line lands.
      if (name === 'platform/hsi-sync.mjs') return Promise.resolve({ ensureCss() {},
        instalarHtml: (inst, o) => (inst ? `<p class="hsi-instalar"${(o && o.attrs) || ''}>${inst.guia}</p>` : '') });
      return Promise.resolve({});
    }
  });
  doc.defaultView = win;
  win.EventSource = function (url) {
    Object.assign(this, target(), { url, closes: 0, close() { this.closes++; } }); streams.push(this);
  };
  const saved = new Map(['document', 'window', 'location', 'fetch'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.assign(globalThis, {
    document: doc, window: win, location: { search: '', pathname: '/admin/cola' },
    fetch(url, options) {
      if (String(url).startsWith('/api/admin/day-queue/calendar?')) {
        if (holdCalendar) return new Promise((resolve, reject) => { calendarRequests.push({ url, options, resolve, reject }); });
        const query = new URL(url, 'https://test.invalid').searchParams;
        return Promise.resolve(new Response(JSON.stringify({ dia: query.get('dia'), dias: [], anterior: null, siguiente: null }), { status: 200 }));
      }
      return new Promise((resolve, reject) => { requests.push({ url, options, resolve, reject }); });
    }
  });
  t.after(async () => {
    for (const owner of owners) owner();
    for (const request of calendarRequests) request.resolve(new Response('{}', { status: 503 }));
    for (const request of requests) request.resolve(new Response('{}', { status: 503 }));
    resolveTable({ crear() { return { dispose() {} }; } });
    await Promise.allSettled(owners.map(owner => owner.ready));
    for (const [key, descriptor] of saved) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  });
  return {
    doc, win, tables, styles, rejectTable, nodes, streams, requests, calendarRequests, toasts, modules, timers,
    tick(delay) { for (const [id, timer] of [...timers]) { if (timer.delay === delay) { timers.delete(id); timer.fn(); } } },
    mount(root = doc, options = {}) { const owner = mount(root, options); owners.push(owner); return owner; },
    readyTable(configs = []) {
      resolveTable({ crear(config) { configs.push(config); const instance = { disposals: 0, fits: 0, autoFit() { this.fits++; }, programarFit() {}, dispose() { this.disposals++; } }; tables.push(instance); return instance; } });
    }
  };
}
export function click(f, selector, attrs = {}) {
  const button = {
    disabled: false,
    parentElement: { isConnected: true },
    getAttribute(name) { return attrs[name] ?? null; },
    closest(query) { return query === selector ? button : query === '.queue-attendance-btns' ? group : null; }
  };
  const group = { querySelectorAll: () => [button] };
  f.doc.emit('click', { target: button, preventDefault() {} });
  return button;
}
