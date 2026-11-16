// El pronóstico de asistencia («Historial», patient_ranking.py) como UNA pieza
// compartida (static/platform/attendance-forecast.mjs): tres barras, el número
// de barras llenas es el NIVEL (3/2/1), «Sin historial» es un guión, y el
// ícono de Información del encabezado abre la explicación (responsable del producto, 2026-10-27:
// «three bars, not five», «an Information icon in its header»).
// Requisitos F1–F3 de la pantalla de pacientes del médico.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as F from '../static/platform/attendance-forecast.mjs';

const ROOT = new URL('..', import.meta.url);
const read = path => readFileSync(new URL(path, ROOT), 'utf8');

// T con registro de claves: cada texto visible sale del catálogo con su
// castellano de respaldo.
function recordingT() {
  const keys = [];
  const T = (key, vars, fallback) => {
    keys.push(key);
    let out = fallback;
    for (const k of Object.keys(vars || {})) out = out.split('{' + k + '}').join(String(vars[k]));
    return out;
  };
  return { T, keys };
}

const filled = html => (html.match(/med-occupancy-segment is-filled/g) || []).length;
const segments = html => (html.match(/med-occupancy-segment/g) || []).length;

test('F2: tres bandas; el slug retirado «suele-venir» se lee como «viene-siempre»', () => {
  assert.deepEqual(F.BANDS.map(b => b.slug), ['viene-siempre', 'falta-a-veces', 'falta-seguido']);
  assert.equal(F.canonicalBand('suele-venir'), 'viene-siempre');
  assert.equal(F.canonicalBand('falta-a-veces'), 'falta-a-veces');
  assert.equal(F.canonicalBand(''), '');
  const { T } = recordingT();
  assert.equal(F.bandLabel({ banda: 'suele-venir', etiqueta: 'Suele venir' }, T), 'Viene siempre');
  assert.equal(F.bandLabel({ banda: 'falta-seguido', etiqueta: 'Falta seguido' }, T), 'Falta seguido');
});

test('F1: el nivel, no el puntaje, decide cuántas barras van llenas', () => {
  assert.equal(F.level({ nivel: 'good', puntaje: 71 }), 3);
  assert.equal(F.level({ nivel: 'good', puntaje: 99 }), 3);
  assert.equal(F.level({ nivel: 'warn', puntaje: 64 }), 2);
  assert.equal(F.level({ nivel: 'bad', puntaje: 12 }), 1);
  assert.equal(F.level({ sin_historial: true, nivel: 'none' }), 0);
  assert.equal(F.level(null), 0);
});

test('F1: tres barras con nombre accesible (nivel + en cuántos turnos se basa); color sólo en warn/bad', () => {
  const { T } = recordingT();
  const good = F.barsHtml({ banda: 'viene-siempre', nivel: 'good', puntaje: 91, hechos: 7 }, T);
  assert.equal(segments(good), 3);
  assert.equal(filled(good), 3);
  assert.match(good, /role="img" aria-label="Viene siempre · según 7 turnos"/);
  assert.match(good, /queue-forecast--good/);
  const warn = F.barsHtml({ banda: 'falta-a-veces', nivel: 'warn', puntaje: 60, hechos: 1 }, T);
  assert.equal(filled(warn), 2);
  assert.match(warn, /aria-label="Falta a veces · según 1 turno"/);
  assert.match(warn, /queue-forecast--warn/);
  const bad = F.barsHtml({ banda: 'falta-seguido', nivel: 'bad', puntaje: 20, hechos: 5 }, T);
  assert.equal(filled(bad), 1);
  assert.match(bad, /queue-forecast--bad/);
  // el porcentaje no se pinta ni se nombra: menos precisión, a propósito
  assert.doesNotMatch(good + warn + bad, /%/);
  const none = F.barsHtml({ sin_historial: true, nivel: 'none' }, T);
  assert.equal(segments(none), 0);
  assert.match(none, /aria-label="Sin historial">—</);
});

