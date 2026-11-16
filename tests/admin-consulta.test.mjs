// Structural retirement guards only. Behavior is exercised by the complete
// native module in admin-consult-lifecycle/browser.test.mjs and managed routes.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';

const at = path => new URL('../' + path, import.meta.url);
const source = readFileSync(at('app/modules/intake/admin-consult.mjs'), 'utf8');
const template = readFileSync(at('app/modules/intake/admin_consult.html'), 'utf8');
const manifest = JSON.parse(readFileSync(at('app/modules/intake/module.json'), 'utf8'));

test('classic consultation producer is physically absent without a compatibility facade', () => {
  assert.equal(existsSync(at('admin-consulta.js')), false);
  assert.doesNotMatch(source, /SanaAdminRouter|SanaAdminSections|window\.Sana|module\.exports|location\.hash|hashchange/);
  assert.match(source, /export function mount\(host, options\)/);
});

test('intake owns exactly one fingerprinted consultation asset and direct screen', () => {
  // a manifest names its assets inside the code package, as their URLs do
  assert.deepEqual(manifest.assets.filter(asset => asset.path === 'modules/intake/admin-consult.mjs'),
    [{ path: 'modules/intake/admin-consult.mjs', fingerprint: true }]);
  const sections = manifest.admin_sections.filter(section => section.id === 'consulta');
  assert.equal(sections.length, 1);
  assert.equal(sections[0].screen.path, '/admin/consult');
  assert.equal(sections[0].screen.module, 'app.modules.intake.admin_consult_screen');
});

test('direct consultation template owns one host and uses no classic script', () => {
  assert.equal((template.match(/id="sec-consulta"/g) || []).length, 1);
  assert.equal((template.match(/module_static\('modules\/intake\/admin-consult\.mjs'\)/g) || []).length, 1);
  assert.doesNotMatch(template, /admin-consulta\.js/);
  assert.match(template, /mount: \(Consultation\) => Consultation\.mount\(host\)/);
  assert.match(template, /bootScreen\(\{/);
});
