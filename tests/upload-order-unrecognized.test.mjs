// TASK-52 (26.10, persona Marcelo, 1280 px): «Subí tu orden» read the order
// but recognised nothing it asks for, and the page ended in a grey sentence —
// «buscá … desde el inicio» — with no button and no link. The same screen as
// every other upload problem now: the message, what was uploaded, the upload
// control to try another photo, and a link to search the study or specialty.
import test from 'node:test';
import assert from 'node:assert/strict';
import {runtime} from './lineage/runtime.mjs';

function context(extra = {}) {
  const host = {innerHTML: ''};
  const calls = {wired: 0, step: []};
  const c = runtime('app/modules/turnos/upload-order.mjs',
    ['renderTurnos', 'stepNoReconocido', 'sidewaysLinksHtml', 'previewImgHtml', 'sinAnalisis'], {
      host, esc: String, Search: {}, Listing: {}, SERV: [{id: 'x'}], CENT: {},
      $: (s) => (s === '#prep-turnos' ? host : null),
      stopReading() {}, setPage(t) { calls.step.push(t); }, initialTitle: 'Subí tu orden', initialSub: '',
      zoneHtml: () => '<div id="wz-upzone">ZONE</div>', TS_BOX: '<div id="prep-ts"></div>',
      wireUploadZone() { calls.wired++; },
      state: {ai: {verdict: 'parece_correcta'}, ordenPreview: ''}, servicioId: '',
      ...extra,
    });
  return {c, host, calls};
}

test('nothing recognised: try another photo with the same control, or search the catalog', () => {
  const {c, host, calls} = context();
  c.renderTurnos([], null);
  assert.match(host.innerHTML, /No reconocimos qué pide la orden/);
  assert.match(host.innerHTML, /id="wz-upzone"/, 'the upload control is right there');
  assert.equal(calls.wired, 1, 'and it works');
  assert.match(host.innerHTML, /<a href="\/">Buscar el estudio o la especialidad<\/a>/);
});
