// Bare icon buttons (no frame, no backing at rest) only in the search's own
// row (the product owner, 2026-11-09: "without a backing I asked only for the ones that
// come after the search, not all of them"). Every other icon button of the
// panel — the title line's actions, Recorrido's «Elegir pasos», the queue's
// interview settings — keeps the outlined panel button; on «Pacientes de hoy»
// the day's controls are outlined too and only the actions after the search
// and the funnel go bare.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, ' ');
const css = strip(fs.readFileSync(new URL('../static/root/admin-direct.css', import.meta.url), 'utf8'));
const queueCss = strip(fs.readFileSync(new URL('../static/platform/admin-queue.css', import.meta.url), 'utf8'));

function rules(sheet, sel) {
  const out = [];
  for (let i = sheet.indexOf(sel + '{'); i !== -1; i = sheet.indexOf(sel + '{', i + 1)) {
    const start = i + sel.length + 1;
    // Only a rule whose selector list starts here, not a longer selector ending in it.
    const prev = sheet[i - 1];
    if (prev && !/[}\s]/.test(prev)) continue;
    out.push(sheet.slice(start, sheet.indexOf('}', start)));
  }
  return out;
}

const ICONS = '.adm5-main :is(.cnt-gear-btn,.adm-filter-toggle)';
const SEARCH_ROWS = '.adm5-main :is(.adm-band,.cnt-toolbar,.queue-controls,.medcal-filters,.kn-toolbar) :is(.cnt-gear-btn,.adm-filter-toggle)';

test('the panel-wide icon rule sets the square only, never the bare look', () => {
  const base = rules(css, ICONS);
  assert.ok(base.length, 'the 44px icon rule is there');
  for (const b of base) {
    assert.match(b, /width:44px/);
    assert.doesNotMatch(b, /border-color:transparent|background:transparent/, 'no bare look outside the search rows');
  }
});

test('bare at rest only inside the rows that hold a search; framed on hover, focus and open', () => {
  const [rest] = rules(css, SEARCH_ROWS);
  assert.ok(rest, 'the search-row rule is there');
  assert.match(rest, /border-color:transparent/);
  assert.match(rest, /background:transparent/);
  const [lit] = rules(css, SEARCH_ROWS + ':is(:hover,:focus-visible,[aria-expanded="true"],[aria-pressed="true"],.on)');
  assert.ok(lit, 'the lit state is there');
  assert.match(lit, /border-color:var\(--border-strong\)/);
  assert.match(lit, /background:var\(--bg-2\)/);
});

test('Pacientes de hoy: only the actions after the search and the funnel go bare', () => {
  const [square] = rules(queueCss, '.adm5-main #sec-cola .queue-controls :is(.cnt-gear-btn,.adm-filter-toggle,.queue-icon-btn)');
  assert.ok(square);
  assert.doesNotMatch(square, /transparent/, 'the shared square of the row is not bare by itself');
  const [bare] = rules(queueCss, '.adm5-main #sec-cola .queue-controls :is(.queue-actions :is(.cnt-gear-btn,.queue-icon-btn),.adm-filter-toggle)');
  assert.ok(bare, 'the bare rule names the actions and the funnel');
  assert.match(bare, /border-color:transparent;background:transparent/);
  // The day's controls are the panel's outlined buttons.
  const [month] = rules(queueCss, '.adm5-main #sec-cola .queue-controls .queue-month-btn');
  assert.match(month, /border-color:var\(--border-strong\)/);
  const [seg] = rules(queueCss, '.queue-day-seg');
  assert.match(seg, /border:1px solid var\(--border-strong\)/);
  // The interview settings' gear lives outside the search row: outlined.
  const [cfg] = rules(queueCss, '.queue-entrevista-cfg .queue-icon-btn');
  assert.match(cfg, /border-color:var\(--border-strong\)/);
  assert.doesNotMatch(cfg, /transparent|margin-left:-/);
});
