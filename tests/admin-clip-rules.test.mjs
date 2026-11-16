// Panel rules that keep key text whole and dark (review 08.11, themes 6 and
// 15; the product owner R25/R26: «text is seen whole», key text dark grey). A conflict
// resolution in another slice once dropped them all without a red test, so
// each one is pinned here by its selector and the declaration that matters.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const css = fs.readFileSync(new URL('../static/root/admin-direct.css', import.meta.url), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, ' ');

// The body of the rule whose selector list is exactly `sel` (first match).
function body(sel) {
  const i = css.indexOf(sel + '{');
  assert.notEqual(i, -1, 'missing rule ' + sel);
  const start = i + sel.length + 1;
  return css.slice(start, css.indexOf('}', start));
}

test("a row's name in a resizable table wraps instead of ending in an ellipsis (A9)", () => {
  const b = body('.tbl-anchos td.cnt-td-link');
  assert.match(b, /white-space:normal/);
  assert.match(b, /text-overflow:clip/);
});

test('«Consultas por especialidad» becomes one card per specialty on a phone (E1)', () => {
  const at = css.indexOf('table.cnt-table.rep-esp-table thead{display:none;}');
  assert.notEqual(at, -1, 'the phone layout of .rep-esp-table is missing');
  const media = css.lastIndexOf('@media(max-width:720px){', at);
  assert.ok(media !== -1 && at - media < 400, 'the card layout must sit inside the 720px media block');
  assert.match(body('table.cnt-table.rep-esp-table tr'), /display:flex/);
  assert.match(body('table.cnt-table.rep-esp-table td[data-label]::before'), /content:attr\(data-label\)/);
});

test('report figures are dark, their axis captions use --hint-ink (B6, E14)', () => {
  assert.match(body('.rep-bar-num'), /color:var\(--text\)/);
  assert.match(body('.rep-mini-axis'), /color:var\(--hint-ink/);
});

// Eleven report names never fit a row: at every width the list opens from the
// report's name in the breadcrumb as a column, so no row of links sits above
// the report or scrolls sideways with its end cut by the right edge
// (the product owner, 2026-11-08 and 2026-11-10). The breadcrumb stays, thumb-sized.
test('a report page has no row of other reports: the list is a column opened from the breadcrumb', () => {
  const nav = body('.rep-nav');
  assert.match(nav, /position:absolute/);
  assert.match(nav, /flex-direction:column/);
  assert.match(nav, /max-width:calc\(100vw - 32px\)/);
  assert.doesNotMatch(nav, /overflow-x:auto/, 'the list must not scroll sideways');
  assert.match(body('#rep-body .cnt-crumb a,.rep-switch summary'), /min-height:44px/);
});
