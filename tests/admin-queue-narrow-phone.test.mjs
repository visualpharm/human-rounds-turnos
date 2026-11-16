// «Pacientes de hoy» under 360 px (09.11, a 320 px phone): the controls keep the two lines of
// 390 — search with its whole placeholder beside two icons, then the day with the funnel —
// instead of dropping the funnel to a third line and clipping «Paciente o médico». Pinned by
// selector and the declaration that does the work; the widths themselves are measured in the
// browser (cola_bar), these keep a later edit from quietly removing one of them.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const css = fs.readFileSync(new URL('../static/platform/admin-queue.css', import.meta.url), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, ' ');

const NARROW = '@media(max-width:359px){';
const BAR = '.adm5-main #sec-cola .queue-controls';

function narrowBody(sel) {
  const full = BAR + sel;
  const i = css.indexOf(full + '{');
  assert.notEqual(i, -1, 'missing rule ' + full);
  const media = css.lastIndexOf('@media', i);
  assert.equal(css.slice(media, media + NARROW.length), NARROW, full + ' must sit in the under-360 block');
  const start = i + full.length + 1;
  return css.slice(start, css.indexOf('}', start));
}

test('the empty print place lets go, and the print icon takes the download icon\'s place', () => {
  assert.match(narrowBody(' .queue-print-slot:empty'), /display:none/);
  assert.match(narrowBody(' .queue-actions:has(.queue-print-slot:not(:empty)) .tbl-export-btn'), /display:none/);
});

test('an empty search box gives the placeholder the clear button\'s place', () => {
  assert.match(narrowBody(' input[type=search]:placeholder-shown::-webkit-search-cancel-button'), /display:none/);
});

test('the day line holds the longest date with the funnel: narrower arrows, month and funnel, no gaps', () => {
  assert.match(narrowBody(''), /column-gap:6px/);
  // Since 2026-11-09 the arrows and «Hoy» are one segmented control (no gaps
  // inside); 4 px is what keeps its frame off the month button's frame.
  assert.match(narrowBody(' .queue-datenav'), /gap:4px/);
  assert.match(narrowBody(' .queue-seg-arrow'), /flex:0 0 32px/);
  assert.match(narrowBody(' :is(.queue-month-btn,.adm-filter-toggle)'), /flex:0 0 40px/);
  assert.match(narrowBody(' .queue-day-hoy'), /padding:0 4px/);
});
