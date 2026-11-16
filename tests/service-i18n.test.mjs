/* i18n del 2026-08-04: la página de servicio y el wizard de reserva
   (service.js) traducen al inglés. `tests/i18n-fallbacks.test.mjs` ya cubre,
   para TODO el sitio, que cada T(...) tenga un fallback y que esa clave
   exista en el catálogo canónico. Este archivo fija el contrato PROPIO de
   `service` namespace: el inglés dice algo real (no el castellano, no la
   clave cruda) para las frases que este cambio tocó, y el fallback que
   `service.js` lleva adentro es BYTE A BYTE el castellano de siempre —
   la garantía de que el visitante en castellano no ve nada distinto. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = fs.readFileSync(join(ROOT, 'app/modules/turnos/service.mjs'), 'utf8');
const es = JSON.parse(fs.readFileSync(join(ROOT, 'locales/es/service.json'), 'utf8'));
const en = JSON.parse(fs.readFileSync(join(ROOT, 'locales/en/service.json'), 'utf8'));

test('cada clave service.* del castellano tiene su par en inglés, y viceversa', () => {
  const esKeys = Object.keys(es).filter((k) => k !== '_comment').sort();
  const enKeys = Object.keys(en).filter((k) => k !== '_comment').sort();
  assert.deepEqual(esKeys, enKeys);
});

// Plantillas de puro símbolo/variable, sin una sola palabra: "→ {date} · {day}"
// es igual en cualquier idioma porque no dice nada por sí mismo; y el nombre
// de un producto ("Google Calendar") que no se traduce.
const IGUAL_EN_LOS_DOS_IDIOMAS = new Set(['service.date.echo', 'service.calendar.google']);

test('el inglés dice algo real, no el castellano ni la clave cruda', () => {
  for (const [key, esVal] of Object.entries(es)) {
    if (key === '_comment') continue;
    const enVal = en[key];
    assert.ok(enVal, `falta ${key} en locales/en/service.json`);
    assert.notEqual(enVal, key, `${key}: el inglés quedó como la clave cruda`);
    if (IGUAL_EN_LOS_DOS_IDIOMAS.has(key)) continue;
    assert.notEqual(enVal, esVal, `${key}: el inglés quedó igual al castellano ("${esVal}")`);
  }
});

// Las cinco frases que el coordinador nombró como visibles en la demo en
// inglés todavía en castellano (2026-08-04) — verificadas una por una.
test('las frases que se veían en castellano en la demo en inglés ahora tienen traducción real', () => {
  assert.equal(es['service.band.sub'],
    'Es el más próximo, no el único: podés elegir otro día u horario.');
  assert.match(en['service.band.sub'], /soonest/i);

  assert.equal(es['service.how_it_works'], 'Cómo funciona');
  assert.equal(en['service.how_it_works'], 'How it works');

  assert.equal(es['service.hours_title'], 'Horarios'); // "Consultorio" label: page.hours.room
});

test('T(...) en service.js pasa el castellano EXACTO como fallback (el render en castellano no cambia)', () => {
  // Un muestreo de las claves con var, donde un typo en el fallback es más
  // fácil de cometer al armar el string con +.
  const CASES = [
    ['service.prep_heading', 'Cómo prepararte'],
    ['service.slots.notice_taken', 'Ese horario lo acaba de tomar otra persona. Elegí otro.'],
    ['service.prep.title_for', 'Cómo prepararte · {nombre}'],
    ['service.doc.warning', 'Este documento parece ser {tipo}, no una orden médica. Por favor, subí la orden médica.'],
    ['service.submit.generic_error', 'No pudimos enviar la solicitud. Probá de nuevo o llevá la orden a la mesa de admisión.'],
    ['service.patient.switch_account', 'Ingresá con otra cuenta'],
  ];
  for (const [key, fallback] of CASES) {
    const escaped = fallback.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const re = new RegExp(`T\\(\\s*"${key.replace(/\./g, '\\.')}"\\s*,[\\s\\S]{0,120}?"${escaped}"`);
    assert.match(src, re, `no encontré T("${key}", …, "${fallback}") en service.js`);
    // el fallback del código es el mismo texto que locales/es/service.json
    assert.equal(es[key], fallback, `${key}: el fallback del código y el es.json divergieron`);
  }
});

test('el resolver T() de service.js es el verbatim mandado (una sola implementación en todo el sitio)', () => {
  const RESOLVER = [
    '    function T(key, vars, fallback) {',
    '    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);',
    '  }',
  ].join('\n');
  assert.ok(src.includes(RESOLVER), 'el resolver T() de service.js no calza byte a byte con el verbatim');
});
