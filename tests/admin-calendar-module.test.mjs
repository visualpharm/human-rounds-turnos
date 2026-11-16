import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { mount } from '../static/platform/admin-content.mjs';

const source = readFileSync(new URL('../static/platform/admin-content.mjs', import.meta.url), 'utf8');
const calendarModule = new URL('../static/platform/calendar-grid.mjs', import.meta.url);
const flush = () => new Promise(resolve => setImmediate(resolve));

function fixture() {
  const styles = [], imports = [];
  let release;
  const blocked = new Promise(resolve => { release = resolve; });
  let finishRoute, styled;
  const routeGate = new Promise((_resolve, reject) => { finishRoute = () => reject(new Error('fixture ends before route')); });
  const styleReady = new Promise(resolve => { styled = resolve; });
  const document = {
    head: { appendChild(tag) { styles.push(tag); if (tag.id === 'sana-medcal-css') styled(); } },
    createElement() { return { id: '', textContent: '' }; },
    getElementById(id) { return styles.find(tag => tag.id === id) || null; },
    addEventListener() {}, removeEventListener() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
  const window = {
    SANA_ADMIN_SCREEN: 'medicos', SANA_STATIC: {},
    location: { pathname: '/admin/doctors', search: '' },
    history: {}, addEventListener() {}, removeEventListener() {},
  };
  document.defaultView = window;
  const owner = mount(document, {
    fetch: async () => { throw new Error('calendar CSS fixture must not route'); },
    importStatic: async name => {
      imports.push(name);
      if (name === 'platform/calendar-grid.mjs') { await blocked; return import(calendarModule); }
      // Stop before routing: this fixture tests the real CSS dependency only.
      if (name === 'platform/admin-hours-editor.mjs') return routeGate;
      return { load: async () => {}, get: (_key, fallback) => fallback, set() {} };
    },
  });
  owner.ready.catch(() => {});
  return { owner, styles, imports, release, finishRoute, styleReady };
}

test('admin calendar CSS waits for and uses the canonical ESM module', { timeout: 2000 }, async () => {
  const f = fixture();
  try {
    await flush();
    assert.ok(f.imports.includes('platform/calendar-grid.mjs'));
    assert.equal(f.styles.some(tag => tag.id === 'sana-medcal-css'), false);
    const calendar = await import(calendarModule);
    f.release();
    await f.styleReady;
    const style = f.styles.find(tag => tag.id === 'sana-medcal-css');
    assert.ok(style, 'resolved calendar module injects its actual CSS');
    assert.equal(style.textContent, calendar.css);
    assert.match(style.textContent, /\.medcal-grid\{/);
  } finally { f.owner(); f.release(); f.finishRoute(); await f.owner.ready; }
});

test('calendar import resolved after disposal cannot inject a stylesheet', async () => {
  const f = fixture();
  await flush();
  f.owner();
  f.release();
  f.finishRoute();
  await f.owner.ready;
  await flush();
  assert.equal(f.styles.some(tag => tag.id === 'sana-medcal-css'), false);
});

test('admin calendar has no compatibility global or fallback URL', () => {
  assert.doesNotMatch(source, new RegExp('window\\.Sana' + 'Calendar'));
  assert.doesNotMatch(source, /["']\/calendar-grid\.mjs["']/);
});
