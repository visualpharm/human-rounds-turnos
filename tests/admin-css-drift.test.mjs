import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = rel => readFileSync(join(ROOT, rel), 'utf8');

test('direct screens own a linked stylesheet, not an inline legacy-shell copy', () => {
  const base = read('templates/admin/base.html');
  const css = read('static/root/admin-direct.css');
  assert.match(base, /href="\{\{ static\('admin-direct\.css'\) \}\}"/);
  assert.doesNotMatch(base, /<style[\s>]/);
  assert.match(css, /\.adm5-shell\{/);
  assert.match(css, /\.queue-/);
});

test('the direct stylesheet contains no retired root-shell selectors', () => {
  const css = read('static/root/admin-direct.css');
  for (const selector of [
    '.adm-shell', '.adm-side', '.adm-main', '.adm-menu-toggle',
    '.adm-menu-scrim', '.adm-drawer', '#app', '.adm-nav a',
  ]) assert.doesNotMatch(css, new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.equal((css.match(/\{/g) || []).length, (css.match(/\}/g) || []).length);
});

test('the direct sidebar mark is transparent and follows the effective theme logo', () => {
  const css = read('static/root/admin-direct.css');
  const mark = css.match(/\.adm5-brand::before\{[^}]+\}/)?.[0] || '';

  assert.match(mark, /width:30px;height:30px;flex:0 0 30px/,
    'the repaired mark keeps the established sidebar footprint');
  assert.match(mark, /background:transparent url\('\/logo\.png'\) center\/contain no-repeat/,
    'the existing default mark stays in place without a panel-colored tile');
  assert.doesNotMatch(mark, /var\(--panel\)/,
    'the mark itself must not recreate the white tile');
  assert.match(css, /:root\[data-theme="pinamar"\] \.adm5-brand::before\{background-image:url\('\/pinamar-emblema\.svg'\);\}/,
    'Pinamar keeps its municipal emblem override');
});
