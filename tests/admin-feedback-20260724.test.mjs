import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (name) => readFileSync(join(ROOT, name), 'utf8');

test('admin collapses the sidebar at tablet width and exposes a shared description slot', () => {
  const base = read('templates/admin/base.html');
  const css = read('static/root/admin-direct.css');
  const solicitudes = read('templates/admin/solicitudes.html');
  assert.match(base, /static\('admin-direct\.css'\)/);
  assert.match(css, /@media\(max-width:720px\)\{/);
  assert.match(solicitudes, /<p class="adm-sub" id="adm-sub" style="display:none;"><\/p>/);
});

test('roadmap has a plain-language description and segmented accessible tabs', () => {
  const js = read('static/platform/admin-roadmap.mjs');
  const plan = read('templates/admin/plan.html');
  const catalog = read('locales/es/admin.system.json');
  assert.match(plan, /admin\.system\.roadmap\.subtitle/);
  assert.match(catalog, /Los próximos pasos para poner el sistema en marcha en Pinamar\./);
  assert.match(js, /class="rm-tabs"/);
  assert.match(js, /role="tablist"/);
  assert.match(js, /aria-selected=/);
});

test('user and doctor details expose quiet right-aligned deactivate actions', () => {
  const app = read('static/root/admin-app.js');
  const users = read('static/platform/admin-users.mjs');
  const content = read('static/platform/admin-content.mjs');
  assert.match(app, /window\.CURRENT_ROLE = rol;/);
  assert.match(users, /class="cnt-actions-danger"><button class="btn btn-quiet" type="button" id="usr-desactivar">.*T\("admin\.system\.users\.deactivate", null, "Desactivar"\)/);
  // "Deshabilitar" para los tres tipos (reunión hospital 2026-07-24: nada con
  // turnos históricos se elimina). Reemplaza al par "Desactivar"/"Eliminar",
  // que decía dos palabras para una misma acción. El label y el {tipo} pueden
  // venir directo o envueltos en T(key, vars, "…") (i18n).
  assert.match(content, /id="cnt-del">[\s\S]{0,120}Deshabilitar[\s\S]{0,20}<\/button>/);
  assert.match(content, /tipo: TIPOS\[tipo\]\.singular \}, "¿Deshabilitar este \{tipo\}/);
  assert.doesNotMatch(content, /\? "Desactivar" : "Eliminar"/);
});
