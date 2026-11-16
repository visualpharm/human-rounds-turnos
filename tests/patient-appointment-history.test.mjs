import test from 'node:test';
import assert from 'node:assert/strict';
import {runtime} from './lineage/runtime.mjs';

const file = 'static/platform/account-requests.mjs';
function context() {
  return runtime(file, ['turnoDt', 'esPasado', 'esActivo', 'fechaConcreta']);
}

test('pending visits with a past date are history, without changing their status', () => {
  const c = context();
  const row = {estado: 'pendiente', turno_pasado: true, hold: {dia: '2001-01-01', hora: '09:00'}};
  assert.equal(c.esPasado(row), true);
  assert.equal(c.esActivo(row), false);
  assert.equal(c.fechaConcreta(row), false);
  assert.equal(row.estado, 'pendiente');
});

test('future/missing date and cancelled rows retain distinct behavior', () => {
  const c = context();
  assert.equal(c.esPasado({estado: 'pendiente', turno_pasado: false}), false);
  assert.equal(c.esPasado({estado: 'pendiente'}), false);
  assert.equal(c.esPasado({estado: 'cancelada', turno_pasado: false}), true);
  assert.equal(c.esPasado({estado: 'aprobada', turno_dt: '2001-01-01T10:00:00-03:00'}), true);
  assert.equal(c.esPasado({estado: 'aprobada', turno_dt: '2099-01-01T10:00:00-03:00'}), false);
  assert.equal(c.esPasado({estado: 'aprobada', turno_dt: '2001-01-01T10:00:00'}), false,
    'a payload without timezone or server flag must not infer the browser timezone');
});

test('rendered historical pending card shows its date and unresolved status, not ongoing review or actions', () => {
  const c = runtime(file, ['turnoDt', 'mutedCardHtml'], {
    nombreServicio: () => 'Servicio sintético', centroInfo: {}, esc: String,
    turnoTexto: t => typeof t === 'object' ? `${t.fecha} ${t.hora}` : t,
    mutedAvatarHtml: () => '', MTC_ICO_REPRO: '', pedidaTexto: () => '',
  });
  const html = c.mutedCardHtml({estado: 'pendiente', hold: {dia: '2001-01-01', hora: '09:00'}}, 0);
  assert.match(html, /req-muted/);
  assert.match(html, /2001-01-01 09:00/);
  assert.match(html, /El hospital no llegó a confirmarla/);
  assert.doesNotMatch(html, /El hospital la revisa|data-cancel|\.ics|Aprobada/);
});
