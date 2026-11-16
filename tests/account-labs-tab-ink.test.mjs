// The «Resultados / Evolución» tabs of a patient's lab results are a control,
// not descriptive small print: the one not selected is the opaque dark
// --hint-ink of tokens.css (defined in both themes), never the translucent
// --text-2/--text-3/--text-4 scale the humanrounds theme renders as light grey.
// The selected tab keeps --text with the accent underline (2026-11-08).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const src = fs.readFileSync(new URL('../static/platform/account-labs.mjs', import.meta.url), 'utf8')
  .replace(/["']\s*[+,]\s*\n?\s*["']/g, '');

function rules(text) {
  const out = [];
  const re = /([^{}"'`]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(text))) out.push({ sel: m[1].trim(), body: m[2] });
  return out;
}

const all = rules(src);

test('lab result tabs: the unselected tab is dark --hint-ink, the selected one keeps --text', () => {
  const own = all.filter((r) => r.sel === '.lr-tab' && /(^|;)\s*color:/.test(r.body));
  assert.ok(own.length, '.lr-tab colour not found');
  for (const r of own) assert.match(r.body, /(^|;)\s*color:var\(--hint-ink,/, '.lr-tab must use --hint-ink');
  const on = all.filter((r) => r.sel === '.lr-tab.on' && /(^|;)\s*color:/.test(r.body));
  assert.ok(on.length, '.lr-tab.on colour not found');
  for (const r of on) assert.match(r.body, /(^|;)\s*color:var\(--text\)/, '.lr-tab.on stays --text');
  const variants = all.filter((r) => r.sel.split(',').some((s) => s.trim().startsWith('.lr-tab')));
  for (const r of variants) assert.doesNotMatch(r.body, /(^|;)\s*color:var\(--text-[234]\b/, r.sel);
});

test('--hint-ink is defined in both themes', () => {
  const tokens = fs.readFileSync(new URL('../static/root/tokens.css', import.meta.url), 'utf8');
  assert.equal((tokens.match(/--hint-ink:#[0-9a-f]{6};/g) || []).length, 2);
});
