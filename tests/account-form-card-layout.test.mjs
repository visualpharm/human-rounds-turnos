// Datos personales / Cobertura cards of the patient account (review 2026-11-15,
// item 16): no "Todo guardado" next to "Guardar cambios" before a save, DNI and
// birth date in the body font, one label layout, the postal code by content,
// the x right after its own field, nothing drawn by hand.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const src = fs.readFileSync(new URL('../static/platform/account-form.mjs', import.meta.url), 'utf8');

test('the editor says nothing about saving on load', () => {
  const body = src.slice(src.indexOf('function sayGuardado('));
  const onLoad = body.slice(body.indexOf('}\n'), body.indexOf('function payload('));
  assert.doesNotMatch(onLoad, /sayGuardado\(/, 'no status line before the first save');
});

test('account cards: DNI and date in the body font, labels above, CP by content', () => {
  assert.match(src, /\.sc-card \.sc-dni,\.sc-card \.sc-static\{font:600 18px\/1\.3 var\(--font\);letter-spacing:0;/);
  assert.match(src, /\.sc-card \.sc-dob\{font-variant-numeric:normal;letter-spacing:0;\}/);
  assert.match(src, /\.sc-card \.sc-row\{grid-template-columns:minmax\(0,1fr\);/);
  assert.match(src, /\.sc-card \.sc-row>label,\.sc-card \.sc-row>\.sc-label,\.sc-card \.sc-field2>label,\.sc-card \.sc-field2>\.sc-label\{text-align:left;padding-top:0;font-size:15px;font-weight:500;color:var\(--text\);\}/);
  assert.match(src, /\.sc-field2 \.sc-cp\{width:8ch;\}/);
});

test('the coverage x follows its field, before the list arrow', () => {
  const combo = src.slice(src.indexOf('function comboHtml('), src.indexOf('function comboHtml(') + 1200);
  assert.ok(combo.indexOf('trailingControl ||') < combo.indexOf('sc-combo-btn'), 'x before the arrow');
});

test('no hand-drawn svg icons in the account form', () => {
  assert.doesNotMatch(src, /<svg width=/);
  assert.match(src, /scIco\("visible-sf-regular\.svg"/);
});
