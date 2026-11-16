// TASK-44 (26.10, product owner): a cancelled request reads by who cancelled — «Cancelada por vos» when the
// patient did, «Cancelada por el centro» when the desk did, «Cancelada» when the data does not say.
// The key (not the text) comes from the locale catalog; the server's `cancelada_por` picks it.
import test from 'node:test';
import assert from 'node:assert/strict';
import {runtime} from './lineage/runtime.mjs';

const file = 'static/platform/account-requests.mjs';
const names = ['turnoDt', 'esAprob', 'horaDe', 'cancelledLabel', 'mutedCardHtml', 'pedidaTexto'];

function context(T) {
  return runtime(file, names, {
    T, Dates: null, nombreServicio: () => 'Servicio sintético', centroInfo: {}, esc: String,
    turnoTexto: () => '', mutedAvatarHtml: () => '', MTC_ICO_REPRO: '',
  });
}

const keyAndFallback = (key, _vars, fallback) => `${key}=${fallback}`;

test('the card says who cancelled, from the server field', () => {
  const c = context(keyAndFallback);
  const html = por => c.mutedCardHtml({estado: 'cancelada', cancelada_por: por}, 0);
  assert.match(html('paciente'), /status_cancelled_by_patient=Cancelada por vos</);
  assert.match(html('centro'), /status_cancelled_by_center=Cancelada por el centro</);
});

test('no recorded actor reads «Cancelada» alone — never a guess', () => {
  const c = context(keyAndFallback);
  for (const row of [{estado: 'cancelada'}, {estado: 'cancelada', cancelada_por: null}, {estado: 'cancelado', cancelada_por: ''}]) {
    const html = c.mutedCardHtml(row, 0);
    assert.match(html, /status_cancelled=Cancelada</);
    assert.doesNotMatch(html, /por vos|por el centro/);
  }
});

test('the text itself comes from the catalog: a translation reaches the card', () => {
  const c = context((key) => ({
    'account.mine.requests.status_cancelled_by_patient': 'Cancelled by you',
    'account.mine.requests.status_cancelled_by_center': 'Cancelled by the center',
    'account.mine.requests.status_cancelled': 'Cancelled',
  })[key]);
  assert.equal(c.cancelledLabel({cancelada_por: 'paciente'}), 'Cancelled by you');
  assert.equal(c.cancelledLabel({cancelada_por: 'centro'}), 'Cancelled by the center');
  assert.equal(c.cancelledLabel({}), 'Cancelled');
});
