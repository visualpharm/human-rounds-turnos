// tests/overlay-card-width.test.mjs — the panel's overlay card is ONE rule set
// (2026-11-09).
//
// The queue's turno card was 760 px (1200 beside a referral) with columns
// 1fr + 340 under 800 px, while the request a doctor's calendar opens was
// 860 px with 1fr + 300 under 760 px and its referral 70vh high: two copies of
// the same card drifting apart. The rules now live once, in admin-direct.css,
// and both screens read them; the calendar's card also shows the paper first
// and large, and no line when there is none, like the queue's.
// Left alone on purpose: the create-appointment form (.ct-modal, 680), the
// attendance dialog (520), the HSI install pop-up (480) and the patient
// site's calendar pop-up (540) are other jobs, sized for their content.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = new URL('..', import.meta.url).pathname;
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const DIRECT = read('static/root/admin-direct.css').replace(/\/\*[\s\S]*?\*\//g, ' ');

function rule(sel) {
  const i = DIRECT.indexOf('\n' + sel + '{');
  assert.notEqual(i, -1, 'admin-direct.css: missing top-level rule ' + sel);
  const from = i + sel.length + 2;
  return DIRECT.slice(from, DIRECT.indexOf('}', from));
}

test('the overlay card is 760 wide and widens only beside a referral', () => {
  assert.match(rule('.solmod'), /max-width:min\(760px,94vw\);/);
  assert.match(rule('.solmod:has(.solmod-body:not(.solmod-body--solo))'), /^max-width:min\(1200px,94vw\);$/);
  assert.match(rule('.solmod-body'), /grid-template-columns:minmax\(0,1fr\) 340px;/);
  assert.match(rule('.solmod-orden'), /max-height:78vh;/);
  assert.match(rule('embed.solmod-orden'), /^height:78vh;$/);
  assert.ok(DIRECT.indexOf('\n.solmod-body--solo{') > DIRECT.indexOf('\n.solmod-body{'), 'the one-column rule comes after the two-column one');
  assert.match(DIRECT, /@media\(max-width:800px\)\{\.solmod-body\{grid-template-columns:1fr;\}\}/);
});

test('no other stylesheet or module keeps its own copy of the card', () => {
  const files = ['static/root/human-rounds.css', 'static/root/admin-app.js'];
  for (const dir of ['static/platform', 'app/modules/intake', 'app/modules/turnos', 'app/modules/chat', 'templates/admin']) {
    for (const name of fs.readdirSync(path.join(ROOT, dir))) {
      if (/\.(mjs|js|css|html)$/.test(name)) files.push(dir + '/' + name);
    }
  }
  const own = /(^|[}"\s,])(\.solmod|\.solmod-back|\.solmod-body|\.solmod-orden|embed\.solmod-orden)\{/;
  const copies = files.filter((f) => own.test(read(f)));
  assert.deepEqual(copies, [], 'the .solmod rules live only in admin-direct.css');
});

test("the calendar's request card shows the paper first, and no line when there is none", () => {
  const src = read('static/platform/admin-content.mjs');
  const fn = src.slice(src.indexOf('function solicitudModalHtml('), src.indexOf('function abrirSolicitud('));
  assert.match(fn, /'<div class="solmod-body' \+ \(orden \? "" : " solmod-body--solo"\) \+ '">'/);
  assert.ok(fn.indexOf('solmod-side') < fn.indexOf('solmod-campos'), 'the referral comes before what was read from it');
  assert.doesNotMatch(fn, /request_no_order|sin orden adjunta/);
});
