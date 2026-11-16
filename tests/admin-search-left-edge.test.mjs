// Every list's search is its magnifier and its words, on the panel's one left
// edge (the product owner: one left edge). The magnifier stands under the title's first
// letter; the frame shown on hover/focus opens OUTSIDE that edge, so nothing
// moves. One rule for every list, the queue (Pacientes de hoy) included; a
// module stylesheet that sets the search's padding, background or margin again
// would move the magnifier off the edge on its own screen.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, ' ');
const css = strip(fs.readFileSync(new URL('../static/root/admin-direct.css', import.meta.url), 'utf8'));
const queueCss = strip(fs.readFileSync(new URL('../static/platform/admin-queue.css', import.meta.url), 'utf8'));

const ROWS = '.adm5-main :is(.adm-band,.cnt-toolbar,.queue-controls,.medcal-filters,.kn-toolbar) input[type=search]';

// The body of the rule whose selector list is exactly `sel`; with `needle`, the
// one of those that holds it (the 44px row height shares this selector).
function body(sheet, sel, needle) {
  for (let i = sheet.indexOf(sel + '{'); i !== -1; i = sheet.indexOf(sel + '{', i + 1)) {
    const start = i + sel.length + 1;
    const b = sheet.slice(start, sheet.indexOf('}', start));
    if (!needle || b.includes(needle)) return b;
  }
  assert.fail('missing rule ' + sel + (needle ? ' with ' + needle : ''));
}
const px = (decl, b) => {
  const m = b.match(new RegExp(decl + ':\\s*(-?\\d+)px'));
  return m ? Number(m[1]) : null;
};

test('one rule for every list search: magnifier on the left edge, words 30px after it', () => {
  const out = Number(css.match(/--adm-search-out:\s*(\d+)px/)[1]);
  const b = body(css, ROWS, 'search-sf-regular');
  assert.match(b, /margin-left:calc\(-1 \* var\(--adm-search-out\)\)/);
  assert.match(b, /border-color:transparent/);
  const bg = b.match(/url\(\/static\/icons\/search-sf-regular\.svg\)\s+(\d+)px center\/(\d+)px/);
  assert.ok(bg, 'the magnifier is the background icon at a px position');
  const border = 1;
  // Magnifier: box left (edge - out) + border + its position = the edge.
  assert.equal(-out + border + Number(bg[1]), 0, 'the magnifier stands on the edge');
  // Words: 20px icon plus a 10px gap after it.
  assert.equal(-out + border + px('padding-left', b), 30, 'the words start 30px after the edge');
});

test('the frame shows on hover, focus and with words, without moving the magnifier', () => {
  const b = body(css, ROWS + ':is(:hover,:focus,:not(:placeholder-shown))');
  assert.match(b, /border-color:var\(--border-strong\)/);
  assert.doesNotMatch(b, /margin|padding|background-position|background:/);
});

test('the queue search keeps only its width; the look is the shared rule', () => {
  const b = body(queueCss, '.adm5-main #sec-cola .queue-controls .adm-search');
  assert.doesNotMatch(b, /padding|background|border|margin/);
  for (const rule of queueCss.matchAll(/([^{}]*(?:adm-search|queue-search)[^{}]*)\{([^}]*)\}/g)) {
    assert.doesNotMatch(rule[2], /padding-left|background|margin-left/, 'queue CSS re-sets the search look: ' + rule[1].trim());
  }
});
