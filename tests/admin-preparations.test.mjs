// Architecture contracts only. Former classic-source behavior assertions now
// run the native module and real browser (routing, readiness, labels, actions,
// empty creation) in the admin-preparations-lifecycle suites.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { assertSharedShellContract } from './shared-shell-contract.mjs';

const root = new URL('../', import.meta.url);
const template = readFileSync(new URL('templates/admin/preparations.html',root),'utf8');

test('preparations template loads the native entry through the shared shell', () => {
  assertSharedShellContract(assert,template,['platform/admin-preparations.mjs']);
  assert.equal(template.includes("static('admin-preparations.js')"),false);
  assert.equal(template.includes("static('prep.js')"),false);
});

test('the native controller exports an explicit mount without keeping a classic implementation', async () => {
  assert.equal(existsSync(new URL('admin-preparations.js',root)),false);
  const module = await import('../static/platform/admin-preparations.mjs');
  assert.equal(typeof module.mount,'function');
});
