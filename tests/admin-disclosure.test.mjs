// Progressive disclosure for every staff list (the product owner, 2026-10-26: «if there's
// no content, why to show all the ui? all over the admin panel»). The rule
// lives once in static/platform/admin-filter-controls.mjs; these pin it.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {
  disclosure, applyDisclosure, choiceCount, showIfChoice,
  FILTER_MIN_ROWS, filtersWorth, offerFilter,
  emptyStateHtml, noMatchHtml, aiNoticeHtml,
} from '../static/platform/admin-filter-controls.mjs';

function fakeSelect(values, current = '') {
  return {
    value: current,
    hidden: false,
    querySelectorAll: () => values.map(value => ({ value })),
  };
}

test('no rows at all is the empty state, whatever the filters say', () => {
  assert.equal(disclosure({ total: 0, shown: 0, filtered: false }), 'empty');
  assert.equal(disclosure({ total: 0, shown: 0, filtered: true }), 'empty');
  assert.equal(disclosure({}), 'empty');
});

test('rows hidden by filters are a different state from no rows', () => {
  assert.equal(disclosure({ total: 4, shown: 0, filtered: true }), 'no-match');
  assert.equal(disclosure({ total: 4, shown: 2, filtered: true }), 'data');
  assert.equal(disclosure({ total: 4, shown: 4, filtered: false }), 'data');
});

test('the state is written on the screen root for the shell CSS', () => {
  const attrs = {};
  const root = { setAttribute: (k, v) => { attrs[k] = v; } };
  assert.equal(applyDisclosure(root, 'empty'), 'empty');
  assert.equal(attrs['data-disclosure'], 'empty');
  assert.equal(applyDisclosure(null, 'data'), 'data');
});

test('a filter with a single value in the data is hidden', () => {
  const one = fakeSelect(['', 'med-1']);
  assert.equal(choiceCount(one), 1);
  assert.equal(showIfChoice(one), false);
  assert.equal(one.hidden, true);

  const two = fakeSelect(['', 'med-1', 'med-2']);
  assert.equal(showIfChoice(two), true);
  assert.equal(two.hidden, false);

  const none = fakeSelect(['']);
  assert.equal(showIfChoice(none), false);
});

test('a filter that is set stays visible so it can be cleared', () => {
  const set = fakeSelect(['', 'med-1'], 'med-1');
  assert.equal(showIfChoice(set), true);
  assert.equal(set.hidden, false);
});

test('a fixed-option filter counts the values the data actually has', () => {
  const sug = fakeSelect(['', 'aprobar', 'rechazar']);
  assert.equal(showIfChoice(sug, 1), false);
  assert.equal(showIfChoice(sug, 2), true);
  // A non-empty default (Pendientes) is not «set»; another value is.
  const estado = fakeSelect(['pendiente', 'aprobada', 'todas'], 'pendiente');
  assert.equal(showIfChoice(estado, 1, 'pendiente'), false);
  estado.value = 'aprobada';
  assert.equal(showIfChoice(estado, 1, 'pendiente'), true);
});

// Product owner 05.09 C1, 21.10, 26.10: «fewer than ~10 records — do not draw filters
// at all… one shared helper». One threshold, here, for every list.
test('a list shorter than FILTER_MIN_ROWS offers no filter; a set one stays', () => {
  assert.equal(FILTER_MIN_ROWS, 10);
  assert.equal(filtersWorth(9), false);
  assert.equal(filtersWorth(10), true);
  assert.equal(filtersWorth(undefined), false);
  assert.equal(offerFilter(3, false, 9), false, 'short list: nothing to filter');
  assert.equal(offerFilter(3, false, 10), true);
  assert.equal(offerFilter(1, false, 40), false, 'one value: still nothing to choose');
  assert.equal(offerFilter(1, true, 2), true, 'a set filter stays so it can be cleared');
  assert.equal(offerFilter(2, false), true, 'no row count: not a list filter (state → history)');

  const doctors = fakeSelect(['', 'med-1', 'med-2']);
  assert.equal(showIfChoice(doctors, null, '', 9), false);
  assert.equal(doctors.hidden, true);
  assert.equal(showIfChoice(doctors, null, '', 12), true);
  const set = fakeSelect(['', 'med-1', 'med-2'], 'med-2');
  assert.equal(showIfChoice(set, null, '', 3), true);
});

test('the phone «Filtros» toggle goes when no filter of its row is shown', () => {
  const css = fs.readFileSync(new URL('../static/root/admin-direct.css', import.meta.url), 'utf8');
  assert.match(css, /:not\(:has\(select\.adm-sel:not\(\[hidden\]\)\)\) \[data-filter-toggle\]\{display:none/);
});

test('the empty state is one readable block: what appears here, and the action', () => {
  const html = emptyStateHtml({
    title: 'Todavía no hay <solicitudes>.',
    text: 'Cuando un paciente pide un turno, aparece acá.',
    action: { label: 'Crear turno', accent: true, attrs: { 'data-x': '1' } },
  });
  assert.match(html, /class="adm-empty-state"/);
  assert.match(html, /Todavía no hay &lt;solicitudes&gt;\./);
  assert.match(html, /class="adm-empty-text">Cuando un paciente/);
  assert.match(html, /class="btn btn-accent" data-x="1">Crear turno<\/button>/);
  assert.doesNotMatch(emptyStateHtml({ title: 'Nada' }), /<button|adm-empty-text/);
});

test('the no-match state offers to clear the filters', () => {
  const html = noMatchHtml({ title: 'Ninguna coincide.', clearLabel: 'Quitar filtros' });
  assert.match(html, /data-empty-state="no-match"/);
  assert.match(html, /<button type="button" class="btn" data-clear-filters="">Quitar filtros<\/button>/);
});

test('the AI notice only accompanies AI content', () => {
  assert.equal(aiNoticeHtml({ hasContent: false, text: 'IA' }), '');
  assert.equal(aiNoticeHtml({ hasContent: true, text: 'Contenido <IA>', kind: 'clinical' }),
    '<p class="adm-ai-notice" data-ai-notice="clinical">Contenido &lt;IA&gt;</p>');
});

test('the shell CSS hides the data tools and the AI notice outside the data state', () => {
  const css = fs.readFileSync(new URL('../static/root/admin-direct.css', import.meta.url), 'utf8');
  for (const state of ['empty', 'loading']) {
    assert.match(css, new RegExp(`\\[data-disclosure="${state}"\\] \\.adm-band`));
    assert.match(css, new RegExp(`\\[data-disclosure="${state}"\\] \\[data-ai-notice\\]`));
  }
  assert.match(css, /\[data-disclosure="no-match"\] \[data-ai-notice\]/);
  // Readable: the empty state is body-size dark text, not gray small print.
  assert.match(css, /\.adm-empty-state\{[^}]*font-size:16px/);
  assert.match(css, /\.adm-empty-state\{[^}]*color:var\(--text\)/);
});
