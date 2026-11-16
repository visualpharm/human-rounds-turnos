// Every close / remove button of the product is ONE button: the Icons8 close
// glyph of the project pack as a mask (.hr-x in human-rounds.css), 44 px to
// the finger, never a typed «×» (2026-11-09). A component adds `hr-x` next to
// its own class and keeps only its position; a second copy of the mask or a
// glyph in a button's text is a regression.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = new URL('..', import.meta.url).pathname;
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, ' ');

function rule(sheet, sel) {
  const i = sheet.indexOf(sel + '{');
  assert.notEqual(i, -1, 'missing rule ' + sel);
  return sheet.slice(i + sel.length + 1, sheet.indexOf('}', i));
}

// Every button that closes or removes something, by its own class, and the
// file that renders it.
const BUTTONS = [
  ['static/platform/admin-content.mjs', 'solmod-x'],
  ['static/platform/admin-queue.mjs', 'solmod-x'],
  ['static/platform/admin-solicitudes.mjs', 'sol-det-x'],
  ['static/platform/account-requests.mjs', 'ms-cal-x'],
  ['static/platform/account-form.mjs', 'sc-os-del'],
  ['static/platform/admin-hours-editor.mjs', 'hor-x'],
  ['static/platform/doctor-workspace.mjs', 'dw-x'],
  ['templates/admin/doctor-workspace.html', 'dw-x'],
  ['static/platform/chat-widget.mjs', 'stzc-close'],
  ['static/platform/hsi-sync.mjs', 'hsi-instalar-x'],
  ['static/platform/admin-knowledge.mjs', 'kn-editor-close'],
  ['app/modules/intake/admin-consult.mjs', 'cst-chip-x'],
  ['app/patient_web/availability_pages.py', 'pf-lightbox-x'],
];

test('the shared close button: Icons8 close mask, 44 px zone, no frame', () => {
  const css = strip(read('static/root/human-rounds.css'));
  const btn = rule(css, '.hr-x');
  assert.match(btn, /min-width:44px;min-height:44px;/);
  assert.match(btn, /border:0;/);
  assert.match(btn, /background:none;/);
  assert.match(btn, /color:var\(--hint-ink/);
  assert.match(btn, /font-size:0;/);
  const icon = rule(css, '.hr-x::before');
  assert.match(icon, /-webkit-mask:url\(\/static\/icons\/close-sf-regular\.svg\) center\/contain no-repeat;/);
  assert.match(icon, /mask:url\(\/static\/icons\/close-sf-regular\.svg\) center\/contain no-repeat;/);
  assert.ok(fs.existsSync(path.join(ROOT, 'static/icons/close-sf-regular.svg')));
});

test('every close button carries .hr-x and no glyph of its own', () => {
  for (const [file, cls] of BUTTONS) {
    const src = read(file);
    const tags = src.match(new RegExp('<button[^>]*class="' + cls + '[ "][^>]*>[^<]*</button>', 'g')) || [];
    assert.ok(tags.length, file + ': no .' + cls + ' button found');
    for (const tag of tags) {
      assert.match(tag, new RegExp('class="' + cls + ' hr-x"'), file + ': ' + tag.slice(0, 80));
      assert.match(tag, /"><\/button>$/, file + ': the button has text ' + tag.slice(-40));
    }
  }
  // photo-zoom builds its buttons in DOM: the close one gets the class, no text.
  assert.match(read('static/platform/photo-zoom.mjs'), /button\(doc, "pz-close hr-x", "", /);
});

test('no typed close glyph and no second copy of the close mask', () => {
  const dirs = ['static/platform', 'templates', 'app/modules', 'app/patient_web', 'app/admin_panel'];
  const files = ['static/root/admin-app.js', 'static/root/admin-direct.css'];
  const walk = (dir) => {
    for (const ent of fs.readdirSync(path.join(ROOT, dir), { withFileTypes: true })) {
      const rel = path.join(dir, ent.name);
      if (ent.isDirectory()) walk(rel);
      else if (/\.(mjs|js|css|html|py)$/.test(ent.name)) files.push(rel);
    }
  };
  dirs.forEach(walk);
  const glyph = /(>|")(×|&times;|&#215;|\\u00d7)(<|")/;
  for (const file of files) {
    const src = read(file);
    assert.doesNotMatch(src, glyph, file + ' types a close glyph');
    assert.ok(!src.includes('close-sf-regular.svg'), file + ' copies the close mask: use .hr-x');
  }
});