test('F3 en el teléfono (sin encabezado): las barras de cada fila abren la misma explicación con lo de ese paciente', () => {
  const { T } = recordingT();
  const r = { banda: 'falta-a-veces', nivel: 'warn', puntaje: 60, hechos: 4, motivos: ['1 ausencia', '3 turnos a los que vino'] };
  const html = F.cellHtml(r, T);
  assert.match(html, /^<button type="button" class="forecast-bars-btn" data-forecast-info="" data-no-clip-pop aria-expanded="false" aria-controls="forecast-info-pop"/);
  assert.equal(filled(html), 2);
  assert.match(html, /data-forecast-patient="Falta a veces · según 4 turnos"/);
  assert.match(html, /data-forecast-why="1 ausencia\n3 turnos a los que vino"/);
  // sin historial no hay nada que explicar de ese paciente: guión, no botón
  assert.doesNotMatch(F.cellHtml({ sin_historial: true }, T), /button/);
  const pop = F.popoverHtml(T, { patient: 'Falta a veces · según 4 turnos', why: '1 ausencia\n3 turnos a los que vino' });
  assert.match(pop, /^<p class="forecast-pop-patient">Falta a veces · según 4 turnos<\/p><ul class="forecast-pop-why"><li>1 ausencia<\/li><li>3 turnos a los que vino<\/li><\/ul>/);
});

test('F3: el botón de Información es un botón real con nombre, ícono Icons8 del pack y sin ordenar la columna', () => {
  const { T, keys } = recordingT();
  const html = F.infoButtonHtml(T);
  assert.match(html, /^<button type="button" class="forecast-info"/);
  assert.match(html, /data-forecast-info=""/);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /aria-controls="forecast-info-pop"/);
  assert.match(html, /aria-label="Qué significa Historial"/);
  assert.match(html, /data-no-clip-pop/);
  assert.match(html, /class="forecast-info-icon" aria-hidden="true"/);
  assert.doesNotMatch(html, /<svg/);
  assert.ok(keys.includes('admin.queue.forecast.info_label'));
  const css = read('static/root/admin-direct.css');
  assert.match(css, /\.forecast-info-icon\{[^}]*mask:url\(\/static\/icons\/info-sf-regular\.svg\)/);
  assert.ok(readFileSync(new URL('static/icons/info-sf-regular.svg', ROOT)).length > 200);
});

test('F3: la explicación dice qué cuenta y qué dicen las tres barras; todo por catálogo', () => {
  const { T, keys } = recordingT();
  const html = F.popoverHtml(T);
  assert.match(html, /Cómo vino a sus turnos anteriores/);
  assert.match(html, /si vino, si faltó o si canceló sobre la hora/);
  for (const [n, label] of [[3, 'Viene siempre'], [2, 'Falta a veces'], [1, 'Falta seguido']]) {
    const li = new RegExp('<li class="forecast-legend-row">(?:(?!</li>).)*data-filled="' + n + '"(?:(?!</li>).)*' + label + '</span></li>');
    assert.match(html, li, label);
  }
  assert.match(html, /—<\/span><span>Sin historial todavía<\/span>/);
  for (const k of ['admin.queue.forecast.what_counts', 'admin.queue.forecast.legend_none',
    'admin.queue.patients.band.always_comes', 'admin.queue.patients.band.sometimes_misses', 'admin.queue.patients.band.often_misses']) {
    assert.ok(keys.includes(k), k);
  }
  // es + en a mano, con las mismas claves
  for (const lang of ['es', 'en']) {
    const cat = JSON.parse(read(`locales/${lang}/admin.queue.json`));
    for (const k of ['admin.queue.forecast.info_label', 'admin.queue.forecast.what_counts', 'admin.queue.forecast.legend_none',
      'admin.queue.forecast.based_on_one', 'admin.queue.forecast.based_on']) {
      assert.ok(cat[k], `${lang}: ${k}`);
    }
    assert.equal(cat['admin.queue.patients.band.usually_comes'], undefined, `${lang}: la cuarta banda no existe más`);
  }
});

