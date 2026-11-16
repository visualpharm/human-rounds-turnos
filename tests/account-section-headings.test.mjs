import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const html = readFileSync(new URL('../templates/account.html', import.meta.url), 'utf8');

test('every account section heading shares one size and weight', () => {
  const rule = html.match(/\.dash-col \.mt-h1,\.dash-col \.dcard-h,\.dash-col \.sc-card-h,\.dash-col \.dep h2\{([^}]+)\}/);
  assert.ok(rule, 'one shared heading rule for Mis turnos, cards, forms and dependents');
  assert.match(rule[1], /font-size:24px/);
  assert.match(rule[1], /font-weight:700/);
});
