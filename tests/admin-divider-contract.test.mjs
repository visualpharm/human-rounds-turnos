import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
// El cascarón vivo del panel: templates/admin/base.html lleva la hoja que el
// admin.html por hash copiaba a mano (retirado con él, 2026-09-01).
const html = readFileSync(join(ROOT, 'templates/admin/base.html'), 'utf8');
const agents = readFileSync(join(ROOT, 'AGENTS.md'), 'utf8');

function rule(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return html.match(new RegExp(escaped + '\\{[^}]*\\}'))?.[0] || '';
}

test('the standing divider audit hook is persisted in project instructions', () => {
  assert.match(agents, /Divider audit hook \(MANDATORY\)/);
  assert.match(agents, /inspect every `<hr>`, separator element, `border-top`, and `border-bottom`/);
});

test('bounded admin blocks do not receive redundant divider lines', () => {
  assert.ok(!/<hr(?:\s|>)/i.test(html));
  ['.pgr', '.card-actions', '.rep-card-chart', '.cnt-actions'].forEach((selector) => {
    assert.ok(!/border-(top|bottom)\s*:/.test(rule(selector)), selector + ' has a redundant divider');
  });
});
