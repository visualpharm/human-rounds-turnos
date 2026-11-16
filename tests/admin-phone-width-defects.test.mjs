import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const QUEUE_CSS = readFileSync(join(ROOT, 'static/platform/admin-queue.css'), 'utf8');
const DIRECT_CSS = readFileSync(join(ROOT, 'static', 'root', 'admin-direct.css'), 'utf8');
const SETTINGS_JS = readFileSync(join(ROOT, 'static/platform/admin-settings.mjs'), 'utf8');

// One left edge (UI review 2026-11-03): the section already sits on the panel gutter, so a
// stacked card adds no inset of its own — two stacked 16px paddings pushed the doctor cards
// and the day's rows 32px right of the search, the date and the «Médicos» label.
// 2026-11-08: the doctor cards gave way to one flat list; the doctor the list
// is narrowed to is named in a line on the same gutter as the search.
test('390px phone: the chosen doctor line starts on the page edge, no inset of its own', () => {
  assert.doesNotMatch(QUEUE_CSS, /queue-doctors-table/);
  assert.match(QUEUE_CSS, /\.queue-doctor-head\{display:flex;[^}]*margin:0 0 12px;\}/);
  assert.doesNotMatch(QUEUE_CSS, /\.queue-doctor-head\{[^}]*padding/);
  assert.match(DIRECT_CSS, /#sec-cola \.queue-controls,#sec-cola \.queue-doctor-head,#sec-cola>\.adm-empty\{padding-left:var\(--adm-gutter\);padding-right:var\(--adm-gutter\);\}/);
});

test('640px phone: the day\'s rows start on the page edge, no inset of their own', () => {
  assert.match(QUEUE_CSS, /@media\(max-width:640px\)/, 'has 640px media query');
  assert.match(QUEUE_CSS, /\.queue-table tr\{border-bottom:1px solid var\(--border\);padding:10px 0;\}/);
  assert.match(QUEUE_CSS, /\.queue-table td\{border-bottom:0;padding:2px 0;white-space:normal;\}/);
});

test('390px phone: users table converts to block layout without clipping Email column', () => {
  // The users table should have responsive CSS to convert to block layout at 640px
  assert.match(DIRECT_CSS, /#sec-usuarios \.cnt-table-wrap\{overflow:visible;contain:none;\}/,
    'users table wrap should have overflow:visible on phone');
  assert.match(DIRECT_CSS, /#sec-usuarios \.cnt-table thead\{display:none;\}/,
    'users table thead should be hidden on phone');
  assert.match(DIRECT_CSS, /#sec-usuarios \.cnt-table tr\{border-bottom:1px solid var\(--border\);padding:10px 0;\}/,
    'users table rows add no inset of their own: the section already sits on the gutter');
  assert.match(DIRECT_CSS, /#sec-usuarios \.cnt-table td\{border:0;padding:3px 0 3px 0;/,
    'users table cells should not clip on phone');
});

test('390px phone: settings Ctrl/Cmd+click instruction is hidden', () => {
  // The settings module should have the class set-chain-hint on the instruction
  assert.match(SETTINGS_JS, /class="set-lede set-chain-hint"/,
    'settings chain hint paragraph should have set-chain-hint class');

  // The CSS injection should include a rule to hide it on pointer:coarse
  assert.match(SETTINGS_JS, /\.set-chain-hint\{display:none;\}/,
    'settings should hide chain hint on pointer:coarse (touch devices)');
});
