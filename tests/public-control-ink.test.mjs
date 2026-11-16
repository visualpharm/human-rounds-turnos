// Controls of the public site are not descriptive small print: the listing
// column headers and sort links (listing.mjs) and the chat widget's
// thread list (chat-widget.mjs) take the opaque dark --hint-ink of tokens.css
// (defined in both themes) when not selected, never the translucent
// --text-2/--text-3/--text-4 scale the humanrounds theme renders as light grey.
// The selected state keeps its own colour (2026-11-08). Descriptive text of
// the public site stays on the translucent scale (the product owner, 2026-09-16).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

function css(file) {
  return fs.readFileSync(new URL('../static/platform/' + file, import.meta.url), 'utf8')
    .replace(/["']\s*[+,]\s*\n?\s*["']/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, ' ');
}

function rules(text) {
  const out = [];
  const re = /([^{}"'`]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(text))) out.push({ sel: m[1].trim(), body: m[2] });
  return out;
}

const COLOR = /(^|;)\s*color:/;
const HINT = /(^|;)\s*color:var\(--hint-ink,/;
const TRANSLUCENT = /(^|;)\s*color:var\(--text-[234]\b/;

function colored(all, sel) {
  return all.filter((r) => r.sel.split(',').some((s) => s.trim() === sel) && COLOR.test(r.body));
}

const listing = rules(css('listing.mjs'));
const chat = rules(css('chat-widget.mjs'));

for (const [all, sel] of [[listing, '.lst-sort'], [listing, '.lst-table th'], [chat, '.stzc-thread']]) {
  test(`${sel}: the unselected control is dark --hint-ink`, () => {
    const own = colored(all, sel);
    assert.ok(own.length, `${sel} colour not found`);
    for (const r of own) assert.match(r.body, HINT, `${sel} must use --hint-ink`);
  });
}

test('selected states keep their own colour', () => {
  for (const [sel, ink] of [['.lst-sort.on', '--accent']]) {
    const on = colored(listing, sel);
    assert.ok(on.length, `${sel} colour not found`);
    for (const r of on) assert.match(r.body, new RegExp(`(^|;)\\s*color:var\\(${ink}\\)`), sel);
  }
  const cur = colored(chat, '.stzc-thread[aria-current=true]');
  assert.ok(cur.length, '.stzc-thread[aria-current=true] colour not found');
  for (const r of cur) assert.match(r.body, /(^|;)\s*color:var\(--hover-ink\)/);
});

test('no state of these controls falls back to the translucent scale', () => {
  const ctl = /^\.(lst-sort|stzc-thread)(?![\w-])/;
  for (const r of [...listing, ...chat]) {
    if (!r.sel.split(',').some((s) => ctl.test(s.trim()))) continue;
    assert.doesNotMatch(r.body, TRANSLUCENT, r.sel);
  }
});

test('--hint-ink is defined in both themes', () => {
  const tokens = fs.readFileSync(new URL('../static/root/tokens.css', import.meta.url), 'utf8');
  assert.equal((tokens.match(/--hint-ink:#[0-9a-f]{6};/g) || []).length, 2);
});
