// Guardas de fuente para #legal (admin-legal.js): los términos y la política
// de privacidad se editan desde el panel. Mismo patrón que
// admin-prompts.test.mjs — node --test sobre el texto de los archivos, sin
// levantar navegador — para clavar el cableado que
// admin-section-wiring-guard.py exige (div, dispatch, OTRAS de cada hermano,
// nav item en applyRol).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const jsPath = join(ROOT, 'static', 'platform', 'admin-legal.mjs');
const js = readFileSync(jsPath, 'utf8');
const template = readFileSync(join(ROOT, 'templates', 'admin', 'legal.html'), 'utf8');

test('admin legal native module exists and is loaded by its direct template', () => {
  assert.ok(existsSync(jsPath), 'admin-legal.mjs debe existir');
  assert.match(template, /SANA_STATIC\["platform\/admin-legal\.mjs"\]/);
});

test('direct template owns the legal container', () => {
  assert.match(template, /<div id="sec-legal"><\/div>/);
});

test('legal is direct-only with query/history state', () => {
  assert.match(js, /new URLSearchParams\(location\.search\)\.get\("doc"\)/);
  assert.match(js, /window\.addEventListener\("popstate", onPopstate\)/);
  assert.doesNotMatch(js, /SanaAdminRouter|SanaAdminSections|location\.hash|hashchange/);
});

test('reads, saves and restores through /api/admin/legal', () => {
  assert.match(js, /api\("\/api\/admin\/legal"\)/);
  assert.match(js, /method: "PUT"/);
  assert.match(js, /JSON\.stringify\(\{ cuerpo: ta\.value \}\)/);
  assert.match(js, /method: "DELETE"/);
});

test('restoring the original asks for confirmation first', () => {
  assert.match(js, /window\.confirm\(/);
  assert.match(js, /vuelve el texto original/);
});

test('save and restore copy the controls of «Instrucciones de la IA»', () => {
  // Un control nuevo copia el que ya hace ese trabajo (responsable del producto, 2026-08-01):
  // el mismo botón de acento para guardar y el mismo enlace para restaurar.
  // El label puede venir directo o envuelto en T(key, vars, "Guardar") (i18n).
  const prompts = readFileSync(join(ROOT, 'static', 'platform', 'admin-prompts.mjs'), 'utf8');
  assert.match(prompts, /class="btn btn-accent" id="pr-guardar">[\s\S]{0,120}Guardar/);
  assert.match(js, /class="btn btn-accent" id="lg-guardar">[\s\S]{0,120}Guardar/);
  assert.match(prompts, /id="pr-restaurar">[\s\S]{0,120}Restaurar el original/);
  assert.match(js, /id="lg-restaurar">[\s\S]{0,120}Restaurar el original/);
});

test('the editable body is escaped before it is written into the DOM', () => {
  assert.match(js, /esc\(it\.cuerpo\)/);
  assert.match(js, /function esc\(s\)/);
});

test('the writing help names the installation placeholder', () => {
  assert.match(js, /\{\{sitio\.brand\}\}/);
});

test('the document cards fit a 320px phone: the grid minimum never exceeds the column (09.11)', () => {
  // minmax(320px,1fr) pushed both cards 16px past a 320px screen (288px of content).
  assert.match(js, /\.lg-cards\{[^}]*grid-template-columns:repeat\(auto-fill,minmax\(min\(320px,100%\),1fr\)\)/);
});
