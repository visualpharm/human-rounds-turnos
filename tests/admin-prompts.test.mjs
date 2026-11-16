import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const src = fs.readFileSync(new URL('../static/platform/admin-prompts.mjs', import.meta.url), 'utf8');
const promptsTpl = fs.readFileSync(new URL('../templates/admin/prompts.html', import.meta.url), 'utf8');
const colaTpl = fs.readFileSync(new URL('../templates/admin/cola.html', import.meta.url), 'utf8');
const contentTpl = fs.readFileSync(new URL('../templates/admin/content.html', import.meta.url), 'utf8');

test('the prompt template name is the page title and IA instructions is the breadcrumb', () => {
  // El label puede venir directo o envuelto en T(key, vars, "Instrucciones de la IA") (i18n).
  // Crumb and name share the title band, like a doctor's card (ui-expert 15.11).
  assert.match(src, /h1\.innerHTML = '<a class="adm-crumb" href="' \+ esc\(promptHref\(""\)\) \+ '"/);
  assert.match(src, /'<\/a><span class="adm-crumb-sep">\/<\/span>' \+ esc\(it\.titulo\)/);
  assert.doesNotMatch(src, /<p class="cnt-crumb">/);
  assert.match(src, /return "\/admin\/prompts" \+ \(clave \? "\?prompt=" \+ encodeURIComponent\(clave\) : ""\);/);
  assert.doesNotMatch(src, /Instrucciones de la IA<\/a> \//);
});

test('opening an editor renders known content before requesting a translation', () => {
  assert.match(src, /function promptByKey\(clave\) \{\s*return Object\.prototype\.hasOwnProperty\.call\(ITEMS, clave\) \? ITEMS\[clave\] : null;/);
  assert.match(src, /var item = promptByKey\(clave\);\s*if \(item\) \{\s*renderEditor\(item, null\)/);
  assert.match(src, /datosLista\(\)\.then\(function \(\)/);
  assert.match(src, /Traduciendo…/);
  assert.match(src, /\/traduccion"/);
  assert.doesNotMatch(src, /setTimeout\(function \(\)[\s\S]*Traduciendo/);
});

test('one control with two tabs, one save button, and the active language first', () => {
  assert.match(src, /id="pr-es"/);
  assert.match(src, /id="pr-en"/);
  // UN solo botón de guardar, no uno por idioma.
  assert.match(src, /id="pr-guardar"/);
  assert.doesNotMatch(src, /Guardar en español|Guardar en inglés/);
  assert.match(src, /class="pr-tabs" role="tablist"/);
  assert.match(src, /data-tab="trad"/);
  assert.match(src, /data-tab="en"/);
  // La pestaña que arranca abierta es la del idioma del panel.
  assert.match(src, /class="pr-tab on" role="tab" aria-selected="true" data-tab="trad"/);
  assert.match(src, /JSON\.stringify\(payload\)/);
});

test('with the panel in English there are no tabs at all, only the original', () => {
  assert.match(src, /var soloIngles = lang === "en";/);
  assert.match(src, /var tabs = soloIngles \? "" :/);
  assert.match(src, /var panelTrad = soloIngles \? "" :/);
});

test('switching tabs keeps what was typed in the other one', () => {
  assert.match(src, /p\.hidden = p\.getAttribute\("data-panel"\) !== cual;/);
  assert.doesNotMatch(src, /innerHTML = ""[\s\S]{0,80}data-panel/);
});

test('saving while the translation is still running is refused, not sent', () => {
  assert.match(src, /if \(!es \|\| es\.readOnly\)/);
  assert.match(src, /Esperá a que termine la traducción/);
});

test('a prompt can always be restored to the original it shipped with', () => {
  assert.match(src, /id="pr-restaurar"/);
  assert.match(src, /method: "DELETE"/);
  assert.match(src, /Se restauró el original\./);
});

test('a variable reads «{hoy}: …» with the colon right after it, no chip gap', () => {
  assert.match(src, /"<code>\{" \+ esc\(v\.nombre\) \+ "\}<\/code>: " \+ esc\(v\.que_es\)/);
  const rule = src.match(/"\.pr-vars code\{([^}]*)\}"/);
  assert.ok(rule, '.pr-vars code rule');
  assert.match(rule[1], /(^|;)padding:0(;|$)/);
  assert.match(rule[1], /(^|;)border:0(;|$)/);
  assert.doesNotMatch(rule[1], /padding-(left|right)|margin/);
});

test('technical data is visible in a read-only block and all bundles are cache-busted', () => {
  assert.match(src, /<section class="pr-tech">/);
  assert.match(src, /no se edita/);
  assert.match(src, /<pre>" \+ esc\(it\.tecnico_en\) \+ "<\/pre>/);
  assert.match(promptsTpl, /SANA_STATIC\["platform\/admin-prompts\.mjs"\]/);
  assert.match(colaTpl, /static\('platform\/admin-queue\.mjs'\)/);
  assert.match(contentTpl, /static\('platform\/admin-content\.mjs'\)/);
});
