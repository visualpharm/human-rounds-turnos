import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

globalThis.SANA_COUNTRY_PHONE = JSON.parse(readFileSync(new URL('../packs/ar/country.json', import.meta.url), 'utf8')).phone;
const {detalleHtml} = await import('../static/platform/admin-solicitudes.mjs');

// Journey 2026-09-16: admin-app.js built the "Pidió jue 17, 09:00" line
// (pide[2]) but the detail panel only drew pide[0] and pide[1], so the desk
// never saw which time the patient chose or that it was already taken.
const base = pide => ({id: 'r1', sug: 'ok', nombre: 'Ana Pérez', meta: 'DNI 1', pide,
  aprobarHtml: '', rechazarHtml: ''});
const SVC = {v: '<a href="/admin/services?item=svc">Cardiología</a>'};
const CEN = {v: 'Hospital · Hoy 08:00'};

test('the detail panel shows the slot the patient asked for', () => {
  const h = detalleHtml(base([SVC, CEN, {v: '<span class="sol-pedido">Pidió jue 17, 09:00</span>'}]));
  assert.match(h, /<div class="sol-pide-line3"><span class="sol-pedido">Pidió jue 17, 09:00<\/span><\/div>/);
});

test('the detail panel shows that the requested slot is already taken', () => {
  const h = detalleHtml(base([SVC, CEN, {v: '<span class="sol-pedido">Pidió jue 17, 09:00, ya está tomado</span>'}]));
  assert.match(h, /sol-pide-line3[^>]*><span class="sol-pedido">Pidió jue 17, 09:00, ya está tomado/);
});

test('no requested slot → no empty third line', () => {
  assert.doesNotMatch(detalleHtml(base([SVC, CEN])), /sol-pide-line3"/);
});

// Reference-deployment counterpart, 2026-10-24: «Hospital de Pinamar · Vie 16 oct, 15:40» under the
// service read as the appointment date, but it was the date the request
// arrived (already in the «Recibida» column). The line is the center alone.
const APP = readFileSync(new URL('../static/root/admin-app.js', import.meta.url), 'utf8');

test('the line under the service is what the patient tried to book, never the received date', () => {
  const start = APP.indexOf('const pedidoPartes = [];');
  const end = APP.indexOf("{v: pedidoPartes.join(' · ')}", start);
  assert.ok(start > 0 && end > start, 'pedidoPartes block not found in admin-app.js');
  const block = APP.slice(start, end);
  // order: slot (its hold) · center · requested doctor, each only when known
  assert.ok(block.indexOf('s.turno_pedido') < block.indexOf('s.centro_slug'));
  assert.ok(block.indexOf('s.centro_slug') < block.indexOf('s.medico_pedido'));
  // the center sits in its own span so Derivar can rewrite it alone
  assert.match(block, /if\(s\.centro_slug\) pedidoPartes\.push\(`<span class="sol-pide-centro">\$\{esc\(cenNombre\(s\.centro_slug\)\)\}<\/span>`\)/);
  assert.match(block, /if\(s\.medico_pedido\) pedidoPartes\.push\(esc\(s\.medico_pedido\)\)/);
  assert.doesNotMatch(block, /fmtCreatedShort|s\.created|medico_asignado/);
});

test('the received date still lives in the «Recibida» column of the list', () => {
  assert.match(APP, /cuando: pend \? fmtCreatedShort\(s\.created\) : ''/);
});

test('slot · center · doctor render as ONE line; no center → no empty second line', () => {
  const una = detalleHtml(base([SVC, {v: '<span class="sol-pedido">Pidió jue 26 nov, 10:00</span> · Hospital de Pinamar · Sebastian Frias'}]));
  assert.match(una, /<div class="sol-pide-line2"><span class="sol-pedido">Pidió jue 26 nov, 10:00<\/span> · Hospital de Pinamar · Sebastian Frias<\/div>/);
  assert.doesNotMatch(una, /sol-pide-line3/);
  const con = detalleHtml(base([SVC, {v: 'Hospital de Pinamar'}]));
  assert.match(con, /<div class="sol-pide-line2">Hospital de Pinamar<\/div>/);
  assert.doesNotMatch(con, /Vie 16 oct|15:40/);
  assert.doesNotMatch(detalleHtml(base([SVC, {v: ''}])), /sol-pide-line2/);
});
