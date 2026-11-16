// The desk's queue row says WHO cancelled (the server's `cancelada_por`, the actor of the «cancelada»
// event). The «Turno / motivo» cell says only «Por el paciente» / «Por el centro» (the Estado column
// already says «Cancelada»), and the cancelled appointment's time when no event records who — never
// "por el paciente" for all. The detail keeps the whole phrase «Cancelada por …» (design review
// 2026-11-04, C12). Same source-on-disk pattern as admin-cola.test.mjs: the real functions of
// admin-app.js are cut out and run with a T() that returns the Spanish fallback.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const APP = readFileSync(new URL('../static/root/admin-app.js', import.meta.url), 'utf8');

function fn(name) {
  const start = APP.indexOf('function ' + name + '(');
  assert.ok(start > 0, name + ' not found in admin-app.js');
  const end = APP.indexOf('\n}\n', start);
  return APP.slice(start, end + 2);
}

const T = (_key, _vars, fallback) => fallback;
const fmtTurno = x => (x ? 'turno ' + x : '');
const cancelacionTxt = new Function('T', fn('cancelacionTxt') + '; return cancelacionTxt;')(T);
const turnoOMotivoTxt = new Function('T', 'fmtTurno',
  fn('cancelacionTxt') + fn('cancelacionCeldaTxt') + fn('turnoOMotivoTxt') + '; return turnoOMotivoTxt;')(T, fmtTurno);

test('the queue cell says «Por el paciente» when the patient cancelled', () => {
  assert.equal(turnoOMotivoTxt({estado: 'cancelada', cancelada_por: 'paciente', turno_dt: 'x'}), 'Por el paciente');
});

test('the queue cell says «Por el centro» when the desk cancelled', () => {
  assert.equal(turnoOMotivoTxt({estado: 'cancelada', cancelada_por: 'centro', turno_dt: 'x'}), 'Por el centro');
});

test('with no one recorded the queue cell shows the cancelled appointment, never «Cancelada» again', () => {
  for (const por of [undefined, null, '']) {
    assert.equal(turnoOMotivoTxt({estado: 'cancelada', cancelada_por: por, turno_dt: '2026-11-05T09:00'}), 'turno 2026-11-05T09:00');
  }
  assert.equal(turnoOMotivoTxt({estado: 'cancelada'}), '—', 'no appointment either → the usual dash');
});

test('the detail keeps the whole phrase «Cancelada por …»', () => {
  assert.equal(cancelacionTxt({cancelada_por: 'paciente'}), 'Cancelada por el paciente');
  assert.equal(cancelacionTxt({cancelada_por: 'centro'}), 'Cancelada por el centro');
  assert.equal(cancelacionTxt({}), 'Cancelada');
  assert.match(fn('detalleResVM'), /s\.estado==='cancelada' \? cancelacionTxt\(s\) : turnoOMotivoTxt\(s\)/);
});

test('the row cell reads turnoOMotivoTxt, so it gets the short form', () => {
  assert.match(fn('unifiedRowVM'), /turno: pend \? '' : turnoOMotivoTxt\(s\)/);
});

test('a rejected row still shows its reason, not a cancellation text', () => {
  assert.equal(turnoOMotivoTxt({estado: 'rechazada', motivo_rechazo: 'Sin cupo'}), 'Sin cupo');
});

test('the texts are locale keys with the Spanish fallback', () => {
  const all = ['es', 'en', 'pt', 'fr', 'it', 'de', 'ru', 'uk', 'ja', 'zh'];
  const keys = [
    ['cancelada_por_paciente', all], ['cancelada_por_centro', all], ['cancelada_sin_actor', all],
    // the cell's short form: hand-kept es/en; the other eight are generated from them
    ['cancelada_celda_paciente', ['es', 'en']], ['cancelada_celda_centro', ['es', 'en']],
  ];
  for (const [key, langs] of keys) {
    assert.match(APP, new RegExp('T\\("admin\\.queue\\.solicitudes\\.' + key + '"'));
    for (const lang of langs) {
      const cat = JSON.parse(readFileSync(new URL('../locales/' + lang + '/admin.queue.json', import.meta.url), 'utf8'));
      assert.ok(cat['admin.queue.solicitudes.' + key], lang + ' lacks ' + key);
    }
  }
});