test('F3: abrir y cerrar — hover, foco, toque, Escape y un toque afuera', () => {
  const doc = fakeDocument();
  const { T } = recordingT();
  const dispose = F.bind(doc, T);
  const btn = doc.button;
  const open = () => doc.pop && !doc.pop.hidden;
  doc.fire('pointerover', btn, { pointerType: 'mouse' });
  assert.ok(open(), 'el mouse encima la abre');
  assert.equal(btn.getAttribute('aria-expanded'), 'true');
  doc.fire('pointerout', btn, { pointerType: 'mouse', relatedTarget: doc.body });
  doc.runTimers();
  assert.ok(!open(), 'al irse el mouse se cierra');
  doc.fire('click', btn);
  assert.ok(open(), 'un toque la abre y queda abierta');
  doc.fire('pointerout', btn, { pointerType: 'mouse', relatedTarget: doc.body });
  doc.runTimers();
  assert.ok(open(), 'abierta con un toque no se cierra al mover el mouse');
  doc.fire('click', doc.body);
  assert.ok(!open(), 'un toque afuera la cierra');
  doc.fire('focusin', btn);
  assert.ok(open(), 'el foco del teclado la abre');
  doc.fire('keydown', btn, { key: 'Escape' });
  assert.ok(!open(), 'Escape la cierra');
  assert.equal(btn.getAttribute('aria-expanded'), 'false');
  dispose();
  assert.equal(doc.listenerCount(), 0, 'dispose saca todos los listeners');
});

// Un documento mínimo: el botón del encabezado, el body, y lo que bind() crea.
function fakeDocument() {
  const listeners = new Map();
  const timers = [];
  function el(tag, attrs = {}) {
    const a = new Map(Object.entries(attrs));
    const node = {
      tagName: tag.toUpperCase(), nodeType: 1, hidden: false, style: {}, children: [], parentElement: null, innerHTML: '', id: '',
      setAttribute(k, v) { a.set(k, String(v)); if (k === 'id') this.id = String(v); },
      getAttribute(k) { return a.has(k) ? a.get(k) : null; },
      hasAttribute(k) { return a.has(k); },
      removeAttribute(k) { a.delete(k); },
      appendChild(c) { c.parentElement = this; this.children.push(c); return c; },
      remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(c => c !== this); this.parentElement = null; },
      contains(o) { for (let n = o; n; n = n.parentElement) if (n === this) return true; return false; },
      closest(sel) {
        for (let n = this; n; n = n.parentElement) {
          if (sel === '[data-forecast-info]' && n.hasAttribute && n.hasAttribute('data-forecast-info')) return n;
          if (sel === '#forecast-info-pop' && n.id === 'forecast-info-pop') return n;
        }
        return null;
      },
      focus() {},
      getBoundingClientRect() { return { left: 100, top: 40, right: 120, bottom: 60, width: 20, height: 20 }; },
      get offsetHeight() { return 120; }, get offsetWidth() { return 300; }
    };
    return node;
  }
  const body = el('body');
  const th = el('th');
  body.appendChild(th);
  const button = el('button', { 'data-forecast-info': '', 'aria-expanded': 'false' });
  th.appendChild(button);
  const doc = {
    body, button, pop: null,
    defaultView: {
      innerWidth: 1440, innerHeight: 900,
      setTimeout(fn) { timers.push(fn); return timers.length; }, clearTimeout(id) { timers[id - 1] = null; },
      addEventListener(type, fn) { add('win:' + type, fn); }, removeEventListener(type, fn) { listeners.get('win:' + type)?.delete(fn); }
    },
    createElement(tag) { return el(tag); },
    getElementById(id) { return id === 'forecast-info-pop' ? doc.pop : null; },
    addEventListener(type, fn) { add(type, fn); },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    fire(type, target, extra = {}) {
      const ev = { type, target, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {}, ...extra };
      for (const fn of [...(listeners.get(type) || [])]) fn(ev);
      doc.pop = body.children.find(c => c.id === 'forecast-info-pop') || null;
    },
    runTimers() { while (timers.length) { const fn = timers.shift(); if (fn) fn(); } },
    listenerCount() { return [...listeners.values()].reduce((n, s) => n + s.size, 0); }
  };
  function add(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); }
  return doc;
}
